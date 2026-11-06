"""Imagery from free STAC catalogues of Cloud-Optimised GeoTIFFs (no account, no login).

* Earth Search (Element 84, AWS Open Data) — Sentinel-2 L2A, public, no login.
* Microsoft Planetary Computer — Sentinel-2 L2A, Landsat 5/7/8/9 C2 L2, Sentinel-1 RTC; no login
  (anonymous SAS token fetched automatically).

Only the pixels inside the AOI are read (GDAL /vsicurl/ range requests), each scene is cloud-masked
(Sentinel-2 SCL, Landsat QA_PIXEL), then a composite is computed locally with NumPy in row blocks.
The result carries band names, roles and scale metadata, so every tab works the same for any source.
"""
import json
from .compat import web_url
import math
import os
import shutil
import tempfile
import time
import urllib.request
import warnings

import numpy as np
from osgeo import gdal, ogr, osr

from . import catalog

gdal.UseExceptions()

HERE = os.path.dirname(__file__)


def _cfg():
    with open(os.path.join(HERE, "data", "sources.json"), encoding="utf-8") as f:
        return json.load(f)["imagery"]


S2_ES = {"B1": "coastal", "B2": "blue", "B3": "green", "B4": "red", "B5": "rededge1", "B6": "rededge2",
         "B7": "rededge3", "B8": "nir", "B8A": "nir08", "B9": "nir09", "B11": "swir16", "B12": "swir22"}
S2_PC = {"B1": "B01", "B2": "B02", "B3": "B03", "B4": "B04", "B5": "B05", "B6": "B06", "B7": "B07",
         "B8": "B08", "B8A": "B8A", "B9": "B09", "B11": "B11", "B12": "B12"}
L89_PC = {"SR_B1": "coastal", "SR_B2": "blue", "SR_B3": "green", "SR_B4": "red", "SR_B5": "nir08",
          "SR_B6": "swir16", "SR_B7": "swir22", "ST_B10": "lwir11"}
L57_PC = {"SR_B1": "blue", "SR_B2": "green", "SR_B3": "red", "SR_B4": "nir08", "SR_B5": "swir16",
          "SR_B7": "swir22", "ST_B6": "lwir"}

# source -> sensor key -> how to read it
STAC = {
    "earth_search": {
        "S2_SR": dict(coll="sentinel-2-l2a", assets=S2_ES, mask=("scl", "s2"), tile="s2:mgrs_tile"),
    },
    "planetary_computer": {
        "S2_SR": dict(coll="sentinel-2-l2a", assets=S2_PC, mask=("SCL", "s2"), tile="s2:mgrs_tile"),
        "L9_L2": dict(coll="landsat-c2-l2", platform="landsat-9", assets=L89_PC, mask=("qa_pixel", "landsat")),
        "L8_L2": dict(coll="landsat-c2-l2", platform="landsat-8", assets=L89_PC, mask=("qa_pixel", "landsat")),
        "L7_L2": dict(coll="landsat-c2-l2", platform="landsat-7", assets=L57_PC, mask=("qa_pixel", "landsat")),
        "L5_L2": dict(coll="landsat-c2-l2", platform="landsat-5", assets=L57_PC, mask=("qa_pixel", "landsat")),
        "S1_GRD": dict(coll="sentinel-1-rtc", assets={"VV": "vv", "VH": "vh"}, mask=None, sar=True),
    },
}

SCL_BAD = (0, 1, 3, 8, 9, 10)          # no data, saturated, cloud shadow, cloud medium/high, cirrus
LANDSAT_BAD_BITS = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)   # fill, dilated, cirrus, cloud, shadow


def sensors_for(source):
    return [k for k in catalog.SENSOR_ORDER if k in STAC.get(source, {})]


def _gdal_http():
    for k, v in (("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR"),
                 ("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.TIF,.tiff,.TIFF"),
                 ("GDAL_HTTP_MULTIRANGE", "YES"), ("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES"),
                 ("GDAL_HTTP_MAX_RETRY", "5"), ("GDAL_HTTP_RETRY_DELAY", "2"), ("VSI_CACHE", "TRUE"),
                 ("GDAL_HTTP_TIMEOUT", "120"), ("GDAL_HTTP_VERSION", "2"), ("GDAL_HTTP_MULTIPLEX", "YES"),
                 ("GDAL_INGESTED_BYTES_AT_OPEN", "32768"), ("CPL_VSIL_CURL_CACHE_SIZE", "200000000"),
                 ("GDAL_CACHEMAX", "512")):
        gdal.SetConfigOption(k, v)


def _http_json(url, body=None, timeout=90, tries=4):
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(web_url(url), data=data, headers={"User-Agent": "QGIS-RS-Lab/1.2",  # noqa: S310  # nosec B310
                                                                           "Content-Type": "application/json",
                                                                           "Accept": "application/geo+json, application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # nosec B310
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = "%s %s" % (e.code, e.read()[:300])
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (a + 1))
    raise IOError("STAC request failed (%s): %s" % (url, last))


# ------------------------------------------------------------------ Planetary Computer signing
_tokens = {}


def _pc_token(coll):
    t = _tokens.get(coll)
    if t and t[1] > time.time() + 300:
        return t[0]
    js = _http_json(_cfg()["planetary_computer"]["token"] + coll)
    tok = js["token"]
    _tokens[coll] = (tok, time.time() + 45 * 60)
    return tok


def _signed(source, coll, href):
    if source != "planetary_computer":
        return href
    return href + ("&" if "?" in href else "?") + _pc_token(coll)


# ------------------------------------------------------------------ search
def search(source, sensor, geom_4326, start, end, max_cloud=30, s1_orbit="BOTH", limit=300, task=None):
    """Return scene dicts (id, date, cloud, tile, item). end is exclusive (YYYY-MM-DD)."""
    spec = STAC[source][sensor]
    base = _cfg()[source]["stac"]
    end_incl = (np.datetime64(end) - np.timedelta64(1, "D")).astype(str)
    body = {"collections": [spec["coll"]], "intersects": json.loads(geom_4326.asJson()),
            "datetime": "%sT00:00:00Z/%sT23:59:59Z" % (start, end_incl), "limit": 100}
    q = {}
    if not spec.get("sar") and max_cloud < 100:
        q["eo:cloud_cover"] = {"lte": max_cloud}
    if spec.get("platform"):
        q["platform"] = {"in": [spec["platform"]]}
    if spec.get("sar") and s1_orbit in ("ASCENDING", "DESCENDING"):
        q["sat:orbit_state"] = {"eq": s1_orbit.lower()}
    if q:
        body["query"] = q
    url, items = base.rstrip("/") + "/search", []
    while url and len(items) < limit:
        if task is not None and task.isCanceled():
            break
        js = _http_json(url, body)
        items += js.get("features", [])
        nxt = next((ln for ln in js.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt["href"]
        if nxt.get("method", "GET").upper() == "POST":
            body = nxt.get("body") or body
            if nxt.get("merge"):
                body = dict(body, **nxt.get("body", {}))
        else:
            body = None
    out = []
    for it in items[:limit]:
        p = it.get("properties", {})
        dt = (p.get("datetime") or p.get("start_datetime") or "")[:10]
        tile = p.get(spec.get("tile", ""), "") if spec.get("tile") else ""
        if not tile and p.get("grid:code"):
            tile = str(p["grid:code"]).replace("MGRS-", "")
        if not tile and p.get("mgrs:utm_zone"):
            tile = "%s%s%s" % (p.get("mgrs:utm_zone"), p.get("mgrs:latitude_band", ""), p.get("mgrs:grid_square", ""))
        if not tile and "landsat:wrs_path" in p:
            tile = "%s/%s" % (p.get("landsat:wrs_path"), p.get("landsat:wrs_row"))
        if spec.get("sar"):
            tile = "%s orbit %s" % (p.get("sat:orbit_state", ""), p.get("sat:relative_orbit", ""))
        out.append(dict(id=it["id"], date=dt, cloud=p.get("eo:cloud_cover"), tile=tile, item=it))
    out.sort(key=lambda r: r["date"])
    return out


def stac_search(source, collections, geom_4326=None, datetime=None, query=None, limit=500, task=None):
    """Generic STAC /search with paging. Returns raw items (dicts)."""
    base = _cfg()[source]["stac"]
    body = {"collections": list(collections), "limit": 100}
    if geom_4326 is not None:
        body["intersects"] = json.loads(geom_4326.asJson())
    if datetime:
        body["datetime"] = datetime
    if query:
        body["query"] = query
    url, items = base.rstrip("/") + "/search", []
    while url and len(items) < limit:
        if task is not None and task.isCanceled():
            break
        js = _http_json(url, body)
        items += js.get("features", [])
        nxt = next((ln for ln in js.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt:
            break
        url = nxt["href"]
        if nxt.get("method", "GET").upper() == "POST":
            body = dict(body, **(nxt.get("body") or {})) if nxt.get("merge") else (nxt.get("body") or body)
        else:
            body = None
    return items[:limit]


def choose_items(scenes, method, max_scenes=12, selected_ids=None):
    if not scenes:
        return []
    if method == "selected":
        ids = set(selected_ids or [])
        return [s for s in scenes if s["id"] in ids]
    if method == "leastcloudy":
        best = min(scenes, key=lambda s: (s["cloud"] if s["cloud"] is not None else 0))
        return [s for s in scenes if s["date"] == best["date"]]  # all tiles of that day → mosaic
    ranked = sorted(scenes, key=lambda s: (s["cloud"] if s["cloud"] is not None else 0, s["date"]))
    return ranked[:max(1, max_scenes)]


# ------------------------------------------------------------------ grid
class Grid:
    def __init__(self, geom_4326, epsg, scale):
        from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
                               QgsProject)
        g = QgsGeometry(geom_4326)
        g.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                           QgsCoordinateReferenceSystem("EPSG:%d" % epsg), QgsProject.instance()))
        bb = g.boundingBox()
        s = float(scale)
        self.x0 = math.floor(bb.xMinimum() / s) * s
        self.y1 = math.ceil(bb.yMaximum() / s) * s
        x1 = math.ceil(bb.xMaximum() / s) * s
        y0 = math.floor(bb.yMinimum() / s) * s
        self.W, self.H = int(round((x1 - self.x0) / s)), int(round((self.y1 - y0) / s))
        self.bounds = (self.x0, y0, x1, self.y1)
        self.scale, self.epsg, self.geom = s, epsg, g
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(epsg)
        self.wkt = srs.ExportToWkt()
        self.gt = (self.x0, s, 0, self.y1, 0, -s)

    def aoi_mask(self):
        drv = gdal.GetDriverByName("MEM")
        ds = drv.Create("", self.W, self.H, 1, gdal.GDT_Byte)
        ds.SetGeoTransform(self.gt)
        ds.SetProjection(self.wkt)
        mem = ogr.GetDriverByName("Memory").CreateDataSource("m")
        srs = osr.SpatialReference(wkt=self.wkt)
        lyr = mem.CreateLayer("a", srs, ogr.wkbMultiPolygon)
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetGeometry(ogr.CreateGeometryFromWkt(self.geom.asWkt()))
        lyr.CreateFeature(f)
        gdal.RasterizeLayer(ds, [1], lyr, burn_values=[1], options=["ALL_TOUCHED=TRUE"])
        return ds.GetRasterBand(1).ReadAsArray().astype(bool)


def _read(href, grid, resample="near", src_nodata=None, out_type=None):
    """Read one band onto the AOI grid.

    Fast path: when the image is already in the grid's CRS (the normal case: Sentinel-2 / Landsat tiles
    are in UTM) and is not much coarser-sampled than needed, read ONLY the pixel window that covers the
    AOI and pick nearest pixels with index arrays — no warping, minimal HTTP range requests.
    Otherwise fall back to gdal.Warp (reprojection, or overviews for coarse quick looks)."""
    gdal.UseExceptions()
    path = "/vsicurl/" + href if href.startswith("http") else href
    ds = gdal.Open(path)
    try:
        srs = ds.GetSpatialRef()
        gt = ds.GetGeoTransform()
        tgt = osr.SpatialReference(wkt=grid.wkt)
        same = srs is not None and srs.IsSame(tgt) and gt[2] == 0 and gt[4] == 0
        # (source much finer than the grid — e.g. a coarse quick look — goes to Warp to use overviews)
        is_float = out_type in (gdal.GDT_Float32, gdal.GDT_Float64)
        if same and abs(gt[1]) >= grid.scale / 1.5:
            # outside the tile = no data: NaN for float output (so mosaics can fill from other tiles)
            fill = float("nan") if is_float else (src_nodata if src_nodata is not None else 0)
            xs = grid.x0 + (np.arange(grid.W) + 0.5) * grid.scale
            ys = grid.y1 - (np.arange(grid.H) + 0.5) * grid.scale
            ci = np.floor((xs - gt[0]) / gt[1]).astype("int64")
            ri = np.floor((ys - gt[3]) / gt[5]).astype("int64")
            vc = (ci >= 0) & (ci < ds.RasterXSize)
            vr = (ri >= 0) & (ri < ds.RasterYSize)
            band = ds.GetRasterBand(1)
            dt = gdal.GetDataTypeName(out_type) if out_type else gdal.GetDataTypeName(band.DataType)
            np_t = {"Byte": "uint8", "UInt16": "uint16", "Int16": "int16", "UInt32": "uint32", "Int32": "int32",
                    "Float32": "float32", "Float64": "float64"}.get(dt, "float32")
            out = np.full((grid.H, grid.W), fill, np_t)
            if vc.any() and vr.any():
                c0, c1 = int(ci[vc].min()), int(ci[vc].max())
                r0, r1 = int(ri[vr].min()), int(ri[vr].max())
                win = band.ReadAsArray(c0, r0, c1 - c0 + 1, r1 - r0 + 1)
                out[np.ix_(vr, vc)] = win[np.ix_(ri[vr] - r0, ci[vc] - c0)]
            return out
    finally:
        ds = None
    opts = dict(format="MEM", dstSRS=grid.wkt, outputBounds=grid.bounds, width=grid.W, height=grid.H,
                resampleAlg=resample)
    if src_nodata is not None:
        opts["srcNodata"] = src_nodata
        opts["dstNodata"] = src_nodata
    if out_type is not None:
        opts["outputType"] = out_type
        if out_type in (gdal.GDT_Float32, gdal.GDT_Float64):
            opts["dstNodata"] = float("nan")
    wds = gdal.Warp("", path, **opts)
    a = wds.GetRasterBand(1).ReadAsArray()
    wds = None
    return a


_warp = _read   # backwards name


def _s2_dn_offset(item, asset):
    """DN to add so that values match the 'harmonized' (pre-2022) Sentinel-2 scale (as in the 'harmonized' collections)."""
    rb = (asset.get("raster:bands") or [{}])[0]
    if "offset" in rb and rb.get("scale"):
        return float(rb["offset"]) / float(rb["scale"])          # e.g. -0.1 / 0.0001 = -1000
    pb = item.get("properties", {}).get("s2:processing_baseline")
    try:
        return -1000.0 if pb and float(pb) >= 4.0 else 0.0
    except ValueError:
        return 0.0


def _write(fn, a, grid, gdt, nodata):
    ds = gdal.GetDriverByName("GTiff").Create(fn, grid.W, grid.H, 1, gdt,
                                              ["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform(grid.gt)
    ds.SetProjection(grid.wkt)
    b = ds.GetRasterBand(1)
    b.SetNoDataValue(nodata)
    b.WriteArray(a)
    ds = None


def _run_parallel(task, jobs, workers, p0, p1, say):
    """Run callables in a thread pool (GDAL releases the GIL during HTTP reads). Returns results in order."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    res = [None] * len(jobs)
    if not jobs:
        return res
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(fn): i for i, fn in enumerate(jobs)}
        for f in as_completed(futs):
            i = futs[f]
            try:
                res[i] = f.result()
            except Exception as e:  # noqa: BLE001
                res[i] = e
            done += 1
            task.setProgress(p0 + (p1 - p0) * done / len(jobs))
            if task.isCanceled():
                for g in futs:
                    g.cancel()
                break
    return res


# ------------------------------------------------------------------ download job (runs in a QgsTask)
def download(task, source, sensor, geom_4326, start, end, max_cloud, method, bands, product, out_path,
             scale, epsg, clip=True, use_mask=True, s1_orbit="BOTH", max_scenes=12, selected_ids=None,
             scenes=None, band_descs=None, meta=None, log=None, workers=8, min_clear=5.0):
    """1) read the small cloud-mask band of every candidate scene (in parallel) and measure how much of
    the AOI is really clear; 2) read the bands only for the clearest scenes (in parallel);
    3) composite locally in row blocks."""
    _gdal_http()
    t0 = time.time()
    spec = STAC[source][sensor]
    sar = spec.get("sar", False)
    s = catalog.SENSORS[sensor]

    def say(m):
        if log is not None:
            log.append(m)

    if scenes is None:
        scenes = search(source, sensor, geom_4326, start, end, max_cloud, s1_orbit, task=task)
    if method == "selected":
        cands = choose_items(scenes, "selected", 0, selected_ids)
    else:
        cands = sorted(scenes, key=lambda x: (x["cloud"] if x["cloud"] is not None else 0, x["date"]))
        cands = cands[:max(max_scenes * 3, 12)]          # a few spares in case scenes are cloudy over the AOI
    if not cands:
        raise ValueError("No scenes found for these dates / cloud limit.")
    grid = Grid(geom_4326, epsg, scale)
    if grid.W * grid.H == 0:
        raise ValueError("AOI is empty.")
    aoi = grid.aoi_mask() if clip else np.ones((grid.H, grid.W), bool)
    n_aoi = max(int(aoi.sum()), 1)
    tmp = tempfile.mkdtemp(prefix="rslab_stac_")
    nb = len(bands)
    try:
        # ---- 1. cloud masks first (one small band per scene) → real clear fraction inside the AOI
        has_mask = use_mask and spec.get("mask") is not None
        masks = {}
        if has_mask:
            mk, kind = spec["mask"]

            def mjob(sc):
                def f():
                    A = sc["item"].get("assets", {})
                    if mk not in A:
                        return None
                    q = _read(_signed(source, spec["coll"], A[mk]["href"]), grid, "near")
                    if kind == "s2":
                        v = ~np.isin(q, SCL_BAD)
                    else:
                        v = (q.astype("int64") & LANDSAT_BAD_BITS) == 0
                    return v & aoi
                return f
            got = _run_parallel(task, [mjob(sc) for sc in cands], workers, 0, 25, say)
            for sc, v in zip(cands, got):
                if isinstance(v, Exception):
                    say("%s: mask not readable (%s)" % (sc["id"], v))
                    continue
                masks[sc["id"]] = v if v is not None else np.array(aoi)
                sc["clear"] = 100.0 * masks[sc["id"]].sum() / n_aoi
            if task.isCanceled():
                return None
            say("Cloud masks: %d scenes checked in %.0f s" % (len(cands), time.time() - t0))
            cands = [c for c in cands if c["id"] in masks]
        else:
            for sc in cands:
                sc["clear"] = 100.0
        # choose the scenes actually used
        if method == "selected":
            items = cands
        elif method == "leastcloudy":
            best = max(cands, key=lambda x: (x["clear"], -(x["cloud"] or 0))) if cands else None
            items = [c for c in cands if best and c["date"] == best["date"]]
        else:
            ok = [c for c in cands if c["clear"] >= min_clear] or cands
            items = sorted(ok, key=lambda x: (-x["clear"], x["cloud"] or 0))[:max(1, max_scenes)]
        for c in items:
            say("use %s %s — %.0f%% of AOI clear" % (c["date"], c["id"], c["clear"]))
        if not items:
            raise ValueError("Every scene was cloudy over the AOI. Try more dates or a higher cloud limit.")

        # ---- 2. bands of the chosen scenes, all (scene, band) pairs in parallel, each to a small temp file
        files = {}

        def bjob(k, sc, j, b):
            def f():
                it = sc["item"]
                A = it.get("assets", {})
                key = spec["assets"][b]
                if key not in A:
                    raise KeyError("asset %s missing" % key)
                href = _signed(source, spec["coll"], A[key]["href"])
                valid = masks.get(sc["id"], aoi)
                fn = os.path.join(tmp, "s%03d_b%02d.tif" % (k, j))
                if sar:
                    a = _read(href, grid, "bilinear", None, gdal.GDT_Float32).astype("float32")
                    a[(a <= 0) | ~np.isfinite(a) | ~valid] = np.nan
                    _write(fn, a, grid, gdal.GDT_Float32, float("nan"))
                else:
                    a = _read(href, grid, "near", 0, gdal.GDT_UInt16).astype("int32")
                    if sensor.startswith("S2"):
                        off = _s2_dn_offset(it, A[key])
                        if off:
                            a = np.where(a > 0, np.maximum(a + int(off), 1), 0)
                    a[~valid] = 0
                    _write(fn, a.astype("uint16"), grid, gdal.GDT_UInt16, 0)
                return fn
            return f
        pairs = [(k, sc, j, b) for k, sc in enumerate(items) for j, b in enumerate(bands)]
        t1 = time.time()
        got = _run_parallel(task, [bjob(*p) for p in pairs], workers, 25, 85, say)
        if task.isCanceled():
            return None
        for (k, sc, j, b), r in zip(pairs, got):
            if isinstance(r, Exception):
                say("%s %s: %s" % (sc["id"], b, r))
            else:
                files[(k, j)] = r
        used = sorted(set(k for (k, j) in files if all((k, jj) in files for jj in range(nb))))
        if not used:
            raise ValueError("No scene could be read. Check the internet connection (details in the log).")
        say("Bands: %d files read in %.0f s with %d parallel connections" % (len(files), time.time() - t1, workers))

        # ---- 3. composite in row blocks
        n = len(used)
        rows = max(16, int(150e6 / max(1, n * nb * grid.W * 4)))
        float_out = product == "scaled" or sar
        nodata = -9999.0 if float_out else 0
        out = gdal.GetDriverByName("GTiff").Create(
            out_path, grid.W, grid.H, nb, gdal.GDT_Float32 if float_out else gdal.GDT_UInt16,
            ["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER", "PREDICTOR=%d" % (3 if float_out else 2)])
        out.SetGeoTransform(grid.gt)
        out.SetProjection(grid.wkt)
        dss = [[gdal.Open(files[(k, j)]) for j in range(nb)] for k in used]
        roles = {b: r for b, r, _, _ in s["bands"]}
        ir = bands.index(next((b for b in bands if roles.get(b) == "red"), bands[0]))
        inn = bands.index(next((b for b in bands if roles.get(b) == "nir"), bands[0]))
        mosaic = method in ("selected", "leastcloudy")
        for y in range(0, grid.H, rows):
            h = min(rows, grid.H - y)
            S = np.empty((n, nb, h, grid.W), "float32")
            for i, row_ds in enumerate(dss):
                for j, d in enumerate(row_ds):
                    S[i, j] = d.GetRasterBand(1).ReadAsArray(0, y, grid.W, h)
            if not sar:
                S[S == 0] = np.nan
            # SAR stays linear power here (averaging dB is wrong); converted to dB when written
            with np.errstate(all="ignore"):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    if mosaic or n == 1:
                        C = S[0].copy()
                        for i in range(1, n):
                            m = ~np.isfinite(C)
                            C[m] = S[i][m]
                    elif method == "mean":
                        C = np.nanmean(S, 0)
                    elif method == "min":
                        C = np.nanmin(S, 0)
                    elif method == "p25":
                        C = np.nanpercentile(S, 25, axis=0)
                    elif method == "greenest" and ir != inn:
                        nd = (S[:, inn] - S[:, ir]) / (S[:, inn] + S[:, ir])
                        nd = np.where(np.isfinite(nd), nd, -np.inf)
                        best = np.argmax(nd, 0)
                        C = np.take_along_axis(S, best[None, None], 0)[0]
                    else:
                        C = np.nanmedian(S, 0)
            for j, b in enumerate(bands):
                c = C[j]
                if sar:
                    with np.errstate(all="ignore"):
                        c = 10.0 * np.log10(c)
                elif product == "scaled":
                    c = c * (catalog.LST_SCALE if b.startswith("ST_") else s["scale"]) + \
                        (catalog.LST_OFFSET if b.startswith("ST_") else s["offset"])
                c = np.where(np.isfinite(c), c, nodata)
                if not float_out:
                    c = np.clip(np.round(c), 0, 65535).astype("uint16")
                out.GetRasterBand(j + 1).WriteArray(c.astype("float32") if float_out else c, 0, y)
            task.setProgress(85 + 14.0 * (y + h) / grid.H)
        for j in range(nb):
            band = out.GetRasterBand(j + 1)
            band.SetNoDataValue(nodata)
            if band_descs:
                band.SetDescription(band_descs[j])
        md = dict(meta or {})
        md.update(RSLAB_SOURCE=source, RSLAB_SCENES=str(n),
                  RSLAB_SCENE_IDS=",".join(items[k]["id"] for k in used)[:4000])
        if sar:
            md.update(RSLAB_PRODUCT="scaled", RSLAB_SCALE="1.0", RSLAB_OFFSET="0.0", RSLAB_UNITS="dB (gamma0 RTC)")
        out.SetMetadata({k: str(v) for k, v in md.items()})
        out = None
        dss = None
        say("Total %.0f s" % (time.time() - t0))
        task.setProgress(100)
        return dict(path=out_path, n_scenes=n, n_items=len(items), seconds=time.time() - t0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def estimate_mb(geom_4326, epsg, scale, nbands, nscenes):
    g = Grid(geom_4326, epsg, scale)
    return g.W * g.H * nbands * 4 * max(1, nscenes) / 1e6, g.W * g.H * nbands * 2 / 1e6
