"""Processing jobs (run inside background tasks). Each returns a plain dict of results."""
import os

import numpy as np
from osgeo import gdal, ogr, osr

from . import catalog
from . import raster_io as rio
from . import rs_core as R


def _out(out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name)
    p, i = base + ".tif", 1
    while os.path.exists(p):
        p = "%s_%d.tif" % (base, i)
        i += 1
    return p


# ------------------------------------------------------------------ enhancement
def enhance(task, path, rgb, method, out_dir, p_lo=2.0, p_hi=98.0, n_std=2.0, gamma=1.0, name="enhanced"):
    info = rio.RasterInfo(path)
    out = []
    ranges = []
    for k, b in enumerate(rgb):
        a = rio.read_band(path, b)
        if method == "minmax":
            s, r = R.minmax_stretch(a)
        elif method == "std":
            s, r = R.stddev_stretch(a, n_std)
        elif method == "histeq":
            s, r = R.histogram_equalize(a), None
        else:
            s, r = R.percent_clip_stretch(a, p_lo, p_hi)
        if gamma != 1.0:
            s = R.gamma_correct(s, gamma)
        s[~np.isfinite(a)] = 0
        out.append(s)
        ranges.append(r)
        task.setProgress(30 * (k + 1))
    p = _out(out_dir, name)
    rio.write_array(p, np.stack(out), info, gdal.GDT_Byte, 0,
                    descs=["R: " + info.descs[rgb[0] - 1], "G: " + info.descs[rgb[1] - 1],
                           "B: " + info.descs[rgb[2] - 1]])
    return dict(path=p, ranges=ranges)


def stats_table(task, path):
    info = rio.RasterInfo(path)
    rows = []
    for i in range(1, info.count + 1):
        st = R.band_stats(rio.read_band(path, i))
        rows.append([info.descs[i - 1], st["min"], st["max"], st["mean"], st["std"], st.get("median", np.nan)])
        task.setProgress(100.0 * i / info.count)
    return dict(rows=rows)


def histograms(task, path, bands, nbins=128):
    out = []
    info = rio.RasterInfo(path)
    for b in bands:
        h, e = R.histogram(rio.read_band(path, b), nbins)
        out.append((info.descs[b - 1], h, e))
    return dict(hists=out)


# ------------------------------------------------------------------ indices
def _isbn(t):
    return len(t) > 1 and t[0] == "b" and t[1:].isdigit()


def roles_needed(formula):
    import re
    toks = set(re.findall(r"[a-z_][a-z0-9_]*", formula))
    return [t for t in toks if t in catalog.ALL_ROLES or t in ("vv_lin", "vh_lin")] + \
           [t for t in toks if re.fullmatch(r"b\d+", t)]


def compute_indices(task, path, roles, names, out_dir, custom=None, scale_factor=1.0, offset=0.0,
                    otsu=False):
    """roles: dict role -> band number. custom: (name, expression) using roles or b1..bn."""
    info = rio.RasterInfo(path)
    jobs = [(n, catalog.INDICES[n][0]) for n in names]
    if custom and custom[1].strip():
        jobs.append((custom[0] or "custom", custom[1]))
    results = []
    for j, (name, formula) in enumerate(jobs):
        need = roles_needed(formula)
        missing = [r for r in need if not _isbn(r) and r not in roles and
                   not (r in ("vv_lin", "vh_lin") and r[:2] in roles)]
        if missing:
            results.append(dict(name=name, error="missing band role(s): %s" % ", ".join(missing)))
            continue
        p = _out(out_dir, name)
        ds = rio.create_like(p, info, 1, gdal.GDT_Float32, -9999.0, [name])
        band = ds.GetRasterBand(1)
        acc = []
        for win in rio.iter_blocks(info):
            vals = {}
            for r in need:
                if _isbn(r):
                    vals[r] = rio.read_band(path, int(r[1:]), win) * scale_factor + offset
                elif r in ("vv_lin", "vh_lin"):
                    vals[r] = R.db_to_linear(rio.read_band(path, roles[r[:2]], win))
                else:
                    a = rio.read_band(path, roles[r], win)
                    vals[r] = a * scale_factor + offset if r not in ("vv", "vh", "tir") else a
            res = R.band_math(formula, vals)
            if res.ndim == 0:
                res = np.full((win[3], win[2]), float(res))
            band.WriteArray(np.where(np.isfinite(res), res, -9999.0).astype("float32"), 0, win[1])
            v = res[np.isfinite(res)]
            if v.size:
                acc.append(v[:: max(1, v.size // 20000)])
        ds.FlushCache()
        ds = None
        v = np.concatenate(acc) if acc else np.array([np.nan])
        r = dict(name=name, path=p, min=float(np.nanmin(v)), max=float(np.nanmax(v)),
                 mean=float(np.nanmean(v)), p2=float(np.nanpercentile(v, 2)), p98=float(np.nanpercentile(v, 98)))
        if otsu and v.size > 10:
            t = R.otsu_threshold(v)
            r["otsu"] = t
            a = rio.read_band(p, 1)
            mask = np.where(np.isfinite(a), (a > t).astype("uint8") + 1, 0)
            mp = _out(out_dir, name + "_otsu_mask")
            rio.write_array(mp, mask, info, gdal.GDT_Byte, 0)
            r["mask"] = mp
        results.append(r)
        task.setProgress(100.0 * (j + 1) / len(jobs))
    return dict(results=results)


# ------------------------------------------------------------------ filters, PCA
def spatial_filter(task, path, band, kind, out_dir, k=3, sigma=1.0):
    info = rio.RasterInfo(path)
    a = rio.read_band(path, band)
    nan = ~np.isfinite(a)
    fill = np.where(nan, np.nanmean(a) if (~nan).any() else 0, a)
    if kind == "mean":
        r = R.mean_filter(fill, k)
    elif kind == "median":
        r = R.median_filter(fill, k)
    elif kind == "gaussian":
        r = R.gaussian_filter(fill, sigma)
    elif kind == "sobel":
        r = R.sobel(fill)
    elif kind == "laplacian":
        r = R.convolve(fill, R.LAPLACIAN)
    elif kind == "highpass":
        r = R.convolve(fill, R.HIGHPASS)
    elif kind == "emboss":
        r = R.convolve(fill, R.EMBOSS)
    else:
        raise ValueError(kind)
    r[nan] = np.nan
    p = _out(out_dir, "%s_b%d_%s" % (kind, band, k if kind in ("mean", "median") else ""))
    rio.write_array(p, r.astype("float32"), info, gdal.GDT_Float32, -9999.0, ["%s filter of band %d" % (kind, band)])
    return dict(path=p)


def pca_job(task, path, bands, n, out_dir):
    info = rio.RasterInfo(path)
    X = rio.sample_pixels(path, bands, 100000)
    mu, w, V, exp = R.pca(X)
    n = min(n, len(bands))
    p = _out(out_dir, "PCA")
    ds = rio.create_like(p, info, n, gdal.GDT_Float32, -9999.0, ["PC%d (%.1f%%)" % (i + 1, exp[i]) for i in range(n)])
    for win in rio.iter_blocks(info):
        A = rio.read_stack(path, bands, win)
        Xb = A.reshape(len(bands), -1).T
        ok = np.isfinite(Xb).all(1)
        Y = np.full((Xb.shape[0], n), -9999.0)
        if ok.any():
            Y[ok] = R.pca_transform(Xb[ok], mu, V, n)
        for i in range(n):
            ds.GetRasterBand(i + 1).WriteArray(Y[:, i].reshape(win[3], win[2]).astype("float32"), 0, win[1])
    ds.FlushCache()
    ds = None
    loadings = [[info.descs[b - 1]] + [float(V[k, i]) for i in range(n)] for k, b in enumerate(bands)]
    return dict(path=p, eig=w.tolist(), explained=exp.tolist(), loadings=loadings, n=n)


# ------------------------------------------------------------------ classification
def _predict_to_file(task, path, bands, fn, out, info, start=10, span=85):
    ds = rio.create_like(out, info, 1, gdal.GDT_Byte, 0, ["class"])
    b = ds.GetRasterBand(1)
    blocks = list(rio.iter_blocks(info, 256))
    for i, win in enumerate(blocks):
        if task.isCanceled():
            return False
        A = rio.read_stack(path, bands, win)
        X = A.reshape(len(bands), -1).T
        ok = np.isfinite(X).all(1)
        y = np.zeros(X.shape[0], "uint8")
        if ok.any():
            y[ok] = fn(X[ok]).astype("uint8")
        b.WriteArray(y.reshape(win[3], win[2]), 0, win[1])
        task.setProgress(start + span * (i + 1) / len(blocks))
    ds.FlushCache()
    ds = None
    return True


def unsupervised(task, path, bands, method, k, out_dir, iters=20, sample=60000, k_min=2, k_max=20,
                 majority=0, seed=0):
    info = rio.RasterInfo(path)
    X = rio.sample_pixels(path, bands, sample, seed)
    if X.shape[0] < k:
        raise ValueError("Not enough valid pixels")
    # standardise so bands with large ranges do not dominate
    mu, sd = X.mean(0), X.std(0) + 1e-12
    Z = (X - mu) / sd

    def cb(i, n):
        task.setProgress(10.0 * i / n)
    if method == "isodata":
        C, _, inertia = R.isodata(Z, k, k_min, k_max, iters, seed=seed, callback=cb)
    else:
        C, _, inertia = R.kmeans(Z, k, iters, seed=seed, callback=cb)
    Cr = C * sd + mu
    order = np.argsort(Cr.mean(1))
    C, Cr = C[order], Cr[order]
    out = _out(out_dir, method)

    def fn(Xb):
        lab, _ = R.nearest_centre((Xb - mu) / sd, C)
        return lab + 1
    if not _predict_to_file(task, path, bands, fn, out, info):
        return None
    if majority and majority > 1:
        a = rio.read_band(out, 1)
        a = np.nan_to_num(a).astype("int32")
        a = R.majority_filter(a, majority, 0)
        rio.write_array(out, a.astype("uint8"), info, gdal.GDT_Byte, 0)
    cls = rio.read_band(out, 1)
    areas = R.class_areas(np.nan_to_num(cls).astype("int32"), info.pixel_area)
    return dict(path=out, centres=Cr.tolist(), k=len(C), inertia=inertia, areas=areas,
                bands=[info.descs[b - 1] for b in bands])


def training_samples(path, bands, train_vec, train_layer, field, max_per_class=20000, seed=0):
    info = rio.RasterInfo(path)
    lab = rio.rasterize_layer(train_vec, train_layer, field, info)
    rng = np.random.default_rng(seed)
    rows, cols, ys = [], [], []
    for c in np.unique(lab):
        if c <= 0:
            continue
        r, col = np.nonzero(lab == c)
        if r.size > max_per_class:
            pick = rng.choice(r.size, max_per_class, replace=False)
            r, col = r[pick], col[pick]
        rows.append(r)
        cols.append(col)
        ys.append(np.full(r.size, c))
    if not ys:
        raise ValueError("No training pixels found. Do the polygons overlap the image and have class ids?")
    rows, cols, y = np.concatenate(rows), np.concatenate(cols), np.concatenate(ys)
    X = np.stack([rio.read_band(path, b)[rows, cols] for b in bands], 1)
    ok = np.isfinite(X).all(1)
    Xs, ys = [X[ok]], [y[ok]]
    if not Xs:
        raise ValueError("No training pixels found. Do the polygons overlap the image and have class ids?")
    return np.vstack(Xs), np.concatenate(ys)


def signatures(task, path, bands, train_vec, train_layer, field):
    X, y = training_samples(path, bands, train_vec, train_layer, field)
    classes = np.unique(y)
    means = [X[y == c].mean(0).tolist() for c in classes]
    stds = [X[y == c].std(0).tolist() for c in classes]
    counts = [int((y == c).sum()) for c in classes]
    cl, jm = R.separability(X, y) if len(classes) > 1 else (classes, np.zeros((1, 1)))
    info = rio.RasterInfo(path)
    return dict(classes=classes.tolist(), means=means, stds=stds, counts=counts, jm=jm.tolist(),
                bands=[info.descs[b - 1] for b in bands])


def supervised(task, path, bands, train_vec, train_layer, field, clf_key, params, out_dir, majority=0,
               path2=None):
    if clf_key in ("rf", "svm", "knn"):
        from . import deps
        deps.guard_multiprocessing()
    info = rio.RasterInfo(path)
    X, y = training_samples(path, bands, train_vec, train_layer, field)
    task.setProgress(5)
    clf = R.make_classifier(clf_key, **params).fit(X, y)
    task.setProgress(10)
    out = _out(out_dir, "class_" + clf_key)
    if not _predict_to_file(task, path, bands, clf.predict, out, info):
        return None
    if majority and majority > 1:
        a = np.nan_to_num(rio.read_band(out, 1)).astype("int32")
        a = R.majority_filter(a, majority, 0)
        rio.write_array(out, a.astype("uint8"), info, gdal.GDT_Byte, 0)
    cls = np.nan_to_num(rio.read_band(out, 1)).astype("int32")
    res = dict(path=out, areas=R.class_areas(cls, info.pixel_area), classifier=clf.name,
               n_train=int(len(y)), train_counts={int(c): int((y == c).sum()) for c in np.unique(y)},
               bands=[info.descs[b - 1] for b in bands])
    # resubstitution accuracy on training pixels (optimistic — for teaching only)
    lab, cm = R.confusion_matrix(y, clf.predict(X))
    res["train_oa"] = float(R.accuracy_metrics(cm)["oa"])
    if path2:                      # same trained model applied to another date (for from → to change)
        info2 = rio.RasterInfo(path2)
        if info2.count < max(bands):
            raise ValueError("The second image has %d bands; the model needs band %d." % (info2.count, max(bands)))
        out2 = _out(out_dir, "class_%s_date2" % clf_key)
        if not _predict_to_file(task, path2, bands, clf.predict, out2, info2, 55, 40):
            return None
        if majority and majority > 1:
            a2 = np.nan_to_num(rio.read_band(out2, 1)).astype("int32")
            rio.write_array(out2, R.majority_filter(a2, majority, 0).astype("uint8"), info2, gdal.GDT_Byte, 0)
        c2 = np.nan_to_num(rio.read_band(out2, 1)).astype("int32")
        res["path2"] = out2
        res["areas2"] = R.class_areas(c2, info2.pixel_area)
    if hasattr(clf, "m") and hasattr(clf.m, "oob_score_"):
        res["oob"] = float(clf.m.oob_score_)
        imp = clf.importances()
        if imp is not None:
            res["importance"] = [(info.descs[b - 1], float(v)) for b, v in zip(bands, imp)]
    return res


def accuracy(task, class_path, ref_vec, ref_layer, field):
    """Confusion matrix of a classified map against reference data.

    Points are sampled exactly at their pixel (several points in one pixel all count); polygons are
    rasterized. Returns the metrics, or dict(problem=..., diag=...) explaining why nothing matched."""
    info = rio.RasterInfo(class_path)
    cls = np.nan_to_num(rio.read_band(class_path, 1)).astype("int32")
    src = ogr.Open(ref_vec)
    lyr = src.GetLayerByName(ref_layer) if ref_layer else src.GetLayer(0)
    gtype = ogr.GT_Flatten(lyr.GetGeomType())
    diag = dict(features=lyr.GetFeatureCount(), no_value=0, outside=0, unclassified=0, used=0)
    if gtype in (ogr.wkbPoint, ogr.wkbMultiPoint):
        gt = info.gt
        refs, maps = [], []
        for f in lyr:
            v = f.GetField(field)
            try:
                v = int(v) if v is not None and str(v).strip() != "" else 0
            except ValueError:
                v = 0
            if v <= 0:
                diag["no_value"] += 1
                continue
            g = f.GetGeometryRef()
            if g is None:
                continue
            pts = [g.GetGeometryRef(i) for i in range(g.GetGeometryCount())] if g.GetGeometryCount() else [g]
            for pt in pts:
                col = int((pt.GetX() - gt[0]) / gt[1])
                row = int((pt.GetY() - gt[3]) / gt[5])
                if not (0 <= col < info.xsize and 0 <= row < info.ysize):
                    diag["outside"] += 1
                    continue
                c = int(cls[row, col])
                if c <= 0:
                    diag["unclassified"] += 1
                    continue
                refs.append(v)
                maps.append(c)
        src = None
        ref_a, map_a = np.array(refs, "int64"), np.array(maps, "int64")
    else:
        src = None
        ref = rio.rasterize_layer(ref_vec, ref_layer, field, info)
        m = (ref > 0) & (cls > 0)
        diag["no_value"] = int(diag["features"]) if not (ref > 0).any() else 0
        diag["unclassified"] = int(((ref > 0) & (cls <= 0)).sum())
        ref_a, map_a = ref[m].astype("int64"), cls[m].astype("int64")
    diag["used"] = int(ref_a.size)
    if ref_a.size == 0:
        if diag["features"] == 0:
            problem = "empty"
        elif diag["no_value"] >= diag["features"]:
            problem = "no_value"
        elif diag["outside"] and not diag["unclassified"]:
            problem = "outside"
        else:
            problem = "unclassified"
        return dict(problem=problem, diag=diag)
    labels, cm = R.confusion_matrix(ref_a, map_a)
    met = R.accuracy_metrics(cm)
    out = dict(labels=labels.tolist(), cm=cm.tolist(), oa=met["oa"], kappa=met["kappa"],
               ua=met["ua"].tolist(), pa=met["pa"].tolist(), f1=met["f1"].tolist(), n=met["n"], diag=diag)
    if gtype in (ogr.wkbPoint, ogr.wkbMultiPoint):   # point sample → area-adjusted estimates (Olofsson 2014)
        vals, cnt = np.unique(cls[cls > 0], return_counts=True)
        px = dict(zip(vals.tolist(), cnt.tolist()))
        adj = R.area_adjusted(cm, [px.get(int(v), 0) for v in labels], info.pixel_area)
        if adj:
            out["adjusted"] = adj
    return out


def sample_points(task, class_path, n_per_class, out_gpkg, seed=0):
    info = rio.RasterInfo(class_path)
    cls = np.nan_to_num(rio.read_band(class_path, 1)).astype("int32")
    r, c, k = R.stratified_sample(cls, n_per_class, 0, seed)
    gt = info.gt
    srs = osr.SpatialReference(wkt=info.wkt)
    drv = ogr.GetDriverByName("GPKG")
    if os.path.exists(out_gpkg):
        drv.DeleteDataSource(out_gpkg)
    ds = drv.CreateDataSource(out_gpkg)
    lyr = ds.CreateLayer("validation_points", srs, ogr.wkbPoint)
    for nm, t in (("pid", ogr.OFTInteger), ("map_class", ogr.OFTInteger), ("ref_class", ogr.OFTInteger),
                  ("note", ogr.OFTString)):
        lyr.CreateField(ogr.FieldDefn(nm, t))
    for i, (rr, cc, kk) in enumerate(zip(r, c, k)):
        x = gt[0] + (cc + 0.5) * gt[1] + (rr + 0.5) * gt[2]
        yy = gt[3] + (cc + 0.5) * gt[4] + (rr + 0.5) * gt[5]
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("pid", i + 1)
        f.SetField("map_class", int(kk))
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint_2D(float(x), float(yy))
        f.SetGeometry(g)
        lyr.CreateFeature(f)
    ds = None
    return dict(path=out_gpkg, n=int(len(r)))


# ------------------------------------------------------------------ change detection
def _align(src, ref_info, tmp_dir, resample="near"):
    out = os.path.join(tmp_dir, "aligned_%d.vrt" % abs(hash(src)))
    xmin = ref_info.gt[0]
    ymax = ref_info.gt[3]
    xmax = xmin + ref_info.gt[1] * ref_info.xsize
    ymin = ymax + ref_info.gt[5] * ref_info.ysize
    gdal.Warp(out, src, format="VRT", dstSRS=ref_info.wkt, outputBounds=(xmin, ymin, xmax, ymax),
              width=ref_info.xsize, height=ref_info.ysize, resampleAlg=resample)
    return out


def change_difference(task, p1, b1, p2, b2, k_std, out_dir):
    info = rio.RasterInfo(p1)
    a1 = rio.read_band(p1, b1)
    p2a = _align(p2, info, out_dir, "bilinear")
    a2 = rio.read_band(p2a, b2)
    d = a2 - a1
    cls, (lo, hi) = R.difference_threshold(d, k_std)
    pd = _out(out_dir, "difference")
    rio.write_array(pd, d.astype("float32"), info, gdal.GDT_Float32, -9999.0, ["date2 - date1"])
    pc = _out(out_dir, "change_classes")
    rio.write_array(pc, cls, info, gdal.GDT_Byte, 0)
    areas = R.class_areas(cls.astype("int32"), info.pixel_area)
    return dict(diff=pd, cls=pc, lo=float(lo), hi=float(hi), areas=areas)


def change_postclass(task, c1, c2, out_dir):
    info = rio.RasterInfo(c1)
    a = np.nan_to_num(rio.read_band(c1, 1)).astype("int32")
    b = np.nan_to_num(rio.read_band(_align(c2, info, out_dir, "near"), 1)).astype("int32")
    labs, cm = R.change_matrix(a, b)
    code = R.change_code(a, b, int(max(labs.max() if labs.size else 0, 1)))
    p = _out(out_dir, "change_from_to")
    rio.write_array(p, code, info, gdal.GDT_Int32, 0, ["from*1000 + to"])
    changed = (a != b) & (a > 0) & (b > 0)
    return dict(path=p, labels=labs.tolist(), cm=cm.tolist(), pixel_area=info.pixel_area,
                changed_ha=float(changed.sum() * info.pixel_area / 1e4),
                codes=sorted(set(int(v) for v in np.unique(code) if v > 0)))


# ------------------------------------------------------------------ clean-up & vectorize
def sieve_vectorize(task, class_path, mmu_ha, eight, polygons, out_dir, names=None):
    """Sieve a classified map to a minimum mapping unit, then (optionally) polygonize it to a
    GeoPackage with class_id, class_name and area_ha."""
    info = rio.RasterInfo(class_path)
    min_px = int(round(mmu_ha * 1e4 / info.pixel_area)) if mmu_ha > 0 else 0
    src = np.nan_to_num(rio.read_band(class_path, 1)).astype("int32")
    out = _out(out_dir, "sieved")
    rio.write_array(out, src.astype("int32"), info, gdal.GDT_Int32, 0)
    if task is not None:
        task.setProgress(10)
    if min_px > 1:
        ds = gdal.Open(out, gdal.GA_Update)
        band = ds.GetRasterBand(1)
        gdal.SieveFilter(band, band.GetMaskBand(), band, min_px, 8 if eight else 4)
        band.FlushCache()
        ds = None
    res = rio.read_band(out, 1)
    changed = int((np.nan_to_num(res).astype("int32") != src).sum())
    result = dict(path=out, min_px=min_px, changed=changed)
    if task is not None:
        task.setProgress(50)
    if polygons:
        gpkg = out[:-4] + "_polygons.gpkg"
        drv = ogr.GetDriverByName("GPKG")
        if os.path.exists(gpkg):
            drv.DeleteDataSource(gpkg)
        vds = drv.CreateDataSource(gpkg)
        srs = osr.SpatialReference(wkt=info.wkt) if info.wkt else None
        lyr = vds.CreateLayer("classes", srs, ogr.wkbPolygon)
        lyr.CreateField(ogr.FieldDefn("class_id", ogr.OFTInteger))
        rds = gdal.Open(out)
        b = rds.GetRasterBand(1)
        gdal.Polygonize(b, b.GetMaskBand(), lyr, 0, ["8CONNECTED=8"] if eight else [])
        rds = None
        for fn, t in (("class_name", ogr.OFTString), ("area_ha", ogr.OFTReal)):
            lyr.CreateField(ogr.FieldDefn(fn, t))
        geo = srs is not None and srs.IsGeographic()
        k = info.pixel_area / abs(info.gt[1] * info.gt[5]) if geo else 1.0   # deg² → m² (approx.)
        rows = []
        for f in lyr:
            g = f.GetGeometryRef()
            rows.append((f.GetFID(), f.GetField("class_id"), (g.GetArea() * k / 1e4) if g is not None else 0.0))
        lyr.ResetReading()
        lyr.StartTransaction()
        for fid, cid, area in rows:
            f = lyr.GetFeature(fid)
            f.SetField("class_name", str((names or {}).get(cid, cid)))
            f.SetField("area_ha", float(area))
            lyr.SetFeature(f)
        lyr.CommitTransaction()
        n = len(rows)
        vds = None
        result.update(gpkg=gpkg, n_poly=n)
    return result


# ------------------------------------------------------------------ sample design (from the GEE LULC Toolkit)
def cochran_n(p=0.85, e=0.05, z=1.96, buffer_pct=10.0):
    """Cochran sample size n = z² p (1-p) / e², plus a buffer for unusable points."""
    n = z * z * p * (1 - p) / (e * e)
    return int(round(n * (1 + buffer_pct / 100.0)))


def allocate(n_target, weights, min_per_class, equal=False):
    """Split n_target over strata: proportional to area (largest-remainder rounding) or equal; rare
    strata are raised to min_per_class (Olofsson et al. 2014), so the total can end up above n_target."""
    import math
    k = len(weights)
    if k == 0:
        return []
    if equal:
        base, rem = divmod(n_target, k)
        raw = [base + (1 if i < rem else 0) for i in range(k)]
    else:
        tot = float(sum(weights)) or 1.0
        exact = [n_target * w / tot for w in weights]
        raw = [int(math.floor(v)) for v in exact]
        for i in sorted(range(k), key=lambda i: exact[i] - raw[i], reverse=True)[:max(0, n_target - sum(raw))]:
            raw[i] += 1
    return [max(min_per_class, v) for v in raw]


def _write_points(out_gpkg, layer, info, rows, cols, fields):
    """fields: list of (name, ogr type, list of values)."""
    srs = osr.SpatialReference(wkt=info.wkt)
    drv = ogr.GetDriverByName("GPKG")
    if os.path.exists(out_gpkg):
        drv.DeleteDataSource(out_gpkg)
    ds = drv.CreateDataSource(out_gpkg)
    lyr = ds.CreateLayer(layer, srs, ogr.wkbPoint)
    for nm, t, _ in fields:
        lyr.CreateField(ogr.FieldDefn(nm, t))
    gt = info.gt
    lyr.StartTransaction()
    for i, (rr, cc) in enumerate(zip(rows, cols)):
        f = ogr.Feature(lyr.GetLayerDefn())
        for nm, _, vals in fields:
            v = vals[i]
            if v is not None:
                f.SetField(nm, v.item() if hasattr(v, "item") else v)
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint_2D(float(gt[0] + (cc + 0.5) * gt[1] + (rr + 0.5) * gt[2]),
                      float(gt[3] + (cc + 0.5) * gt[4] + (rr + 0.5) * gt[5]))
        f.SetGeometry(g)
        lyr.CreateFeature(f)
    lyr.CommitTransaction()
    ds = None


def sample_points_design(task, class_path, design, n_fixed, n_total, min_pc, out_gpkg, seed=0):
    """Validation points stratified by map class. design: 'fixed' (n_fixed per class), 'prop' (Cochran total
    proportional to class area, min_pc floor) or 'equal' (Cochran total split equally)."""
    info = rio.RasterInfo(class_path)
    cls = np.nan_to_num(rio.read_band(class_path, 1)).astype("int32")
    vals, cnt = np.unique(cls[cls > 0], return_counts=True)
    if design == "fixed":
        per = [n_fixed] * len(vals)
    else:
        per = allocate(n_total, cnt.tolist(), min_pc, equal=(design == "equal"))
    rng = np.random.default_rng(seed)
    rr, cc, kk = [], [], []
    for v, n in zip(vals, per):
        r, c = np.nonzero(cls == v)
        pick = rng.choice(r.size, size=min(int(n), r.size), replace=False)
        rr += r[pick].tolist()
        cc += c[pick].tolist()
        kk += [int(v)] * len(pick)
    order = rng.permutation(len(rr))          # mixed order, so labelling is "blind" to the map class
    rr, cc, kk = [rr[i] for i in order], [cc[i] for i in order], [kk[i] for i in order]
    n = len(rr)
    _write_points(out_gpkg, "validation_points", info, rr, cc,
                  [("pid", ogr.OFTInteger, list(range(1, n + 1))), ("map_class", ogr.OFTInteger, kk),
                   ("ref_class", ogr.OFTInteger, [None] * n), ("note", ogr.OFTString, [None] * n)])
    return dict(path=out_gpkg, n=n, per_class={int(v): int(min(p, c)) for v, p, c in zip(vals, per, cnt)})


def training_from_reference(task, image_path, ref_path, mapping, n_per_class, out_gpkg, pure=True, seed=0):
    """Training points labelled automatically from a reference land-cover map (e.g. ESA WorldCover):
    reference resampled to the image grid, recoded with `mapping` {ref value: class id}, stratified random
    points per class; pure=True keeps only pixels whose 3×3 neighbours have the same class (fewer edge errors)."""
    import tempfile
    info = rio.RasterInfo(image_path)
    tmp = tempfile.mkdtemp(prefix="rslab_ref_")
    ref = np.nan_to_num(rio.read_band(_align(ref_path, info, tmp, "mode"), 1)).astype("int32")
    lut = np.zeros(max(int(ref.max()), max(mapping)) + 1, "int32")
    for k, v in mapping.items():
        lut[int(k)] = int(v)
    lab = lut[np.clip(ref, 0, lut.size - 1)]
    valid = np.isfinite(rio.read_band(image_path, 1))
    lab[~valid] = 0
    if task is not None:
        task.setProgress(40)
    if pure:
        p = np.pad(lab, 1, mode="edge")
        same = np.ones(lab.shape, bool)
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                same &= p[dy:dy + lab.shape[0], dx:dx + lab.shape[1]] == lab
        lab = np.where(same, lab, 0)
    rng = np.random.default_rng(seed)
    rr, cc, kk = [], [], []
    for v in np.unique(lab[lab > 0]):
        r, c = np.nonzero(lab == v)
        pick = rng.choice(r.size, size=min(n_per_class, r.size), replace=False)
        rr += r[pick].tolist()
        cc += c[pick].tolist()
        kk += [int(v)] * len(pick)
    _write_points(out_gpkg, "training", info, rr, cc, [("class_id", ogr.OFTInteger, kk)])
    counts = {int(v): int((np.array(kk) == v).sum()) for v in set(kk)}
    return dict(path=out_gpkg, n=len(kk), counts=counts)
