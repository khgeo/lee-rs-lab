"""Build one multi-band image (a "band set") from band files the student downloaded by hand —
USGS EarthExplorer Landsat (…_SR_B4.TIF, …_B4.TIF), Copernicus Browser / SAFE Sentinel-2
(…_B04_10m.jp2, B04.jp2, …), or any single-band GeoTIFFs.

Bands are recognised from the file names, sorted in band order, resampled to one pixel size,
optionally clipped to the AOI, and written with the same 'B4 | red | 665 nm' band names and
scale/offset metadata as the other download sources."""
import os
import re

from osgeo import gdal

from . import catalog
import logging

LOG = logging.getLogger(__name__)

gdal.UseExceptions()

EXTS = (".tif", ".tiff", ".jp2")


def _landsat_sensor(name):
    n = name.upper()
    for tag, key2, keytoa in (("LC09", "L9_L2", "L9_TOA"), ("LC08", "L8_L2", "L8_TOA"),
                              ("LE07", "L7_L2", None), ("LT05", "L5_L2", None), ("LT04", "L5_L2", None)):
        if n.startswith(tag) or ("_" + tag) in n or n.find(tag) >= 0:
            return key2, keytoa
    return None, None


def detect(path):
    """Return dict(sensor, band, role, wl, res, product, scale, offset, key) for one file, or a generic band."""
    name = os.path.basename(path)
    up = name.upper()
    # ---- Landsat Collection 2
    l2, toa = _landsat_sensor(up)
    if l2:
        m = re.search(r"_(SR|ST)_B(\d+)\.", up)
        if m:
            b = "%s_B%s" % (m.group(1), m.group(2))
            for bn, role, wl, res in catalog.SENSORS[l2]["bands"]:
                if bn == b:
                    sc, off = (catalog.LST_SCALE, catalog.LST_OFFSET) if b.startswith("ST_") else (2.75e-5, -0.2)
                    return dict(sensor=l2, band=bn, role=role, wl=wl, res=res, product="raw", scale=sc, offset=off,
                                key=(l2, bn))
        m = re.search(r"_B(\d+)\.", up)
        if m and toa:
            b = "B%s" % m.group(1)
            for bn, role, wl, res in catalog.SENSORS[toa]["bands"]:
                if bn == b:
                    # Level-1 DN: needs the MTL reflectance coefficients; stored as raw DN, scale 1
                    return dict(sensor=toa.replace("TOA", "L1"), band=bn, role=role, wl=wl, res=res, product="raw",
                                scale=1.0, offset=0.0, key=(toa, bn))
    # ---- Sentinel-2 (SAFE jp2 or exported GeoTIFF)
    if "S2A" in up or "S2B" in up or "S2C" in up or "MSIL" in up or re.search(r"(^|_)B(0\d|8A|1[012])(_\d+M)?\.", up):
        m = re.search(r"(?:^|_)B(0?\d{1,2}|8A)(?:_(\d+)M)?\.(JP2|TIF|TIFF)$", up)
        if m:
            raw = m.group(1)
            b = "B8A" if raw == "8A" else "B%d" % int(raw)
            l2a = "MSIL2A" in up or "_L2A" in up or m.group(2) is not None
            key = "S2_SR" if l2a else "S2_TOA"
            for bn, role, wl, res in catalog.SENSORS[key]["bands"]:
                if bn == b:
                    off = 0.0
                    pb = re.search(r"_N(\d{4})_", up)
                    if pb and int(pb.group(1)) >= 400:
                        off = -0.1        # processing baseline 04.00+: reflectance = DN/10000 − 0.1
                    return dict(sensor=key, band=bn, role=role, wl=wl, res=int(m.group(2) or res), product="raw",
                                scale=1e-4, offset=off, key=(key, bn))
    return dict(sensor="local", band=os.path.splitext(name)[0], role="", wl="", res=None, product="raw",
                scale=1.0, offset=0.0, key=("local", name))


def band_order(d):
    """Sort key: sensor band order from the catalogue, then file name."""
    if d["sensor"] in catalog.SENSORS:
        names = [b[0] for b in catalog.SENSORS[d["sensor"]]["bands"]]
        if d["band"] in names:
            return (0, names.index(d["band"]))
    if d["sensor"].endswith("_L1"):
        names = [b[0] for b in catalog.SENSORS[d["sensor"].replace("L1", "TOA")]["bands"]]
        if d["band"] in names:
            return (0, names.index(d["band"]))
    return (1, d["band"])


def scan_folder(folder):
    out = []
    for root, _, files in os.walk(folder):
        for fn in files:
            if fn.lower().endswith(EXTS) and not fn.lower().endswith((".aux.xml",)):
                if re.search(r"(QA|SCL|TCI|AOT|WVP|CLD|SNW|PVI|preview|thumb)", fn, re.I) and not re.search(r"_B\d", fn):
                    continue
                out.append(os.path.join(root, fn))
    return out


def plan(paths):
    """Detect every file, keep the finest copy of each band (SAFE has 10/20/60 m versions)."""
    best = {}
    for p in paths:
        try:
            d = detect(p)
        except Exception:  # noqa: BLE001
            LOG.debug("ignored error", exc_info=True)
            continue
        ds = gdal.Open(p)
        if ds is None:
            continue
        d["path"] = p
        d["pixel"] = abs(ds.GetGeoTransform()[1]) if ds.GetGeoTransform() else 0
        if ds.RasterCount > 1:
            d["multiband"] = ds.RasterCount
        ds = None
        k = d["key"]
        if k not in best or d["pixel"] < best[k]["pixel"]:
            best[k] = d
    return sorted(best.values(), key=band_order)


def build(task, items, out_path, pixel=None, aoi_geom_4326=None, epsg=None, clip=True):
    """items: list from plan() (in the wanted order). Returns dict(path, sensor, descs)."""
    if not items:
        raise ValueError("No band files.")
    srcs = []
    for d in items:
        if d.get("multiband"):
            for i in range(1, d["multiband"] + 1):
                v = out_path + ".b%d_%d.vrt" % (len(srcs), i)
                gdal.BuildVRT(v, [d["path"]], bandList=[i])
                srcs.append(v)
        else:
            srcs.append(d["path"])
    finest = min((d["pixel"] for d in items if d["pixel"]), default=None)
    res = float(pixel or finest or 10)
    vrt = out_path + ".stack.vrt"
    gdal.BuildVRT(vrt, srcs, separate=True, resolution="user", xRes=res, yRes=res, resampleAlg="near",
                  srcNodata=0, VRTNodata=0)
    task.setProgress(10)
    co = ["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER", "PREDICTOR=2"]
    kw = dict(format="GTiff", creationOptions=co, xRes=res, yRes=res, resampleAlg="near",
              srcNodata=0, dstNodata=0, multithread=True,
              callback=lambda c, *a: (task.setProgress(10 + 85 * c), 0 if task.isCanceled() else 1)[1])
    if epsg:
        kw["dstSRS"] = "EPSG:%d" % epsg
    if aoi_geom_4326 is not None:
        cut = out_path + ".aoi.geojson"
        with open(cut, "w") as f:
            f.write('{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},"geometry":%s}]}'
                    % aoi_geom_4326.asJson())
        kw.update(cutlineDSName=cut, cutlineSRS="EPSG:4326", cropToCutline=True)
        if not clip:
            kw.pop("cutlineDSName")
            kw.pop("cutlineSRS")
            kw.pop("cropToCutline")
            bb = aoi_geom_4326.boundingBox()
            kw.update(outputBounds=(bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()),
                      outputBoundsSRS="EPSG:4326")
    gdal.Warp(out_path, vrt, **kw)
    descs = []
    for d in items:
        if d.get("multiband"):
            descs += ["%s band %d" % (d["band"], i) for i in range(1, d["multiband"] + 1)]
        else:
            descs.append(catalog.band_desc(d["band"], d["role"], d["wl"]) if d["role"] else d["band"])
    sensors = sorted(set(d["sensor"] for d in items))
    sc = sorted(set((d["scale"], d["offset"]) for d in items if not d["band"].startswith("ST_")))
    ds = gdal.Open(out_path, gdal.GA_Update)
    for i, de in enumerate(descs[:ds.RasterCount]):
        ds.GetRasterBand(i + 1).SetDescription(de)
        ds.GetRasterBand(i + 1).SetNoDataValue(0)
    md = dict(RSLAB_SOURCE="local files", RSLAB_SENSOR=",".join(sensors), RSLAB_PRODUCT="raw",
              RSLAB_FILES=";".join(os.path.basename(d["path"]) for d in items)[:4000])
    if len(sc) == 1:
        md.update(RSLAB_SCALE=str(sc[0][0]), RSLAB_OFFSET=str(sc[0][1]))
    ds.SetMetadata(md)
    ds = None
    for p in [vrt, out_path + ".aoi.geojson"] + [s for s in srcs if s.endswith(".vrt")]:
        try:
            os.remove(p)
        except OSError:
            LOG.debug("ignored error", exc_info=True)
    task.setProgress(100)
    return dict(path=out_path, sensor=",".join(sensors), descs=descs, res=res)
