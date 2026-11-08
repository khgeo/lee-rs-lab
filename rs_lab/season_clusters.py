"""Multi-season land-cover clustering helpers (tab 3 › Unsupervised).

* build_season_stack(): a 13-band feature stack for the AOI made locally with LEE — Sentinel-2
  dry-season (Feb–Apr) and green-season (Sep–Nov) medians plus Sentinel-1 — so K-means can tell
  evergreen from deciduous trees, paddy from upland crops, permanent from seasonal water, …
* hint(): a rough, explainable guess of what a cluster is, from its mean values.
* suggest_class(): the class of the active scheme that matches a hint (by name).
* examples(): random pixels of one cluster, to fly the map there.
* split_cluster(): re-cluster one mixed cluster into n sub-clusters.

All numbers are computed on your computer; no account, no login.
"""
import numpy as np
from osgeo import gdal

from . import raster_io as rio
from . import rs_core as R

SEASON_BANDS = ["NDVI_dry", "NDVI_green", "NDVI_amp", "NDMI_dry", "NDRE_green", "MNDWI_dry",
                "MNDWI_green", "NDBI_dry", "SWIR_dry", "bright_dry", "VH_dry", "VH_wet", "VV_dry"]
S2_BANDS = ["B2", "B3", "B4", "B5", "B8", "B11"]


def _nd(a, b):
    with np.errstate(all="ignore"):
        return (a - b) / (a + b)


def _median(lee, dataset, start, end, cloud, n, bands, log):
    col = lee.ImageCollection(dataset).filterDate(start, end).filterBounds(lee.aoi())
    if cloud is not None:
        col = col.filterCloud(cloud)
    col = col.limit(int(n)).select(bands)
    img = col.median()
    log("  %s %s → %s: %s image(s)" % (dataset, start, end, img.props.get("count", "?")))
    return img


def build_season_stack(task, aoi_geom, year, scale, out_dir, max_scenes=8, use_s1=True,
                       workers=8, log=None, max_pixels=15e6):
    """Returns dict(path, bands, scale, epsg, valid_pct, counts)."""
    from . import lee
    from .procs import _out
    log = log or (lambda *a: None)
    y = int(year)
    lee.CTX.reset(aoi_geom, out_dir, task, log, workers)
    lee.CTX.max_pixels = float(max_pixels)
    if scale:
        lee.config(scale=scale)
    try:
        counts = {}
        log("Sentinel-2 dry season (Feb–Apr %d)…" % y)
        try:
            dry = _median(lee, "sentinel-2", "%d-02-01" % y, "%d-04-30" % y, 30, max_scenes, S2_BANDS, log)
        except ValueError:
            dry = _median(lee, "sentinel-2", "%d-01-01" % y, "%d-04-30" % y, 70, max_scenes, S2_BANDS, log)
        counts["S2 dry"] = dry.props.get("count")
        log("Sentinel-2 green season (Sep–Nov %d)…" % y)
        try:
            grn = _median(lee, "sentinel-2", "%d-09-01" % y, "%d-11-30" % y, 60, max_scenes, S2_BANDS, log)
        except ValueError:
            grn = _median(lee, "sentinel-2", "%d-08-01" % y, "%d-12-15" % y, 90, max_scenes, S2_BANDS, log)
        counts["S2 green"] = grn.props.get("count")

        b = lambda img, n: img.band(n)  # noqa: E731
        d_b3, d_b4, d_b8, d_b11 = b(dry, "B3"), b(dry, "B4"), b(dry, "B8"), b(dry, "B11")
        g_b3, g_b4, g_b5, g_b8, g_b11 = b(grn, "B3"), b(grn, "B4"), b(grn, "B5"), b(grn, "B8"), b(grn, "B11")
        ndvi_d, ndvi_g = _nd(d_b8, d_b4), _nd(g_b8, g_b4)
        layers = [
            ndvi_d, ndvi_g, np.abs(ndvi_g - ndvi_d), _nd(d_b8, d_b11), _nd(g_b8, g_b5),
            _nd(d_b3, d_b11), _nd(g_b3, g_b11), _nd(d_b11, d_b8), d_b11,
            (b(dry, "B2") + d_b3 + d_b4) / 3.0,
        ]
        names = SEASON_BANDS[:10]
        if use_s1:
            try:
                log("Sentinel-1 dry (Feb–Apr) and wet season (Jul–Sep)…")
                s1d = _median(lee, "sentinel-1", "%d-02-01" % y, "%d-04-30" % y, None, 6, ["VV", "VH"], log)
                s1w = _median(lee, "sentinel-1", "%d-07-01" % y, "%d-09-30" % y, None, 6, ["VV", "VH"], log)
                if s1d.data.shape[1:] == dry.data.shape[1:] and s1w.data.shape[1:] == dry.data.shape[1:]:
                    layers += [s1d.band("VH"), s1w.band("VH"), s1d.band("VV")]
                    names += SEASON_BANDS[10:]
                    counts["S1 dry"] = s1d.props.get("count")
                    counts["S1 wet"] = s1w.props.get("count")
            except Exception as e:  # noqa: BLE001 — radar is optional
                log("  Sentinel-1 skipped: %s" % e)
        stack = lee.Image(np.stack(layers).astype("float32"), names, dry.grid,
                          {"year": y, "stack": "multi-season"})
        valid = float(np.isfinite(stack.data).all(0).sum()) / max(1, int(np.isfinite(ndvi_d).size))
        path = stack.save(_out(out_dir, "season_stack_%d" % y))
        return dict(path=path, bands=names, scale=lee.CTX.scale, epsg=lee.CTX.epsg,
                    valid_pct=100.0 * valid, counts=counts)
    finally:
        lee.CTX.cleanup()


# ====================================================================== hints
HINT_TEXT = {
    "water": "water (open, all year)",
    "flooded": "flooded Sep–Nov, green in dry season: flooded forest / recession rice?",
    "wetland": "flooded after the rains: wetland / seasonal water?",
    "built": "bright, rough radar: built-up?",
    "bare": "no vegetation: bare soil / sand?",
    "rice": "bare in dry season, flooded in rainy season: rice?",
    "crop": "bare in dry season, green after rains: crop?",
    "grass": "low greenness all year: grass / open land?",
    "evergreen": "green all year, rough radar: evergreen forest?",
    "orchard": "green all year: tree crop / orchard?",
    "deciduous": "leafless in dry season: deciduous forest?",
    "shrub": "medium greenness, mixed: shrubland?",
    "forest": "dense vegetation: forest?",
    "none": "",
}

# hint -> keywords, tried in order against the class names of the active scheme
HINT_KEYWORDS = {
    "water": ("water",),
    "flooded": ("flood", "wetland", "mangrove"),
    "wetland": ("wetland", "flood"),
    "built": ("built", "urban", "settle"),
    "bare": ("bare", "barren", "sand"),
    "rice": ("rice", "paddy", "crop", "agri"),
    "crop": ("crop", "agri", "rice"),
    "grass": ("grass", "rangeland", "shrub /", "vegetation"),
    "evergreen": ("evergreen", "forest", "tree", "vegetation"),
    "orchard": ("orchard", "plantation", "tree crop", "agri", "crop"),
    "deciduous": ("deciduous", "forest", "tree", "vegetation"),
    "shrub": ("shrub", "vegetation"),
    "forest": ("forest", "tree", "vegetation"),
}


def _name(desc):
    return desc.split("|")[0].strip()


def means_by_name(descs, centre):
    """{band name or role: value}; adds NDVI/MNDWI/NDBI from nir/red/green/swir1 if missing."""
    from . import catalog
    m = {}
    for d, v in zip(descs, centre):
        if v is None or v != v:
            continue
        m[_name(d)] = float(v)
        r = catalog.role_from_desc(d)
        if r:
            m.setdefault(r, float(v))
    if "NDVI_dry" not in m:
        if "NDVI" not in m and "nir" in m and "red" in m:
            m["NDVI"] = float(_nd(m["nir"], m["red"]))
        if "MNDWI" not in m and "green" in m and "swir1" in m:
            m["MNDWI"] = float(_nd(m["green"], m["swir1"]))
        if "NDBI" not in m and "swir1" in m and "nir" in m:
            m["NDBI"] = float(_nd(m["swir1"], m["nir"]))
        if "VV" not in m and "vv" in m:
            m["VV"] = m["vv"]
    return m


def hint(descs, centre):
    m = means_by_name(descs, centre)
    if "NDVI_dry" in m:                              # multi-season stack
        g = lambda k, d: m.get(k, d)  # noqa: E731
        nd, ng, amp = g("NDVI_dry", 0.0), g("NDVI_green", 0.0), g("NDVI_amp", abs(g("NDVI_green", 0) - g("NDVI_dry", 0)))
        mw_d, mw_g, ndbi = g("MNDWI_dry", -0.5), g("MNDWI_green", -0.5), g("NDBI_dry", -0.2)
        vhd, vhw, vv = g("VH_dry", -16.0), g("VH_wet", -16.0), g("VV_dry", -10.0)
        if mw_d > 0.05 and ng < 0.3:
            return "water"
        if ng < 0.1 and mw_g > 0.0 and nd >= 0.45:
            return "flooded"
        if mw_g > 0.0 and nd < 0.4:
            return "wetland"
        if ng < 0.4 and ndbi > -0.05 and vv > -9:
            return "built"
        if ng < 0.25 and nd < 0.25:
            return "bare"
        if nd < 0.35 and amp >= 0.2:
            return "rice" if vhw < -18 else "crop"
        if nd < 0.3 and ng < 0.5:
            return "grass"
        if nd >= 0.72:
            return "evergreen" if vhd > -14.5 else "orchard"
        if nd >= 0.5 and amp < 0.25:
            return "orchard"
        if nd < 0.5 and amp >= 0.15:
            return "deciduous"
        return "shrub"
    nd = m.get("NDVI")                               # single image
    if nd is None:
        return "none"
    mw, nb, vv = m.get("MNDWI"), m.get("NDBI"), m.get("VV")
    if mw is not None and mw > 0.1 and nd < 0.25:
        return "water"
    if nd < 0.25:
        if (vv is not None and vv > -8) or (nb is not None and nb > 0.05):
            return "built"
        return "bare"
    if nd >= 0.65:
        return "forest"
    if nd >= 0.45:
        return "shrub"
    if nd >= 0.3:
        return "crop"
    return "grass"


def suggest_class(hint_key, classes):
    """classes: [(id, english_name, colour)] → id or None."""
    for kw in HINT_KEYWORDS.get(hint_key, ()):
        for cid, name, _ in classes:
            if kw in name.lower():
                return cid
    return None


# ====================================================================== examples / split
def examples(cluster_path, cid, n=10, seed=None):
    """Up to n random pixel centres of cluster `cid`: ([(x, y)], wkt)."""
    info = rio.RasterInfo(cluster_path)
    a = np.nan_to_num(rio.read_band(cluster_path, 1)).astype("int32")
    r, c = np.nonzero(a == int(cid))
    if r.size == 0:
        return [], info.wkt
    rng = np.random.default_rng(seed)
    pick = rng.choice(r.size, min(n, r.size), replace=False)
    gt = info.gt
    pts = [(float(gt[0] + (c[i] + 0.5) * gt[1] + (r[i] + 0.5) * gt[2]),
            float(gt[3] + (c[i] + 0.5) * gt[4] + (r[i] + 0.5) * gt[5])) for i in pick]
    return pts, info.wkt


def split_cluster(task, cluster_path, img_path, bands, cid, n, next_id, out_dir, seed=0):
    """Re-cluster the pixels of `cid` into n sub-clusters numbered next_id… Writes a new cluster
    raster. Returns dict(path, new_ids, centres (raw), areas)."""
    from .procs import _out
    info = rio.RasterInfo(cluster_path)
    lab = np.nan_to_num(rio.read_band(cluster_path, 1)).astype("int32")
    inside = lab == int(cid)
    if not inside.any():
        raise ValueError("Cluster %d has no pixels" % cid)
    X = np.stack([rio.read_band(img_path, b)[inside] for b in bands], 1).astype("float64")
    ok = np.isfinite(X).all(1)
    if ok.sum() < n:
        raise ValueError("Not enough valid pixels in cluster %d" % cid)
    Xo = X[ok]
    mu, sd = Xo.mean(0), Xo.std(0) + 1e-12
    rng = np.random.default_rng(seed)
    S = Xo[rng.choice(len(Xo), min(60000, len(Xo)), replace=False)]
    C, _, _ = R.kmeans((S - mu) / sd, int(n), 30, seed=seed)
    sub, _ = R.nearest_centre((Xo - mu) / sd, C)
    if task is not None:
        task.setProgress(70)
    vals = np.zeros(X.shape[0], "int32")             # pixels with NaN in a band keep no class
    vals[ok] = sub + int(next_id)
    new = lab.copy()
    new[inside] = vals
    out = _out(out_dir, "clusters_split")
    rio.write_array(out, new.astype("uint8"), info, gdal.GDT_Byte, 0)
    centres = []
    new_ids = []
    for j in range(int(n)):
        sel = sub == j
        if sel.any():
            centres.append(Xo[sel].mean(0).tolist())
            new_ids.append(int(next_id) + j)
    return dict(path=out, new_ids=new_ids, centres=centres,
                areas=R.class_areas(new, info.pixel_area))
