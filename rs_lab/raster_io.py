"""GDAL helpers: read/write rasters, band roles, block iteration, rasterizing training data,
plus QGIS styling helpers (main thread only)."""
import os
import tempfile

import numpy as np
from osgeo import gdal, ogr, osr

from . import catalog
import logging

LOG = logging.getLogger(__name__)

gdal.UseExceptions()

DEFAULT_CLASSES = [
    (1, "Water", "#2b83ba"), (2, "Built-up", "#d7191c"), (3, "Rice / cropland", "#f2e35b"),
    (4, "Forest", "#1a7f3c"), (5, "Shrub / grassland", "#9bd06b"), (6, "Bare soil", "#c2a079"),
]

PALETTE = ["#2b83ba", "#d7191c", "#f2e35b", "#1a7f3c", "#9bd06b", "#c2a079", "#984ea3", "#ff7f00",
           "#a6cee3", "#fb9a99", "#b15928", "#6a3d9a", "#33a02c", "#e31a1c", "#1f78b4", "#ffff99",
           "#8dd3c7", "#bebada", "#80b1d3", "#fdb462"]


class RasterInfo:
    def __init__(self, path):
        ds = gdal.Open(path)
        self.path = path
        self.count = ds.RasterCount
        self.xsize, self.ysize = ds.RasterXSize, ds.RasterYSize
        self.gt = ds.GetGeoTransform()
        self.wkt = ds.GetProjection()
        self.meta = ds.GetMetadata() or {}
        self.descs, self.roles, self.nodata = [], {}, []
        for i in range(1, self.count + 1):
            b = ds.GetRasterBand(i)
            d = b.GetDescription() or "Band %d" % i
            self.descs.append(d)
            self.nodata.append(b.GetNoDataValue())
            r = catalog.role_from_desc(d)
            if r and r not in self.roles:
                self.roles[r] = i
        ds = None

    @property
    def pixel_area(self):
        a = abs(self.gt[1] * self.gt[5])
        srs = osr.SpatialReference(wkt=self.wkt) if self.wkt else None
        if srs is not None and srs.IsGeographic():
            # rough conversion of degrees^2 to m^2 at the image centre latitude
            lat = self.gt[3] + self.gt[5] * self.ysize / 2
            a = a * (111320.0 ** 2) * np.cos(np.radians(lat))
        return a

    def label(self, i):
        return "%d: %s" % (i, self.descs[i - 1])


def read_band(path, i, window=None):
    ds = gdal.Open(path)
    b = ds.GetRasterBand(i)
    if window:
        x, y, w, h = window
        a = b.ReadAsArray(x, y, w, h).astype("float64")
    else:
        a = b.ReadAsArray().astype("float64")
    nd = b.GetNoDataValue()
    if nd is not None:
        a[a == nd] = np.nan
    return a


def read_stack(path, idxs, window=None):
    return np.stack([read_band(path, i, window) for i in idxs])


def iter_blocks(info, rows=512):
    for y in range(0, info.ysize, rows):
        yield (0, y, info.xsize, min(rows, info.ysize - y))


def sample_pixels(path, idxs, n=50000, seed=0):
    """Random sample of valid pixels (n, b) without reading everything at once."""
    info = RasterInfo(path)
    rng = np.random.default_rng(seed)
    per = max(1, n // max(1, int(np.ceil(info.ysize / 512))))
    out = []
    for win in iter_blocks(info):
        a = read_stack(path, idxs, win).reshape(len(idxs), -1).T
        a = a[np.isfinite(a).all(1)]
        if a.shape[0]:
            out.append(a[rng.choice(a.shape[0], min(per, a.shape[0]), replace=False)])
    return np.vstack(out) if out else np.empty((0, len(idxs)))


def create_like(path, info, nbands, dtype=gdal.GDT_Float32, nodata=None, descs=None):
    drv = gdal.GetDriverByName("GTiff")
    co = ["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"]
    ds = drv.Create(path, info.xsize, info.ysize, nbands, dtype, co)
    ds.SetGeoTransform(info.gt)
    ds.SetProjection(info.wkt)
    for i in range(nbands):
        b = ds.GetRasterBand(i + 1)
        if nodata is not None:
            b.SetNoDataValue(nodata)
        if descs and i < len(descs):
            b.SetDescription(descs[i])
    return ds


def write_array(path, arr, info, dtype=gdal.GDT_Float32, nodata=None, descs=None, colors=None):
    arr = np.asarray(arr)
    if arr.ndim == 2:
        arr = arr[None]
    ds = create_like(path, info, arr.shape[0], dtype, nodata, descs)
    for i in range(arr.shape[0]):
        a = arr[i]
        if nodata is not None and np.issubdtype(a.dtype, np.floating):
            a = np.where(np.isfinite(a), a, nodata)
        ds.GetRasterBand(i + 1).WriteArray(a)
    if colors:
        ct = gdal.ColorTable()
        for v, hexc in colors.items():
            h = hexc.lstrip("#")
            ct.SetColorEntry(int(v), (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255))
        ds.GetRasterBand(1).SetRasterColorTable(ct)
    ds.FlushCache()
    ds = None
    return path


def rasterize_layer(vector_path, layer_name, field, info, all_touched=False):
    """Burn a vector field (class ids) onto the raster grid. Returns int32 array (0 = none)."""
    drv = gdal.GetDriverByName("MEM")
    ds = drv.Create("", info.xsize, info.ysize, 1, gdal.GDT_Int32)
    ds.SetGeoTransform(info.gt)
    ds.SetProjection(info.wkt)
    src = ogr.Open(vector_path)
    lyr = src.GetLayerByName(layer_name) if layer_name else src.GetLayer(0)
    opts = ["ATTRIBUTE=%s" % field]
    if all_touched:
        opts.append("ALL_TOUCHED=TRUE")
    gdal.RasterizeLayer(ds, [1], lyr, options=opts)
    a = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    return a


def export_vector_for_raster(qgs_layer, info, selected_only=False):
    """Write a QGIS vector layer to a temp GeoPackage reprojected to the raster CRS."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransformContext,
                           QgsProject, QgsVectorFileWriter)
    tmp = os.path.join(tempfile.mkdtemp(prefix="rslab_v_"), "train.gpkg")
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = "train"
    opts.onlySelectedFeatures = selected_only
    crs = QgsCoordinateReferenceSystem()
    crs.createFromWkt(info.wkt)
    from qgis.core import QgsCoordinateTransform
    opts.ct = QgsCoordinateTransform(qgs_layer.crs(), crs, QgsProject.instance())
    ctx = QgsProject.instance().transformContext() if QgsProject.instance() else QgsCoordinateTransformContext()
    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3"):
        res = QgsVectorFileWriter.writeAsVectorFormatV3(qgs_layer, tmp, ctx, opts)
    else:
        res = QgsVectorFileWriter.writeAsVectorFormatV2(qgs_layer, tmp, ctx, opts)
    if res[0] != QgsVectorFileWriter.NoError:
        raise IOError("Could not export layer: %s" % (res[1] if len(res) > 1 else res))
    return tmp, "train"


def make_vrt_composite(path, bands, out_vrt):
    gdal.BuildVRT(out_vrt, [path], bandList=list(bands))
    return out_vrt


# =================================================================== QGIS styling (main thread)
def add_rgb_layer(path, name, rgb=(1, 2, 3), stretch="clip", group=None):
    from qgis.core import (QgsContrastEnhancement, QgsMultiBandColorRenderer, QgsRasterLayer,
                           QgsRasterMinMaxOrigin)
    lyr = QgsRasterLayer(path, name)
    if not lyr.isValid():
        return None
    r = QgsMultiBandColorRenderer(lyr.dataProvider(), *rgb)
    lyr.setRenderer(r)
    limits = {"clip": QgsRasterMinMaxOrigin.CumulativeCut, "minmax": QgsRasterMinMaxOrigin.MinMax,
              "std": QgsRasterMinMaxOrigin.StdDev}.get(stretch, QgsRasterMinMaxOrigin.CumulativeCut)
    lyr.setContrastEnhancement(QgsContrastEnhancement.StretchToMinimumMaximum, limits)
    _add(lyr, group)
    return lyr


def safe_range(vmin, vmax):
    """Finite, non-empty [vmin, vmax]. A zero or non-finite range (e.g. a mask where every pixel is 1, or an
    all-NaN band) makes QGIS compute an absurd label precision, which can crash Qt — so widen it here."""
    import math
    try:
        vmin, vmax = float(vmin), float(vmax)
    except (TypeError, ValueError):
        return 0.0, 1.0
    if not math.isfinite(vmin) or abs(vmin) > 1e30:
        vmin = 0.0
    if not math.isfinite(vmax) or abs(vmax) > 1e30:
        vmax = vmin + 1.0
    if vmax < vmin:
        vmin, vmax = vmax, vmin
    span = vmax - vmin
    if span <= 1e-9 * max(1.0, abs(vmin), abs(vmax)):
        vmin, vmax = vmin - 0.5, vmax + 0.5
    return vmin, vmax


def _label(v, span):
    """Short legend label with a sensible number of decimals for the range."""
    import math
    dec = 0 if span >= 50 else max(0, min(4, int(math.ceil(-math.log10(span / 10.0)))))
    return ("%.*f" % (dec, v)).rstrip("0").rstrip(".") if dec else "%.0f" % v


def add_single_layer(path, name, ramp="RdYlGn", vmin=None, vmax=None, group=None, band=1, classes=9):
    """Pseudocolour layer. The colour-ramp items and their labels are built here (not by
    QgsColorRampShader.classifyColorRamp), with a guarded min/max."""
    from qgis.core import (QgsColorRampShader, QgsRasterLayer, QgsRasterShader,
                           QgsSingleBandPseudoColorRenderer, QgsStyle)
    lyr = QgsRasterLayer(path, name)
    if not lyr.isValid():
        return None
    if vmin is None or vmax is None:
        try:
            st = lyr.dataProvider().bandStatistics(band)
            vmin = st.minimumValue if vmin is None else vmin
            vmax = st.maximumValue if vmax is None else vmax
        except Exception:  # noqa: BLE001
            vmin, vmax = 0, 1
    vmin, vmax = safe_range(vmin, vmax)
    style = QgsStyle().defaultStyle()
    rev = isinstance(ramp, str) and ramp.endswith("_r")          # "Spectral_r" = reversed ramp
    rp = style.colorRamp(ramp[:-2] if rev else ramp) or style.colorRamp("Spectral")
    if rev:
        try:
            rp.invert()
        except Exception:  # noqa: BLE001
            LOG.debug("ignored error", exc_info=True)
    span = vmax - vmin
    n = max(2, int(classes))
    items = []
    for i in range(n):
        t = i / float(n - 1)
        v = vmin + t * span
        items.append(QgsColorRampShader.ColorRampItem(v, rp.color(t), _label(v, span)))
    fn = QgsColorRampShader(vmin, vmax)
    try:                                   # QGIS ≥ 3.30 / Qt6
        from qgis.core import Qgis
        fn.setColorRampType(Qgis.ShaderInterpolationMethod.Linear)
    except Exception:  # noqa: BLE001
        fn.setColorRampType(QgsColorRampShader.Interpolated)
    try:
        fn.setSourceColorRamp(rp.clone())
    except Exception:  # noqa: BLE001
        LOG.debug("ignored error", exc_info=True)
    fn.setColorRampItemList(items)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(fn)
    ren = QgsSingleBandPseudoColorRenderer(lyr.dataProvider(), band, shader)
    ren.setClassificationMin(vmin)
    ren.setClassificationMax(vmax)
    lyr.setRenderer(ren)
    _add(lyr, group)
    return lyr


def set_rgb_minmax(lyr, vmin, vmax):
    """Explicit stretch (same min/max on R, G and B), like GEE vis params."""
    from qgis.core import QgsContrastEnhancement
    vmin, vmax = safe_range(vmin, vmax)
    ren = lyr.renderer()
    prov = lyr.dataProvider()
    for i, setter in enumerate(("setRedContrastEnhancement", "setGreenContrastEnhancement", "setBlueContrastEnhancement")):
        band = (ren.redBand(), ren.greenBand(), ren.blueBand())[i]
        ce = QgsContrastEnhancement(prov.dataType(band))
        ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum)
        ce.setMinimumValue(vmin)
        ce.setMaximumValue(vmax)
        getattr(ren, setter)(ce)
    lyr.triggerRepaint()


def add_gray_layer(path, name, group=None, stretch=True):
    from qgis.core import (QgsContrastEnhancement, QgsRasterLayer, QgsRasterMinMaxOrigin,
                           QgsSingleBandGrayRenderer)
    lyr = QgsRasterLayer(path, name)
    if not lyr.isValid():
        return None
    lyr.setRenderer(QgsSingleBandGrayRenderer(lyr.dataProvider(), 1))
    if stretch:
        lyr.setContrastEnhancement(QgsContrastEnhancement.StretchToMinimumMaximum,
                                   QgsRasterMinMaxOrigin.CumulativeCut)
    _add(lyr, group)
    return lyr


# ------------------------------------------------------------------ translatable legend labels
# A class entry is (value, key, '#hex') or (value, key, '#hex', [args]).  `key` is the ENGLISH
# label (or a %-template such as "Cluster %s" / "%s → %s"); args are themselves English keys.
# The entries are stored on the layer so a language switch can relabel the legend.
CLASS_PROP = "rslab_classes"


def class_label(key, args=None):
    from .i18n import tr
    if args:
        try:
            return tr(key) % tuple(tr(str(a)) for a in args)
        except Exception:  # noqa: BLE001
            return key
    return tr(str(key))


def _norm(classes):
    out = []
    for c in classes:
        v, key, col = c[0], c[1], c[2]
        args = list(c[3]) if len(c) > 3 and c[3] else []
        out.append([int(v), str(key), str(col), [str(a) for a in args]])
    return out


def _paletted(lyr, entries):
    from qgis.core import QgsPalettedRasterRenderer
    from qgis.PyQt.QtGui import QColor
    cl = [QgsPalettedRasterRenderer.Class(v, QColor(col), class_label(key, args)) for v, key, col, args in entries]
    lyr.setRenderer(QgsPalettedRasterRenderer(lyr.dataProvider(), 1, cl))


def add_class_layer(path, name, classes, group=None):
    """classes: list of (value, english_key, '#hex'[, args]) — labels are shown in the current language."""
    import json
    from qgis.core import QgsRasterLayer
    lyr = QgsRasterLayer(path, name)
    if not lyr.isValid():
        return None
    entries = _norm(classes)
    _paletted(lyr, entries)
    lyr.setCustomProperty(CLASS_PROP, json.dumps(entries, ensure_ascii=False))
    _add(lyr, group)
    return lyr


def set_vector_classes(vl, classes, field):
    """Categorized style for a vector layer, with translatable labels ('<id> <name>')."""
    import json
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol
    from qgis.PyQt.QtGui import QColor
    entries = _norm(classes)
    cats = []
    for v, key, col, args in entries:
        sym = QgsSymbol.defaultSymbol(vl.geometryType())
        sym.setColor(QColor(col))
        sym.setOpacity(0.6)
        cats.append(QgsRendererCategory(v, sym, "%d %s" % (v, class_label(key, args))))
    vl.setRenderer(QgsCategorizedSymbolRenderer(field, cats))
    vl.setCustomProperty(CLASS_PROP, json.dumps(entries, ensure_ascii=False))


def relabel_layer(lyr):
    """Re-apply legend labels in the current language. Returns True if the layer changed."""
    import json
    raw = lyr.customProperty(CLASS_PROP)
    if not raw:
        return False
    try:
        entries = json.loads(raw)
    except Exception:  # noqa: BLE001
        return False
    from qgis.core import QgsRasterLayer, QgsVectorLayer
    if isinstance(lyr, QgsRasterLayer):
        _paletted(lyr, entries)
    elif isinstance(lyr, QgsVectorLayer):
        ren = lyr.renderer()
        if hasattr(ren, "categories"):
            labels = {v: "%d %s" % (v, class_label(k, a)) for v, k, _, a in entries}
            for i, cat in enumerate(ren.categories()):
                try:
                    v = int(cat.value())
                except Exception:  # noqa: BLE001
                    LOG.debug("ignored error", exc_info=True)
                    continue
                if v in labels:
                    ren.updateCategoryLabel(i, labels[v])
    lyr.triggerRepaint()
    return True


def _add(lyr, group=None):
    from qgis.core import QgsProject
    proj = QgsProject.instance()
    if group:
        root = proj.layerTreeRoot()
        g = root.findGroup(group) or root.insertGroup(0, group)
        proj.addMapLayer(lyr, False)
        g.insertLayer(0, lyr)
    else:
        proj.addMapLayer(lyr)


def raster_path(layer):
    src = layer.source()
    return src.split("|")[0]
