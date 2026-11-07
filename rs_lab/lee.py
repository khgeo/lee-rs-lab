"""LEE — Local Earth Engine.

A small Google-Earth-Engine-style API that runs on YOUR computer. Data are read from free STAC
catalogues (Microsoft Planetary Computer, Earth Search) — no account, no login — clipped to the AOI
and computed with NumPy. Scripts look like GEE scripts:

    s2 = (lee.ImageCollection("sentinel-2")
            .filterDate("2025-01-01", "2025-03-31")
            .filterBounds(aoi)                 # aoi = the AOI chosen in tab 1
            .filterMetadata("eo:cloud_cover", "lt", 20))
    img = s2.median()                          # cloud-masked median composite
    ndvi = img.normalizedDifference("nir", "red").rename("NDVI")
    Map.addLayer(ndvi, {"min": 0, "max": 0.9, "palette": "RdYlGn"}, "NDVI")
    print(ndvi.reduceRegion("mean"))

Differences from GEE: computation is eager (when a composite / Image is made), the grid is the AOI
bounding box in its UTM zone at `lee.config(scale=…)` metres, and reflectance is already scaled (0–1).
"""
import math
import os
import shutil
import tempfile
import time
import warnings

import logging
import numpy as np
from osgeo import gdal, osr

from . import catalog
from . import rs_core as R
from . import stac_engine as se

gdal.UseExceptions()

LOG = logging.getLogger(__name__)


# ====================================================================== datasets (all free, no login)
S2_ROLES = {b: r for b, r, _, _ in catalog.SENSORS["S2_SR"]["bands"]}
L89_ROLES = {b: r for b, r, _, _ in catalog.SENSORS["L8_L2"]["bands"]}
L57_ROLES = {b: r for b, r, _, _ in catalog.SENSORS["L5_L2"]["bands"]}

DATASETS = {
    # ---------------- image time series
    "sentinel-2": dict(title="Sentinel-2 L2A surface reflectance (10–60 m, 2015–)", source="planetary_computer",
                       coll="sentinel-2-l2a", assets=se.S2_PC, roles=S2_ROLES, mask=("SCL", "s2"), res=10,
                       scale=1e-4, offset=0.0, s2=True, cloud="eo:cloud_cover",
                       default=["B2", "B3", "B4", "B8", "B11", "B12"]),
    "sentinel-2@aws": dict(title="Sentinel-2 L2A — Earth Search (AWS)", source="earth_search", coll="sentinel-2-l2a",
                           assets=se.S2_ES, roles=S2_ROLES, mask=("scl", "s2"), res=10, scale=1e-4, offset=0.0,
                           s2=True, cloud="eo:cloud_cover", default=["B2", "B3", "B4", "B8", "B11", "B12"]),
    "landsat-9": dict(title="Landsat 9 C2 L2 (30 m, 2021–)", source="planetary_computer", coll="landsat-c2-l2",
                      platform="landsat-9", assets=se.L89_PC, roles=L89_ROLES, mask=("qa_pixel", "landsat"), res=30,
                      scale=2.75e-5, offset=-0.2, cloud="eo:cloud_cover",
                      default=["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"]),
    "landsat-8": dict(title="Landsat 8 C2 L2 (30 m, 2013–)", source="planetary_computer", coll="landsat-c2-l2",
                      platform="landsat-8", assets=se.L89_PC, roles=L89_ROLES, mask=("qa_pixel", "landsat"), res=30,
                      scale=2.75e-5, offset=-0.2, cloud="eo:cloud_cover",
                      default=["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"]),
    "landsat-7": dict(title="Landsat 7 C2 L2 (30 m, 1999–2022)", source="planetary_computer", coll="landsat-c2-l2",
                      platform="landsat-7", assets=se.L57_PC, roles=L57_ROLES, mask=("qa_pixel", "landsat"), res=30,
                      scale=2.75e-5, offset=-0.2, cloud="eo:cloud_cover",
                      default=["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7"]),
    "landsat-5": dict(title="Landsat 5 C2 L2 (30 m, 1984–2012)", source="planetary_computer", coll="landsat-c2-l2",
                      platform="landsat-5", assets=se.L57_PC, roles=L57_ROLES, mask=("qa_pixel", "landsat"), res=30,
                      scale=2.75e-5, offset=-0.2, cloud="eo:cloud_cover",
                      default=["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7"]),
    "sentinel-1": dict(title="Sentinel-1 RTC backscatter, dB (10 m, 2014–)", source="planetary_computer",
                       coll="sentinel-1-rtc", assets={"VV": "vv", "VH": "vh"}, roles={"VV": "vv", "VH": "vh"},
                       mask=None, res=10, sar=True, default=["VV", "VH"]),
    "modis-ndvi": dict(title="MODIS Terra NDVI/EVI 16-day (250 m, 2000–)", source="planetary_computer",
                       coll="modis-13Q1-061", assets={"NDVI": "250m_16_days_NDVI", "EVI": "250m_16_days_EVI"},
                       roles={"NDVI": "ndvi", "EVI": "evi"}, mask=None, res=250, scale=1e-4, offset=0.0,
                       nodata=-3000, default=["NDVI", "EVI"], resample="bilinear"),
    # ---------------- single layers (mosaicked over the AOI)
    "dem": dict(title="Copernicus DEM GLO-30 elevation (30 m)", source="planetary_computer", coll="cop-dem-glo-30",
                assets={"elevation": "data"}, roles={"elevation": "elevation"}, mask=None, res=30, static=True,
                default=["elevation"], resample="bilinear", nodata=None),
    "nasadem": dict(title="NASADEM elevation (30 m)", source="planetary_computer", coll="nasadem",
                    assets={"elevation": "elevation"}, roles={"elevation": "elevation"}, mask=None, res=30,
                    static=True, default=["elevation"], resample="bilinear", nodata=-32768),
    "worldcover": dict(title="ESA WorldCover land cover 2020/2021 (10 m)", source="planetary_computer",
                       coll="esa-worldcover", assets={"landcover": "map"}, roles={"landcover": "landcover"},
                       mask=None, res=10, static=True, categorical=True, default=["landcover"], nodata=0),
    "lulc-annual": dict(title="Impact Observatory / Esri annual land cover 2017– (10 m)", source="planetary_computer",
                        coll="io-lulc-annual-v02", assets={"landcover": "data"}, roles={"landcover": "landcover"},
                        mask=None, res=10, static=True, categorical=True, default=["landcover"], nodata=0),
    "surface-water": dict(title="JRC Global Surface Water 1984–2021 (30 m)", source="planetary_computer",
                          coll="jrc-gsw", assets={"occurrence": "occurrence", "seasonality": "seasonality",
                                                  "recurrence": "recurrence", "change": "change", "extent": "extent"},
                          roles={}, mask=None, res=30, static=True, default=["occurrence"], nodata=255),
}
ALIASES = {"sentinel-2-l2a": "sentinel-2", "COPERNICUS/S2_SR_HARMONIZED": "sentinel-2", "s2": "sentinel-2",
           "LANDSAT/LC09/C02/T1_L2": "landsat-9", "LANDSAT/LC08/C02/T1_L2": "landsat-8",
           "LANDSAT/LE07/C02/T1_L2": "landsat-7", "LANDSAT/LT05/C02/T1_L2": "landsat-5",
           "COPERNICUS/S1_GRD": "sentinel-1", "s1": "sentinel-1", "cop-dem-glo-30": "dem",
           "esa-worldcover": "worldcover", "ESA/WorldCover/v200": "worldcover", "io-lulc-annual-v02": "lulc-annual",
           "jrc-gsw": "surface-water", "JRC/GSW1_4/GlobalSurfaceWater": "surface-water",
           "modis-13Q1-061": "modis-ndvi", "MODIS/061/MOD13Q1": "modis-ndvi"}

LEGENDS = {
    "worldcover": [(10, "Tree cover", "#006400"), (20, "Shrubland", "#ffbb22"), (30, "Grassland", "#ffff4c"),
                   (40, "Cropland", "#f096ff"), (50, "Built-up", "#fa0000"), (60, "Bare / sparse vegetation", "#b4b4b4"),
                   (70, "Snow and ice", "#f0f0f0"), (80, "Permanent water", "#0064c8"),
                   (90, "Herbaceous wetland", "#0096a0"), (95, "Mangroves", "#00cf75"), (100, "Moss and lichen", "#fae6a0")],
    "lulc-annual": [(1, "Water", "#419bdf"), (2, "Trees", "#397d49"), (4, "Flooded vegetation", "#7a87c6"),
                    (5, "Crops", "#e49635"), (7, "Built area", "#c4281b"), (8, "Bare ground", "#a59b8f"),
                    (9, "Snow / ice", "#a8ebff"), (10, "Clouds", "#616161"), (11, "Rangeland", "#e3e2c3")],
}


# ====================================================================== context (set by the runner)
class _Ctx:
    def __init__(self):
        self.reset()

    def reset(self, aoi=None, out_dir=None, task=None, log=None, workers=8):
        self.aoi, self.task, self.workers = aoi, task, workers
        self.out_dir = out_dir or tempfile.gettempdir()
        self.log = log or (lambda *a: None)
        self.scale, self.epsg, self.max_pixels = None, None, 40e6
        self.clip = True
        self.layers, self.charts, self.tables = [], [], []
        self.tmp = tempfile.mkdtemp(prefix="lee_")
        self.n = 0

    def tmpfile(self, stem):
        self.n += 1
        return os.path.join(self.tmp, "%s_%04d.tif" % (stem, self.n))

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


CTX = _Ctx()


def config(scale=None, epsg=None, max_pixels=None, workers=None, clip=None):
    """lee.config(scale=30) — pixel size (m) for everything that follows; epsg to force a CRS;
    clip=False keeps the whole rectangle around the AOI (default: layers and statistics are cut to the AOI)."""
    if clip is not None:
        CTX.clip = bool(clip)
    if scale:
        CTX.scale = float(scale)
    if epsg:
        CTX.epsg = int(epsg)
    if max_pixels:
        CTX.max_pixels = float(max_pixels)
    if workers:
        CTX.workers = int(workers)


def aoi():
    if CTX.aoi is None:
        raise ValueError("No AOI. Set one in tab 1 (Use this AOI) or pass a geometry.")
    return CTX.aoi


def _cancelled():
    t = CTX.task
    if t is not None and t.isCanceled():
        raise KeyboardInterrupt("Stopped by the user")


def _progress(v):
    if CTX.task is not None:
        CTX.task.setProgress(max(0.0, min(99.0, v)))


def _grid(geom, native_res):
    from . import basemaps as bmod
    epsg = CTX.epsg or bmod.utm_for(geom)[0]
    scale = float(CTX.scale or native_res)
    g = se.Grid(geom, epsg, scale)
    if g.W * g.H > CTX.max_pixels:
        f = math.sqrt(g.W * g.H / CTX.max_pixels)
        scale = math.ceil(scale * f / 10.0) * 10.0
        CTX.log("Note: AOI is large — using %g m pixels (lee.config(scale=…, max_pixels=…) to change)." % scale)
        g = se.Grid(geom, epsg, scale)
    CTX.scale = scale
    CTX.epsg = epsg
    return g


# ====================================================================== Image
class Image:
    """An in-memory multi-band image on a grid. Values are float32; NaN = masked."""

    def __init__(self, data, names, grid, props=None, roles=None, legend=None):
        a = np.asarray(data, "float32")
        self.data = a[None] if a.ndim == 2 else a
        self.names = list(names)
        self.grid = grid
        self.props = dict(props or {})
        self.roles = dict(roles or {})
        self.legend = legend

    # ---------------- loading a single dataset (static layers) -----------------
    @staticmethod
    def load(dataset, date=None, bands=None, geometry=None):
        """lee.Image.load("dem") / ("worldcover", "2021") — mosaic of the dataset over the AOI."""
        col = ImageCollection(dataset)
        if date:
            d = str(date)
            col = col.filterDate(d if len(d) > 4 else d + "-01-01", None if len(d) > 4 else "%d-12-31" % int(d))
        if geometry is not None:
            col = col.filterBounds(geometry)
        if bands:
            col = col.select(*([bands] if isinstance(bands, str) else bands))
        return col.mosaic()

    @staticmethod
    def open(source, bands=None, resample="near"):
        """lee.Image.open("Classification (Random Forest)") or ("/path/file.tif") — a raster from the QGIS
        project (layer name) or a file, warped onto the AOI grid. Use it to bring RS Lab results (classified
        maps, downloads, indices) into a script. resample: near (classes), bilinear, cubic, average."""
        path = str(source)
        if not os.path.exists(path):
            from qgis.core import QgsProject
            hits = QgsProject.instance().mapLayersByName(path)
            if not hits:
                raise KeyError("No file or layer called '%s'" % source)
            path = hits[0].source().split("|")[0]
        src = gdal.Open(path)
        if src is None:
            raise IOError("Cannot open %s" % path)
        gt = src.GetGeoTransform()
        srs = osr.SpatialReference(wkt=src.GetProjection() or "")
        res = abs(gt[1]) * (111320.0 if srs.IsGeographic() else 1.0)
        grid = _grid(aoi(), res or 30)
        ds = gdal.Warp("", src, format="MEM", dstSRS=grid.wkt, outputBounds=grid.bounds, width=grid.W,
                       height=grid.H, resampleAlg=resample, dstNodata=float("nan"), outputType=gdal.GDT_Float32)
        data, names, roles = [], [], {}
        for i in range(1, ds.RasterCount + 1):
            b = ds.GetRasterBand(i)
            a = b.ReadAsArray().astype("float32")
            nd = src.GetRasterBand(i).GetNoDataValue()
            if nd is not None:
                a[a == nd] = np.nan
            desc = src.GetRasterBand(i).GetDescription() or ""
            n = (desc.split("|")[0].strip() or "b%d" % i)
            names.append(n)
            r = catalog.role_from_desc(desc) if desc else None
            if r:
                roles[r] = n
            data.append(a)
        img = Image(np.array(data), names, grid, {"source": os.path.basename(path)}, roles)
        if bands:
            img = img.select(bands)
        return img

    # ---------------- band handling -----------------
    def _idx(self, b):
        if isinstance(b, int):
            return b
        if b in self.names:
            return self.names.index(b)
        if b in self.roles and self.roles[b] in self.names:
            return self.names.index(self.roles[b])
        low = {n.lower(): i for i, n in enumerate(self.names)}
        if str(b).lower() in low:
            return low[str(b).lower()]
        raise KeyError("Band '%s' not found. Bands: %s; roles: %s" % (b, self.names, sorted(self.roles)))

    def bandNames(self):
        return list(self.names)

    def select(self, *bands):
        if len(bands) == 1 and isinstance(bands[0], (list, tuple)):
            bands = bands[0]
        idx = [self._idx(b) for b in bands]
        names = [self.names[i] for i in idx]
        roles = {r: n for r, n in self.roles.items() if n in names}
        return Image(self.data[idx], names, self.grid, self.props, roles, self.legend)

    def band(self, b):
        return self.data[self._idx(b)]

    def rename(self, *names):
        if len(names) == 1 and isinstance(names[0], (list, tuple)):
            names = names[0]
        if len(names) != len(self.names):
            raise ValueError("rename: %d names for %d bands" % (len(names), len(self.names)))
        return Image(self.data, names, self.grid, self.props, {}, self.legend)

    def addBands(self, other):
        return Image(np.concatenate([self.data, other.data]), self.names + other.names, self.grid, self.props,
                     dict(self.roles, **other.roles))

    # ---------------- maths -----------------
    def _bin(self, other, fn, name=None):
        with np.errstate(all="ignore"):
            if isinstance(other, Image):
                b = other.data
                if b.shape[0] == 1 and self.data.shape[0] > 1:
                    b = np.repeat(b, self.data.shape[0], 0)
                out = fn(self.data, b)
            else:
                out = fn(self.data, float(other))
        return Image(out.astype("float32"), [name] * out.shape[0] if name else self.names, self.grid, self.props,
                     self.roles)

    def add(self, o): return self._bin(o, np.add)
    def subtract(self, o): return self._bin(o, np.subtract)
    def multiply(self, o): return self._bin(o, np.multiply)
    def divide(self, o): return self._bin(o, np.divide)
    def pow(self, o): return self._bin(o, np.power)
    __add__, __sub__, __mul__, __truediv__, __pow__ = add, subtract, multiply, divide, pow
    def __radd__(self, o): return self.add(o)
    def __rmul__(self, o): return self.multiply(o)
    def __rsub__(self, o): return Image(float(o) - self.data, self.names, self.grid, self.props, self.roles)
    def __rtruediv__(self, o): return Image(float(o) / self.data, self.names, self.grid, self.props, self.roles)
    def __neg__(self): return Image(-self.data, self.names, self.grid, self.props, self.roles)

    def _cmp(self, o, fn):
        r = self._bin(o, lambda a, b: fn(a, b).astype("float32"))
        r.data[~np.isfinite(self.data)] = np.nan
        return r

    def gt(self, o): return self._cmp(o, np.greater)
    def gte(self, o): return self._cmp(o, np.greater_equal)
    def lt(self, o): return self._cmp(o, np.less)
    def lte(self, o): return self._cmp(o, np.less_equal)
    def eq(self, o): return self._cmp(o, np.equal)
    def neq(self, o): return self._cmp(o, np.not_equal)
    __gt__, __ge__, __lt__, __le__ = gt, gte, lt, lte

    def And(self, o): return self._bin(o, lambda a, b: ((a > 0) & (b > 0)).astype("float32"))
    def Or(self, o): return self._bin(o, lambda a, b: ((a > 0) | (b > 0)).astype("float32"))
    def Not(self): return Image((~(self.data > 0)).astype("float32"), self.names, self.grid, self.props)
    def abs(self): return Image(np.abs(self.data), self.names, self.grid, self.props, self.roles)
    def sqrt(self): return Image(np.sqrt(self.data), self.names, self.grid, self.props, self.roles)

    def log10(self):
        with np.errstate(all="ignore"):
            return Image(np.log10(self.data), self.names, self.grid, self.props, self.roles)

    def clamp(self, lo, hi):
        return Image(np.clip(self.data, lo, hi), self.names, self.grid, self.props, self.roles)

    def normalizedDifference(self, a="nir", b="red"):
        """(a − b) / (a + b), e.g. normalizedDifference("nir", "red") = NDVI. Takes band names or roles."""
        if isinstance(a, (list, tuple)):
            a, b = a
        x, y = self.band(a), self.band(b)
        with np.errstate(all="ignore"):
            nd = (x - y) / (x + y)
        return Image(nd, ["nd"], self.grid, self.props)

    def expression(self, expr, mapping=None):
        """img.expression("2.5*(nir-red)/(nir+6*red-7.5*blue+1)") — band names, roles and mapping keys."""
        env = {}
        for i, n in enumerate(self.names):
            env[n] = self.data[i]
            env[n.lower()] = self.data[i]
        for r, n in self.roles.items():
            env[r] = self.data[self.names.index(n)]
        for k, v in (mapping or {}).items():
            env[k] = v.data[0] if isinstance(v, Image) else (self.band(v) if isinstance(v, str) else v)
        out = R.band_math(expr, env)
        return Image(out, ["expression"], self.grid, self.props)

    # ---------------- masks -----------------
    def updateMask(self, mask):
        m = mask.data[0] if isinstance(mask, Image) else np.asarray(mask)
        d = self.data.copy()
        d[:, ~(np.nan_to_num(m) > 0)] = np.nan
        return Image(d, self.names, self.grid, self.props, self.roles, self.legend)

    def unmask(self, value=0):
        return Image(np.where(np.isfinite(self.data), self.data, value), self.names, self.grid, self.props, self.roles,
                     self.legend)

    def where(self, cond, value):
        c = cond.data[0] if isinstance(cond, Image) else cond
        v = value.data if isinstance(value, Image) else value
        d = np.where(np.nan_to_num(c) > 0, v, self.data)
        return Image(d, self.names, self.grid, self.props, self.roles, self.legend)

    def remap(self, src, dst, default=np.nan):
        d = np.full(self.data.shape, default, "float32")
        for a, b in zip(src, dst):
            d[self.data == a] = b
        return Image(d, self.names, self.grid, self.props)

    # ---------------- neighbourhood & terrain -----------------
    def _per_band(self, fn):
        return Image(np.stack([fn(np.where(np.isfinite(b), b, np.nanmean(b) if np.isfinite(b).any() else 0))
                               for b in self.data]), self.names, self.grid, self.props, self.roles)

    def focal_mean(self, radius=1): return self._per_band(lambda a: R.mean_filter(a, 2 * int(radius) + 1))
    def focal_median(self, radius=1): return self._per_band(lambda a: R.median_filter(a, 2 * int(radius) + 1))
    def gaussian(self, sigma=1.0): return self._per_band(lambda a: R.gaussian_filter(a, sigma))
    def edges(self): return self._per_band(R.sobel)

    def _gradients(self):
        z = np.where(np.isfinite(self.data[0]), self.data[0], np.nanmean(self.data[0]))
        s = self.grid.scale
        dzdx = R.convolve(z, np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]) / (8.0 * s))
        dzdy = R.convolve(z, np.array([[1, 2, 1], [0, 0, 0], [-1, -2, -1]]) / (8.0 * s))
        return dzdx, dzdy

    def slope(self):
        """Slope in degrees (from the first band, an elevation in metres)."""
        dx, dy = self._gradients()
        return Image(np.degrees(np.arctan(np.hypot(dx, dy))), ["slope"], self.grid, self.props)

    def aspect(self):
        dx, dy = self._gradients()
        return Image((np.degrees(np.arctan2(-dx, dy)) + 360) % 360, ["aspect"], self.grid, self.props)

    def hillshade(self, azimuth=315, elevation=45):
        dx, dy = self._gradients()
        sl = np.arctan(np.hypot(dx, dy))
        asp = np.arctan2(-dx, dy)
        az, el = np.radians(360 - azimuth + 90), np.radians(elevation)
        hs = np.sin(el) * np.cos(sl) + np.cos(el) * np.sin(sl) * np.cos(az - asp)
        return Image(np.clip(hs, 0, 1) * 255, ["hillshade"], self.grid, self.props)

    # ---------------- statistics -----------------
    def reduceRegion(self, reducer="mean", geometry=None):
        """{band: value} inside the AOI (or a geometry in EPSG:4326)."""
        m = self._region_mask(geometry)
        out = {}
        fns = {"mean": np.nanmean, "median": np.nanmedian, "min": np.nanmin, "max": np.nanmax, "std": np.nanstd,
               "sum": np.nansum, "count": lambda v: float(np.isfinite(v).sum())}
        for i, n in enumerate(self.names):
            v = self.data[i][m]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                out[n] = float(fns[reducer](v)) if v.size else float("nan")
        return out

    def _aoi_mask(self):
        g = self.grid
        m = getattr(g, "_lee_aoi_mask", None)
        if m is None or m.shape != self.data.shape[1:]:
            try:
                m = g.aoi_mask()
            except Exception:  # noqa: BLE001
                LOG.debug("no AOI mask", exc_info=True)
                m = np.ones(self.data.shape[1:], bool)
            g._lee_aoi_mask = m
        return m

    def clip(self, geometry=None):
        """Pixels outside the AOI (or `geometry`, EPSG:4326) become masked — like ee.Image.clip()."""
        m = self._region_mask(geometry) if geometry is not None else self._aoi_mask()
        return Image(np.where(m[None], self.data, np.nan), self.names, self.grid, self.props, self.roles, self.legend)

    def _region_mask(self, geometry=None):
        if geometry is None:
            return self._aoi_mask() if CTX.clip else np.ones(self.data.shape[1:], bool)
        g = se.Grid.__new__(se.Grid)
        g.__dict__.update(self.grid.__dict__)
        from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject)
        gg = QgsGeometry(geometry)
        gg.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                            QgsCoordinateReferenceSystem("EPSG:%d" % self.grid.epsg),
                                            QgsProject.instance()))
        g.geom = gg
        return g.aoi_mask()

    def classAreas(self, band=0):
        """{class value: hectares} — for land-cover maps or classified images."""
        a = self.data[self._idx(band)]
        v = a[np.isfinite(a) & self._region_mask()].astype("int64")
        vals, cnt = np.unique(v, return_counts=True)
        px = self.grid.scale ** 2 / 1e4
        names = {k: n for k, n, _ in (self.legend or [])}
        return {(names.get(int(k), int(k))): float(c * px) for k, c in zip(vals, cnt)}

    def histogram(self, band=0, bins=64):
        a = self.data[self._idx(band)]
        return np.histogram(a[np.isfinite(a)], bins=bins)

    def otsu(self, band=0):
        return R.otsu_threshold(self.data[self._idx(band)])

    # ---------------- classification -----------------
    def cluster(self, k=6, bands=None, sample=50000, seed=0):
        """Unsupervised K-means (bands scaled to z-scores). Returns a 1-band class image 1..k."""
        img = self.select(bands) if bands else self
        X = img.data.reshape(img.data.shape[0], -1).T
        ok = np.isfinite(X).all(1)
        Xs = X[ok]
        rng = np.random.default_rng(seed)
        S = Xs[rng.choice(len(Xs), min(sample, len(Xs)), replace=False)]
        mu, sd = S.mean(0), S.std(0) + 1e-12
        C, _, _ = R.kmeans((S - mu) / sd, k, 30, seed=seed)
        lab = np.full(X.shape[0], np.nan, "float32")
        lab[ok], _ = R.nearest_centre((Xs - mu) / sd, C)
        lab[ok] += 1
        return Image(lab.reshape(img.data.shape[1:]), ["cluster"], self.grid, self.props)

    # ---------------- output -----------------
    def save(self, path=None, nodata=-9999.0):
        path = path or os.path.join(CTX.out_dir, "lee_%s.tif" % "_".join(self.names)[:40])
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        ds = gdal.GetDriverByName("GTiff").Create(path, self.grid.W, self.grid.H, len(self.names), gdal.GDT_Float32,
                                                  ["COMPRESS=DEFLATE", "TILED=YES", "PREDICTOR=3", "BIGTIFF=IF_SAFER"])
        ds.SetGeoTransform(self.grid.gt)
        ds.SetProjection(self.grid.wkt)
        inv = {n: r for r, n in self.roles.items()}
        for i, n in enumerate(self.names):
            b = ds.GetRasterBand(i + 1)
            b.WriteArray(np.where(np.isfinite(self.data[i]), self.data[i], nodata))
            b.SetNoDataValue(nodata)
            b.SetDescription(catalog.band_desc(n, inv.get(n, ""), "") if n in inv else n)
        md = {"RSLAB_SOURCE": "LEE (local)", "RSLAB_PRODUCT": "scaled", "RSLAB_SCALE": "1.0", "RSLAB_OFFSET": "0.0"}
        md.update({("PROP_" + k)[:60]: str(v)[:500] for k, v in self.props.items()})
        ds.SetMetadata(md)
        ds = None
        return path

    def getInfo(self):
        return dict(bands=self.names, size=(self.grid.W, self.grid.H), scale=self.grid.scale,
                    crs="EPSG:%d" % self.grid.epsg, properties=self.props)

    def __repr__(self):
        return "<lee.Image %s %dx%d @%gm>" % (self.names, self.grid.W, self.grid.H, self.grid.scale)


# ====================================================================== ImageCollection
class ImageCollection:
    """A dataset filtered by date, place and metadata. Nothing is downloaded until you ask for a
    composite (median/mean/…), a mosaic, toList() or a time series."""

    def __init__(self, dataset, _state=None):
        key = ALIASES.get(dataset, dataset)
        if key not in DATASETS:
            raise KeyError("Unknown dataset '%s'. Available: %s" % (dataset, ", ".join(sorted(DATASETS))))
        self.key, self.spec = key, DATASETS[key]
        st = dict(start=None, end=None, geom=None, query={}, limit=None, bands=None, maps=[], mask=True,
                  ids=None, sort="cloud")
        st.update(_state or {})
        self.st = st
        self._items = None

    def _copy(self, **kw):
        st = dict(self.st)
        st["query"] = dict(self.st["query"])
        st["maps"] = list(self.st["maps"])
        st.update(kw)
        return ImageCollection(self.key, st)

    # ---------------- filters (lazy) -----------------
    def filterDate(self, start, end=None):
        start = str(start)
        if len(start) == 4:
            start += "-01-01"
        if end is None:
            end = start[:4] + "-12-31"
        return self._copy(start=start, end=str(end))

    def filterBounds(self, geometry):
        return self._copy(geom=geometry)

    def filterMetadata(self, prop, op, value):
        """filterMetadata("eo:cloud_cover", "lt", 20) — ops: lt lte gt gte eq neq."""
        q = dict(self.st["query"])
        q[prop] = {{"less_than": "lt", "greater_than": "gt", "equals": "eq", "not_equals": "neq"}.get(op, op): value}
        return self._copy(query=q)

    def filterCloud(self, max_percent):
        return self.filterMetadata("eo:cloud_cover", "lte", max_percent)

    def limit(self, n, sort="cloud"):
        return self._copy(limit=int(n), sort=sort)

    def select(self, *bands):
        if len(bands) == 1 and isinstance(bands[0], (list, tuple)):
            bands = bands[0]
        names = []
        roles = self.spec["roles"]
        inv = {r: b for b, r in roles.items()}
        for b in bands:
            if b in self.spec["assets"]:
                names.append(b)
            elif b in inv and inv[b] in self.spec["assets"]:
                names.append(inv[b])
            else:
                raise KeyError("Band '%s' not in %s (%s)" % (b, self.key, ", ".join(self.spec["assets"])))
        return self._copy(bands=names)

    def maskClouds(self, on=True):
        return self._copy(mask=bool(on))

    def map(self, fn):
        """Apply fn(Image) -> Image to every image when the collection is loaded."""
        return self._copy(maps=self.st["maps"] + [fn])

    # ---------------- searching -----------------
    def _geom(self):
        return self.st["geom"] or aoi()

    def items(self):
        if self._items is not None:
            return self._items
        sp, st = self.spec, self.st
        dt = None
        if st["start"]:
            dt = "%sT00:00:00Z/%sT23:59:59Z" % (st["start"], st["end"])
        q = {}
        if sp.get("platform"):
            q["platform"] = {"in": [sp["platform"]]}
        for k, v in st["query"].items():
            q[k] = v
        CTX.log("Searching %s …" % sp["title"])
        its = se.stac_search(sp["source"], [sp["coll"]], self._geom(), dt, q or None, 1000, CTX.task)
        if sp.get("static") and not st["start"] and its:
            # newest version only (e.g. WorldCover 2021, latest annual land cover)
            newest = max((i["properties"].get("datetime") or i["properties"].get("start_datetime") or "") for i in its)
            yr = newest[:4]
            its = [i for i in its if (i["properties"].get("datetime") or i["properties"].get("start_datetime") or "")[:4] == yr]
        cloud = sp.get("cloud")
        if st["sort"] == "cloud" and cloud:
            its.sort(key=lambda i: i["properties"].get(cloud, 0) or 0)
        else:
            its.sort(key=lambda i: i["properties"].get("datetime") or "")
        if st["limit"]:
            its = its[:st["limit"]]
        self._items = its
        return its

    def size(self):
        return len(self.items())

    def dates(self):
        return sorted(set((i["properties"].get("datetime") or i["properties"].get("start_datetime") or "")[:10]
                          for i in self.items()))

    def getInfo(self):
        cl = self.spec.get("cloud")
        return [dict(id=i["id"], date=(i["properties"].get("datetime") or "")[:10],
                     cloud=i["properties"].get(cl) if cl else None) for i in self.items()]

    # ---------------- loading -----------------
    def _bands(self):
        return self.st["bands"] or self.spec["default"]

    def _load(self, grid=None):
        """Read every item onto the grid (cloud-masked). Returns list of (props, {band: tif path})."""
        sp = self.spec
        its = self.items()
        if not its:
            raise ValueError("%s: no images for these filters (dates / place / cloud)." % self.key)
        grid = grid or _grid(self._geom(), sp["res"])
        bands = self._bands()
        aoi_m = grid.aoi_mask()
        se._gdal_http()
        masks = {}
        if self.st["mask"] and sp.get("mask"):
            mk, kind = sp["mask"]

            def mjob(it):
                def f():
                    _cancelled()
                    A = it["assets"]
                    if mk not in A:
                        return aoi_m
                    q = se._read(se._signed(sp["source"], sp["coll"], A[mk]["href"]), grid, "near")
                    v = ~np.isin(q, se.SCL_BAD) if kind == "s2" else (q.astype("int64") & se.LANDSAT_BAD_BITS) == 0
                    return v & aoi_m
                return f
            got = se._run_parallel(CTX.task or _NoTask(), [mjob(i) for i in its], CTX.workers, 0, 20, None)
            for it, v in zip(its, got):
                if not isinstance(v, Exception):
                    masks[it["id"]] = v
            its = [i for i in its if i["id"] in masks and masks[i["id"]].any()]
        nod = sp.get("nodata", 0)

        def bjob(it, b):
            def f():
                _cancelled()
                A = it["assets"]
                key = sp["assets"][b]
                if key not in A:
                    return None
                href = se._signed(sp["source"], sp["coll"], A[key]["href"])
                resample = sp.get("resample", "near")
                a = se._read(href, grid, resample, nod, gdal.GDT_Float32).astype("float32")
                if nod is not None:
                    a[a == nod] = np.nan
                if sp.get("sar"):
                    a[(a <= 0) | ~np.isfinite(a)] = np.nan
                    with np.errstate(all="ignore"):
                        a = 10 * np.log10(a)
                elif sp.get("s2"):
                    a[a == 0] = np.nan
                    off = se._s2_dn_offset(it, A[key])
                    a = (np.maximum(a + off, 1) if off else a) * sp["scale"] + sp["offset"]
                elif "scale" in sp:
                    a = a * (catalog.LST_SCALE if b.startswith("ST_") else sp["scale"]) + \
                        (catalog.LST_OFFSET if b.startswith("ST_") else sp["offset"])
                if it["id"] in masks:
                    a[~masks[it["id"]]] = np.nan
                elif not sp.get("static"):
                    a[~aoi_m] = np.nan
                fn = CTX.tmpfile("%s_%s" % (self.key, b))
                se._write(fn, a, grid, gdal.GDT_Float32, float("nan"))
                return fn
            return f
        pairs = [(it, b) for it in its for b in bands]
        t0 = time.time()
        got = se._run_parallel(CTX.task or _NoTask(), [bjob(*p) for p in pairs], CTX.workers, 20, 80, None)
        files = {}
        for (it, b), r in zip(pairs, got):
            if isinstance(r, Exception):
                if isinstance(r, KeyboardInterrupt):
                    raise r
                CTX.log("  %s %s: %s" % (it["id"], b, r))
            elif r:
                files.setdefault(it["id"], {})[b] = r
        out = []
        for it in its:
            if it["id"] in files and len(files[it["id"]]) == len(bands):
                p = dict(it["properties"])
                p["id"] = it["id"]
                p["date"] = (p.get("datetime") or p.get("start_datetime") or "")[:10]
                out.append((p, files[it["id"]]))
        CTX.log("  %d image(s) read in %.0f s (%d px × %d px @ %g m)" % (len(out), time.time() - t0, grid.W, grid.H,
                                                                         grid.scale))
        if not out:
            raise ValueError("%s: nothing could be read (all cloudy or no internet)." % self.key)
        return grid, bands, out

    def _image(self, grid, bands, props, files):
        data = np.stack([_read_local(files[b]) for b in bands])
        img = Image(data, bands, grid, props, {r: b for b, r in self.spec["roles"].items() if b in bands},
                    LEGENDS.get(self.key))
        for fn in self.st["maps"]:
            img = fn(img)
        return img

    def toList(self):
        """All images as a Python list of lee.Image (cloud-masked, maps applied)."""
        grid, bands, recs = self._load()
        return [self._image(grid, bands, p, f) for p, f in recs]

    def first(self):
        return self.limit(1).toList()[0]

    # ---------------- reductions -----------------
    def _reduce(self, how, q=None, quality=None):
        grid, bands, recs = self._load()
        if self.st["maps"] or quality:
            imgs = [self._image(grid, bands, p, f) for p, f in recs]
            names, roles = imgs[0].names, imgs[0].roles
            S = np.stack([i.data for i in imgs])
            C = _reduce_stack(S, how, q, imgs[0]._idx(quality) if quality else None)
            props = dict(count=len(imgs), start=recs[0][0]["date"], end=recs[-1][0]["date"], dataset=self.key)
            return Image(C, names, grid, props, roles, imgs[0].legend)
        # memory-friendly: band by band, row blocks
        out = np.empty((len(bands), grid.H, grid.W), "float32")
        rows = max(16, int(200e6 / max(1, len(recs) * grid.W * 4)))
        for j, b in enumerate(bands):
            dss = [gdal.Open(f[b]) for _, f in recs]
            for y in range(0, grid.H, rows):
                _cancelled()
                h = min(rows, grid.H - y)
                S = np.stack([d.GetRasterBand(1).ReadAsArray(0, y, grid.W, h) for d in dss])[:, None]
                out[j, y:y + h] = _reduce_stack(S, how, q, None)[0]
            dss = None
            _progress(80 + 19.0 * (j + 1) / len(bands))
        props = dict(count=len(recs), start=recs[0][0]["date"], end=recs[-1][0]["date"], dataset=self.key)
        return Image(out, bands, grid, props, {r: b for b, r in self.spec["roles"].items() if b in bands},
                     LEGENDS.get(self.key))

    def median(self): return self._reduce("median")
    def mean(self): return self._reduce("mean")
    def min(self): return self._reduce("min")
    def max(self): return self._reduce("max")
    def mosaic(self): return self._reduce("mosaic")
    def count(self): return self._reduce("count")
    def percentile(self, p): return self._reduce("percentile", p)

    def qualityMosaic(self, band):
        """Per pixel, the image where `band` is highest (e.g. a 'greenest pixel' mosaic on NDVI)."""
        return self._reduce("quality", quality=band)

    # ---------------- time series -----------------
    def timeSeries(self, fn=None, reducer="mean", geometry=None, title=None, ylabel=None):
        """[(date, value)] — fn(Image) -> 1-band Image (default: NDVI). Adds a chart in QGIS."""
        fn = fn or (lambda i: i.normalizedDifference("nir", "red"))
        grid, bands, recs = self._load()
        series = {}
        for k, (p, f) in enumerate(recs):
            _cancelled()
            img = fn(self._image(grid, bands, p, f))
            v = img.reduceRegion(reducer, geometry)[img.names[0]]
            if np.isfinite(v):
                series.setdefault(p["date"], []).append(v)
        ts = [(d, float(np.mean(v))) for d, v in sorted(series.items())]
        Chart.series(ts, title or "%s — %s per image" % (self.key, reducer), ylabel or (title or "value").split(" — ")[0])
        return ts

    def monthly(self, reducer="median"):
        """{"YYYY-MM": Image} — one composite per month."""
        grid, bands, recs = self._load()
        groups = {}
        for p, f in recs:
            groups.setdefault(p["date"][:7], []).append((p, f))
        out = {}
        for m, g in sorted(groups.items()):
            imgs = [self._image(grid, bands, p, f) for p, f in g]
            S = np.stack([i.data for i in imgs])
            out[m] = Image(_reduce_stack(S, reducer, None, None), imgs[0].names, grid, dict(month=m, count=len(g)),
                           imgs[0].roles, imgs[0].legend)
        return out


class _NoTask:
    def setProgress(self, v): pass
    def isCanceled(self): return False


def _read_local(fn):
    ds = gdal.Open(fn)
    a = ds.GetRasterBand(1).ReadAsArray().astype("float32")
    ds = None
    return a


def _reduce_stack(S, how, q, qi):
    """S: (n, bands, h, w) → (bands, h, w)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(all="ignore"):
            if how == "median":
                return np.nanmedian(S, 0)
            if how == "mean":
                return np.nanmean(S, 0)
            if how == "min":
                return np.nanmin(S, 0)
            if how == "max":
                return np.nanmax(S, 0)
            if how == "count":
                return np.isfinite(S).sum(0).astype("float32")
            if how == "percentile":
                return np.nanpercentile(S, q, axis=0)
            if how == "quality":
                key = np.where(np.isfinite(S[:, qi]), S[:, qi], -np.inf)
                best = np.argmax(key, 0)
                return np.take_along_axis(S, best[None, None], 0)[0]
            C = S[0].copy()          # mosaic: first valid value
            for i in range(1, S.shape[0]):
                m = ~np.isfinite(C)
                C[m] = S[i][m]
            return C


# ====================================================================== Map / Chart / print → QGIS
class Map:
    @staticmethod
    def addLayer(img, vis=None, name=None):
        """Map.addLayer(img, {"bands": ["red","green","blue"], "min": 0, "max": 0.3}, "True colour")
        vis keys: bands, min, max, palette (a QGIS colour-ramp name, e.g. RdYlGn, Blues, Viridis)."""
        vis = dict(vis or {})
        if "bands" in vis:
            img = img.select(vis["bands"])
        if vis.pop("clip", CTX.clip):
            img = img.clip()
        legend = img.legend
        if legend:                                   # legend: only the classes found inside the AOI
            v = img.data[0][np.isfinite(img.data[0])]
            present = set(np.unique(v).astype("int64").tolist()) if v.size else set()
            legend = [c for c in legend if int(c[0]) in present] or legend
        name = name or "_".join(img.names)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:50]
        path = img.save(os.path.join(CTX.out_dir, "lee_%s_%s.tif" % (safe, time.strftime("%H%M%S"))))
        kind = "rgb" if len(img.names) >= 3 else ("class" if legend else "single")
        CTX.layers.append(dict(path=path, name=name, vis=vis, kind=kind, legend=legend))
        CTX.log("Map: added '%s' → %s" % (name, path))
        return path


class Chart:
    @staticmethod
    def series(ts, title="Time series", ylabel="value"):
        CTX.charts.append(dict(kind="series", data=list(ts), title=title, ylabel=ylabel))

    @staticmethod
    def bars(d, title="", ylabel=""):
        CTX.charts.append(dict(kind="bars", data=dict(d), title=title, ylabel=ylabel))


def table(rows, title=""):
    CTX.tables.append(dict(rows=rows, title=title))


def datasets():
    """Text list of the available datasets."""
    return "\n".join("%-15s %s" % (k, v["title"]) for k, v in DATASETS.items())


# ====================================================================== runner (called from a QgsTask)
def run(code, aoi_geom, out_dir, task=None, log=None, workers=8):
    """Execute a LEE script. Returns dict(layers, charts, tables, seconds)."""
    CTX.reset(aoi_geom, out_dir, task, log, workers)
    t0 = time.time()
    lines = []

    def _print(*a):
        s = " ".join(str(x) for x in a)
        lines.append(s)
        CTX.log(s)
    import sys as _sys
    ns = {"lee": _sys.modules[__name__], "ee": _sys.modules[__name__], "Map": Map, "Chart": Chart, "np": np,
          "aoi": aoi_geom, "print": _print, "Image": Image, "ImageCollection": ImageCollection}
    try:
        exec(compile(code, "<LEE script>", "exec"), ns)  # noqa: S102  # nosec B102 - the user's own LEE script, run locally
    finally:
        res = dict(layers=list(CTX.layers), charts=list(CTX.charts), tables=list(CTX.tables),
                   seconds=time.time() - t0, printed=lines, scale=CTX.scale, epsg=CTX.epsg)
        CTX.cleanup()
    return res
