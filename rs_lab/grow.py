"""Click-to-grow ("magic wand") training areas.

Click one pixel of a field, a river or a forest: the pixels around it that are spectrally similar AND
connected to it are grown into a region, which becomes a training polygon of the chosen class.
Pure NumPy + GDAL (no SciPy), so it runs in any QGIS.

    mask, info = grow_region(path, bands, x, y, tolerance=1.0, max_pixels=2000)
    wkt = mask_to_wkt(mask, info)

Similarity = root-mean-square of the per-band differences from the seed, each band divided by its standard
deviation in the window, so bands with large DN ranges do not dominate. tolerance 1.0 = strict (only very
similar pixels), 2.0 = loose.
"""
import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()


def _read_window(path, bands, col, row, radius):
    ds = gdal.Open(path)
    W, H = ds.RasterXSize, ds.RasterYSize
    x0, y0 = max(0, col - radius), max(0, row - radius)
    x1, y1 = min(W, col + radius + 1), min(H, row + radius + 1)
    stack = []
    for b in bands:
        rb = ds.GetRasterBand(int(b))
        a = rb.ReadAsArray(x0, y0, x1 - x0, y1 - y0).astype("float64")
        nd = rb.GetNoDataValue()
        if nd is not None:
            a[a == nd] = np.nan
        stack.append(a)
    gt = ds.GetGeoTransform()
    wkt = ds.GetProjection()
    ds = None
    return np.stack(stack), (x0, y0), gt, wkt


def pixel_of(path, x, y):
    """(col, row) of map coordinate (x, y) in the raster's CRS, or None outside."""
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform()
    col = int(np.floor((x - gt[0]) / gt[1]))
    row = int(np.floor((y - gt[3]) / gt[5]))
    ok = 0 <= col < ds.RasterXSize and 0 <= row < ds.RasterYSize
    ds = None
    return (col, row) if ok else None


def grow_from_array(X, seed_rc, tolerance=1.0, max_pixels=2000):
    """X: (bands, h, w). seed_rc: (row, col) inside X. Returns a boolean region mask (h, w)."""
    nb, h, w = X.shape
    valid = np.isfinite(X).all(0)
    r, c = seed_rc
    if not valid[r, c]:
        return np.zeros((h, w), bool)
    r0, r1, c0, c1 = max(0, r - 1), min(h, r + 2), max(0, c - 1), min(w, c + 2)
    nb_seed = X[:, r0:r1, c0:c1].reshape(nb, -1)
    seed = np.nanmean(nb_seed, 1)                   # 3×3 mean: steadier than one pixel
    sd = np.array([np.nanstd(X[i][valid]) for i in range(nb)])
    sd[~np.isfinite(sd) | (sd <= 0)] = 1.0
    with np.errstate(invalid="ignore"):
        d = np.sqrt(np.nanmean(((X - seed[:, None, None]) / sd[:, None, None]) ** 2, 0))
    similar = valid & (d <= float(tolerance))
    similar[r, c] = True
    region = np.zeros((h, w), bool)
    region[r, c] = True
    front = region.copy()
    n = 1
    while front.any() and n < max_pixels:
        grown = np.zeros_like(front)               # 4-neighbour dilation of the front
        grown[1:, :] |= front[:-1, :]
        grown[:-1, :] |= front[1:, :]
        grown[:, 1:] |= front[:, :-1]
        grown[:, :-1] |= front[:, 1:]
        new = grown & similar & ~region
        k = int(new.sum())
        if k == 0:
            break
        if n + k > max_pixels:                     # keep the pixels closest to the seed
            idx = np.flatnonzero(new)
            keep = idx[np.argsort(d.ravel()[idx])[:max_pixels - n]]
            new = np.zeros_like(new)
            new.ravel()[keep] = True
            k = len(keep)
        region |= new
        front = new
        n += k
    return region


def fill_small_holes(mask, max_frac=0.05):
    """Fill holes enclosed by the region (noisy pixels inside a field) when they are small in total
    (<= max_frac of the region); otherwise fill only single isolated pixels."""
    inv = ~mask
    outside = np.zeros_like(mask)
    outside[0, :], outside[-1, :], outside[:, 0], outside[:, -1] = inv[0, :], inv[-1, :], inv[:, 0], inv[:, -1]
    while True:                                    # flood the background from the window border
        g = outside.copy()
        g[1:, :] |= outside[:-1, :]
        g[:-1, :] |= outside[1:, :]
        g[:, 1:] |= outside[:, :-1]
        g[:, :-1] |= outside[:, 1:]
        g &= inv
        if (g == outside).all():
            break
        outside = g
    holes = inv & ~outside
    if holes.sum() <= max_frac * mask.sum():
        return mask | holes
    p = np.pad(mask, 1)
    single = holes & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return mask | single


def pixel_area_m2(path, y=None):
    """Area of one pixel in m² (geographic CRS: approximated at latitude y)."""
    ds = gdal.Open(path)
    gt, wkt = ds.GetGeoTransform(), ds.GetProjection()
    ds = None
    a = abs(gt[1] * gt[5])
    srs = osr.SpatialReference(wkt=wkt) if wkt else None
    if srs is not None and srs.IsGeographic():
        lat = y if y is not None else gt[3]
        return a * 111320.0 ** 2 * max(0.05, np.cos(np.radians(lat)))
    if srs is not None and srs.IsProjected():
        return a * srs.GetLinearUnits() ** 2
    return a


def trim(mask):
    """Remove the outer ring (4-neighbour erosion) if at least half of the region survives."""
    p = np.pad(mask, 1)
    e = mask & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return e if e.sum() >= 0.5 * mask.sum() else mask


def grow_region(path, bands, x, y, tolerance=1.0, max_pixels=2000, radius=None, max_ha=None, trim_edge=True):
    """Grow from map point (x, y) (raster CRS). Returns (mask, info) or (None, reason).
    max_ha (if given) sets max_pixels from the pixel size. trim_edge drops the outer ring of pixels of larger
    regions: edge pixels are often mixed (half field, half road), which blurs the training signature."""
    pc = pixel_of(path, x, y)
    if pc is None:
        return None, "outside"
    col, row = pc
    if max_ha:
        max_pixels = int(max(4, min(250000, max_ha * 10000.0 / max(pixel_area_m2(path, y), 1e-9))))
    radius = int(radius or max(30, min(400, np.sqrt(max_pixels) * 3)))
    X, (x0, y0), gt, wkt = _read_window(path, bands, col, row, radius)
    seed = (row - y0, col - x0)
    if not np.isfinite(X[:, seed[0], seed[1]]).all():
        return None, "nodata"
    m = grow_from_array(X, seed, tolerance, max_pixels)
    capped = int(m.sum()) >= max_pixels
    if m.sum() > 8:
        m = fill_small_holes(m)
    if trim_edge and m.sum() >= 30:
        m = trim(m)
    info = dict(gt=(gt[0] + x0 * gt[1], gt[1], gt[2], gt[3] + y0 * gt[5], gt[4], gt[5]), wkt=wkt,
                n=int(m.sum()), capped=capped, pixel_area=abs(gt[1] * gt[5]))
    return m, info


def mask_to_wkt(mask, info):
    """Polygon (WKT, raster CRS) of the region; the largest part if it falls apart."""
    h, w = mask.shape
    mem = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(info["gt"])
    if info.get("wkt"):
        mem.SetProjection(info["wkt"])
    b = mem.GetRasterBand(1)
    b.WriteArray(mask.astype("uint8"))
    srs = osr.SpatialReference(wkt=info["wkt"]) if info.get("wkt") else None
    ods = ogr.GetDriverByName("Memory").CreateDataSource("grow")
    ol = ods.CreateLayer("p", srs, ogr.wkbPolygon)
    ol.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    gdal.Polygonize(b, b, ol, 0, [], callback=None)
    best, area = None, -1.0
    for f in ol:
        g = f.GetGeometryRef()
        if g is not None and g.GetArea() > area:
            best, area = g.ExportToWkt(), g.GetArea()
    mem, ods = None, None
    return best
