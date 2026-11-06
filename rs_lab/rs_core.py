"""Pure-NumPy remote sensing algorithms used for teaching.

Every function works on plain arrays so students (and teachers) can read the
code and see exactly what each method does.  No QGIS / GDAL imports here.
Arrays: images are (bands, rows, cols); pixel matrices are (n_pixels, bands).
"""
import numpy as np

try:
    from numpy.lib.stride_tricks import sliding_window_view as _swv
except Exception:  # numpy < 1.20
    _swv = None


# ===================================================================== enhancement
def _valid(a, nodata=None):
    m = np.isfinite(a)
    if nodata is not None:
        m &= a != nodata
    return m


def linear_stretch(a, lo, hi):
    """Map [lo, hi] linearly to 0..255 (values outside are saturated)."""
    a = a.astype("float64")
    if hi <= lo:
        hi = lo + 1e-9
    out = (a - lo) / (hi - lo) * 255.0
    return np.clip(np.nan_to_num(out, nan=0), 0, 255).astype("uint8")


def minmax_stretch(a, nodata=None):
    v = a[_valid(a, nodata)]
    if v.size == 0:
        return np.zeros(a.shape, "uint8"), (0, 0)
    lo, hi = float(v.min()), float(v.max())
    return linear_stretch(a, lo, hi), (lo, hi)


def percent_clip_stretch(a, p_lo=2.0, p_hi=98.0, nodata=None):
    v = a[_valid(a, nodata)]
    if v.size == 0:
        return np.zeros(a.shape, "uint8"), (0, 0)
    lo, hi = np.percentile(v, [p_lo, p_hi])
    return linear_stretch(a, lo, hi), (float(lo), float(hi))


def stddev_stretch(a, n_std=2.0, nodata=None):
    v = a[_valid(a, nodata)]
    if v.size == 0:
        return np.zeros(a.shape, "uint8"), (0, 0)
    m, s = float(v.mean()), float(v.std())
    lo, hi = m - n_std * s, m + n_std * s
    return linear_stretch(a, lo, hi), (lo, hi)


def histogram_equalize(a, nbins=1024, nodata=None):
    """Classic histogram equalisation using the cumulative distribution function."""
    valid = _valid(a, nodata)
    v = a[valid]
    out = np.zeros(a.shape, "uint8")
    if v.size == 0:
        return out
    hist, edges = np.histogram(v, bins=nbins)
    cdf = hist.cumsum().astype("float64")
    cdf = (cdf - cdf.min()) / max(cdf.max() - cdf.min(), 1) * 255.0
    centers = (edges[:-1] + edges[1:]) / 2.0
    out[valid] = np.interp(v, centers, cdf).astype("uint8")
    return out


def gamma_correct(a8, gamma=1.0):
    lut = (np.linspace(0, 1, 256) ** (1.0 / max(gamma, 1e-3)) * 255).astype("uint8")
    return lut[a8]


def histogram(a, nbins=128, nodata=None):
    v = a[_valid(a, nodata)]
    if v.size == 0:
        return np.zeros(nbins), np.linspace(0, 1, nbins + 1)
    return np.histogram(v, bins=nbins)


def band_stats(a, nodata=None):
    v = a[_valid(a, nodata)].astype("float64")
    if v.size == 0:
        return dict(n=0, min=np.nan, max=np.nan, mean=np.nan, std=np.nan, p2=np.nan, p98=np.nan)
    p2, p50, p98 = np.percentile(v, [2, 50, 98])
    return dict(n=int(v.size), min=float(v.min()), max=float(v.max()), mean=float(v.mean()),
                std=float(v.std()), median=float(p50), p2=float(p2), p98=float(p98))


# ===================================================================== spatial filters
def _windows(a, k):
    r = k // 2
    p = np.pad(a, r, mode="reflect")
    if _swv is not None:
        return _swv(p, (k, k))
    s = p.strides
    return np.lib.stride_tricks.as_strided(p, (a.shape[0], a.shape[1], k, k), s + s)


def convolve(a, kernel):
    """2-D convolution (correlation) with reflect padding, done with shifted sums."""
    a = np.nan_to_num(a.astype("float64"))
    kernel = np.asarray(kernel, "float64")
    kh, kw = kernel.shape
    ry, rx = kh // 2, kw // 2
    p = np.pad(a, ((ry, ry), (rx, rx)), mode="reflect")
    out = np.zeros_like(a)
    H, W = a.shape
    for y in range(kh):
        for x in range(kw):
            w = kernel[y, x]
            if w != 0:
                out += w * p[y:y + H, x:x + W]
    return out


def mean_filter(a, k=3):
    return convolve(a, np.ones((k, k)) / (k * k))


def gaussian_kernel(sigma=1.0):
    r = max(1, int(round(3 * sigma)))
    x = np.arange(-r, r + 1)
    g = np.exp(-(x ** 2) / (2 * sigma ** 2))
    k = np.outer(g, g)
    return k / k.sum()


def gaussian_filter(a, sigma=1.0):
    return convolve(a, gaussian_kernel(sigma))


def median_filter(a, k=3, block_rows=256):
    a = np.nan_to_num(a.astype("float64"))
    out = np.empty_like(a)
    r = k // 2
    for y0 in range(0, a.shape[0], block_rows):
        y1 = min(a.shape[0], y0 + block_rows)
        ya, yb = max(0, y0 - r), min(a.shape[0], y1 + r)
        w = _windows(a[ya:yb], k)
        med = np.median(w.reshape(w.shape[0], w.shape[1], -1), axis=2)
        out[y0:y1] = med[y0 - ya:y0 - ya + (y1 - y0)]
    return out


SOBEL_X = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], float)
SOBEL_Y = SOBEL_X.T
LAPLACIAN = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], float)
HIGHPASS = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]], float)
EMBOSS = np.array([[-2, -1, 0], [-1, 1, 1], [0, 1, 2]], float)


def sobel(a):
    gx, gy = convolve(a, SOBEL_X), convolve(a, SOBEL_Y)
    return np.hypot(gx, gy)


def majority_filter(cls, k=3, nodata=0, block_rows=128):
    """Mode filter for smoothing a classified map (removes 'salt and pepper')."""
    cls = cls.astype("int32")
    out = cls.copy()
    labels = np.unique(cls[cls != nodata])
    if labels.size == 0:
        return out
    r = k // 2
    for y0 in range(0, cls.shape[0], block_rows):
        y1 = min(cls.shape[0], y0 + block_rows)
        ya, yb = max(0, y0 - r), min(cls.shape[0], y1 + r)
        best = np.full((y1 - y0, cls.shape[1]), -1, "int32")
        bestc = np.zeros((y1 - y0, cls.shape[1]), "int32")
        for lab in labels:
            cnt = convolve((cls[ya:yb] == lab).astype("float64"), np.ones((k, k)))
            cnt = cnt[y0 - ya:y0 - ya + (y1 - y0)]
            upd = cnt > best
            best[upd] = cnt[upd]
            bestc[upd] = lab
        keep = cls[y0:y1] == nodata
        bestc[keep] = nodata
        out[y0:y1] = bestc
    return out


# ===================================================================== band math
def band_math(expr, variables):
    """Evaluate an expression like '(nir-red)/(nir+red)' safely with NumPy."""
    safe = {"np": np, "sqrt": np.sqrt, "log": np.log, "log10": np.log10, "exp": np.exp,
            "abs": np.abs, "where": np.where, "minimum": np.minimum, "maximum": np.maximum,
            "pi": np.pi}
    env = dict(safe)
    env.update({k: np.asarray(v, "float64") for k, v in variables.items()})
    for bad in ("__", "import", "lambda", "open", "exec", "eval"):
        if bad in expr:
            raise ValueError("Expression not allowed: %s" % bad)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = eval(expr, {"__builtins__": {}}, env)  # noqa: S307  # nosec B307 - band math in an empty-builtins namespace
    out = np.asarray(out, "float64")
    out[~np.isfinite(out)] = np.nan
    return out


def db_to_linear(db):
    return 10.0 ** (np.asarray(db, "float64") / 10.0)


# ===================================================================== PCA
def pca(X):
    """X: (n, b) pixel matrix. Returns (mean, eigvals desc, eigvecs columns, explained %)."""
    X = X.astype("float64")
    mu = X.mean(axis=0)
    C = np.cov(X - mu, rowvar=False)
    C = np.atleast_2d(C)
    w, V = np.linalg.eigh(C)
    order = np.argsort(w)[::-1]
    w, V = w[order], V[:, order]
    exp = 100.0 * w / max(w.sum(), 1e-12)
    return mu, w, V, exp


def pca_transform(X, mu, V, n):
    return (X.astype("float64") - mu) @ V[:, :n]


# ===================================================================== unsupervised
def kmeans(X, k=6, max_iter=30, tol=1e-4, seed=0, callback=None):
    """K-means with k-means++ initialisation. X (n, b). Returns (centres, labels 0..k-1, inertia)."""
    rng = np.random.default_rng(seed)
    X = X.astype("float64")
    n = X.shape[0]
    centres = np.empty((k, X.shape[1]))
    centres[0] = X[rng.integers(n)]
    d2 = ((X - centres[0]) ** 2).sum(1)
    for i in range(1, k):
        p = d2 / d2.sum() if d2.sum() > 0 else None
        centres[i] = X[rng.choice(n, p=p)]
        d2 = np.minimum(d2, ((X - centres[i]) ** 2).sum(1))
    labels = np.zeros(n, "int32")
    for it in range(max_iter):
        labels, dmin = nearest_centre(X, centres)
        new = centres.copy()
        for j in range(k):
            m = labels == j
            if m.any():
                new[j] = X[m].mean(0)
            else:  # empty cluster -> re-seed at the farthest point
                new[j] = X[np.argmax(dmin)]
        shift = np.sqrt(((new - centres) ** 2).sum(1)).max()
        centres = new
        if callback:
            callback(it + 1, max_iter)
        if shift < tol * (np.abs(X).mean() + 1e-12):
            break
    labels, dmin = nearest_centre(X, centres)
    return centres, labels, float(dmin.sum())


def nearest_centre(X, centres):
    d = ((X[:, None, :] - centres[None, :, :]) ** 2).sum(2)
    lab = d.argmin(1).astype("int32")
    return lab, d[np.arange(X.shape[0]), lab]


def isodata(X, k_init=8, k_min=3, k_max=16, max_iter=20, min_size_frac=0.01, max_std=None,
            min_dist=None, seed=0, callback=None):
    """Simplified ISODATA: k-means steps + discard small, split wide, merge close clusters."""
    X = X.astype("float64")
    n = X.shape[0]
    gstd = X.std(0).mean()
    max_std = max_std if max_std is not None else 0.6 * gstd
    min_dist = min_dist if min_dist is not None else 0.4 * gstd
    centres, labels, _ = kmeans(X, k_init, max_iter=5, seed=seed)
    for it in range(max_iter):
        labels, _ = nearest_centre(X, centres)
        keep = []
        for j in range(len(centres)):
            m = labels == j
            if m.sum() >= max(2, min_size_frac * n):
                keep.append(X[m].mean(0))
        centres = np.array(keep) if keep else centres
        labels, _ = nearest_centre(X, centres)
        # split
        if len(centres) < k_max:
            new = []
            for j, c in enumerate(centres):
                m = labels == j
                s = X[m].std(0) if m.sum() > 1 else np.zeros_like(c)
                if s.max() > max_std and m.sum() > 2 * min_size_frac * n and len(centres) + len(new) < k_max:
                    d = np.zeros_like(c)
                    d[s.argmax()] = 0.5 * s.max()
                    new += [c + d, c - d]
                else:
                    new.append(c)
            centres = np.array(new)
        # merge
        if len(centres) > k_min:
            dist = np.sqrt(((centres[:, None] - centres[None]) ** 2).sum(2))
            np.fill_diagonal(dist, np.inf)
            i, j = np.unravel_index(dist.argmin(), dist.shape)
            if dist[i, j] < min_dist:
                merged = (centres[i] + centres[j]) / 2
                centres = np.vstack([np.delete(centres, [i, j], 0), merged])
        if callback:
            callback(it + 1, max_iter)
    labels, d = nearest_centre(X, centres)
    return centres, labels, float(d.sum())


def sort_clusters_by_brightness(centres):
    """Order clusters dark -> bright so colours are stable between runs."""
    order = np.argsort(centres.mean(1))
    remap = np.empty(len(order), "int32")
    remap[order] = np.arange(len(order))
    return centres[order], remap


# ===================================================================== supervised
class Classifier:
    """Common interface: fit(X, y) then predict(X) -> class ids (0 = unclassified)."""
    name = "base"

    def fit(self, X, y):
        X = X.astype("float64")
        self.classes = np.unique(y)
        self.means = np.array([X[y == c].mean(0) for c in self.classes])
        self.stds = np.array([X[y == c].std(0) + 1e-9 for c in self.classes])
        self.mins = np.array([X[y == c].min(0) for c in self.classes])
        self.maxs = np.array([X[y == c].max(0) for c in self.classes])
        return self

    def predict(self, X):
        raise NotImplementedError


class MinimumDistance(Classifier):
    name = "Minimum Distance"

    def __init__(self, max_dist=None):
        self.max_dist = max_dist

    def predict(self, X):
        d = np.sqrt(((X[:, None, :] - self.means[None]) ** 2).sum(2))
        i = d.argmin(1)
        out = self.classes[i]
        if self.max_dist:
            out = np.where(d[np.arange(len(i)), i] > self.max_dist, 0, out)
        return out


class MaximumLikelihood(Classifier):
    name = "Maximum Likelihood"

    def __init__(self, threshold_prob=0.0):
        self.threshold = threshold_prob

    def fit(self, X, y):
        super().fit(X, y)
        X = X.astype("float64")
        self.inv, self.logdet = [], []
        b = X.shape[1]
        for c in self.classes:
            Xi = X[y == c]
            C = np.cov(Xi, rowvar=False) if len(Xi) > 1 else np.eye(b)
            C = np.atleast_2d(C) + np.eye(b) * 1e-6 * (np.trace(np.atleast_2d(C)) / b + 1e-12)
            sign, ld = np.linalg.slogdet(C)
            self.inv.append(np.linalg.pinv(C))
            self.logdet.append(ld)
        n = np.array([(y == c).sum() for c in self.classes], float)
        self.logprior = np.log(n / n.sum())
        return self

    def discriminants(self, X):
        X = X.astype("float64")
        g = np.empty((X.shape[0], len(self.classes)))
        for j in range(len(self.classes)):
            d = X - self.means[j]
            m = np.einsum("ij,jk,ik->i", d, self.inv[j], d)
            g[:, j] = -0.5 * self.logdet[j] - 0.5 * m
        return g

    def predict(self, X):
        g = self.discriminants(X)
        i = g.argmax(1)
        out = self.classes[i]
        if self.threshold > 0:
            p = np.exp(g - g.max(1, keepdims=True))
            p = p / p.sum(1, keepdims=True)
            out = np.where(p[np.arange(len(i)), i] < self.threshold, 0, out)
        return out


class SpectralAngle(Classifier):
    name = "Spectral Angle Mapper"

    def __init__(self, max_angle_rad=None):
        self.max_angle = max_angle_rad

    def predict(self, X):
        X = X.astype("float64")
        num = X @ self.means.T
        den = np.linalg.norm(X, axis=1)[:, None] * np.linalg.norm(self.means, axis=1)[None] + 1e-12
        ang = np.arccos(np.clip(num / den, -1, 1))
        i = ang.argmin(1)
        out = self.classes[i]
        if self.max_angle:
            out = np.where(ang[np.arange(len(i)), i] > self.max_angle, 0, out)
        return out


class Parallelepiped(Classifier):
    name = "Parallelepiped"

    def __init__(self, n_std=2.0):
        self.n_std = n_std

    def predict(self, X):
        lo = self.means - self.n_std * self.stds
        hi = self.means + self.n_std * self.stds
        inside = ((X[:, None, :] >= lo[None]) & (X[:, None, :] <= hi[None])).all(2)
        # ties / overlaps resolved by minimum distance
        d = np.sqrt(((X[:, None, :] - self.means[None]) ** 2).sum(2))
        d[~inside] = np.inf
        i = d.argmin(1)
        out = self.classes[i]
        out[~inside.any(1)] = 0
        return out


def _threads():
    """Run scikit-learn/joblib with THREADS, never processes. Inside QGIS (macOS especially) a worker
    process is started with sys.executable = the QGIS app, which opened a second QGIS window."""
    import contextlib
    import os
    os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 2))
    try:
        from joblib import parallel_backend
        return parallel_backend("threading", n_jobs=-1)
    except Exception:  # noqa: BLE001
        try:
            from sklearn.utils import parallel_backend
            return parallel_backend("threading", n_jobs=-1)
        except Exception:  # noqa: BLE001
            return contextlib.nullcontext()


class SklearnWrapper(Classifier):
    def __init__(self, kind="rf", **kw):
        self.kind, self.kw = kind, kw
        self.name = {"rf": "Random Forest", "svm": "Support Vector Machine", "knn": "k-Nearest Neighbours"}[kind]

    def fit(self, X, y):
        super().fit(X, y)
        if self.kind == "rf":
            from sklearn.ensemble import RandomForestClassifier
            self.m = RandomForestClassifier(n_estimators=self.kw.get("n_trees", 200), n_jobs=-1,
                                            random_state=0, oob_score=True)
        elif self.kind == "svm":
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler
            from sklearn.svm import SVC
            self.m = make_pipeline(StandardScaler(), SVC(C=self.kw.get("C", 10.0), gamma="scale"))
        else:
            from sklearn.neighbors import KNeighborsClassifier
            self.m = KNeighborsClassifier(n_neighbors=self.kw.get("k", 5))
        with _threads():
            self.m.fit(X, y)
        return self

    def predict(self, X):
        with _threads():
            return self.m.predict(X)

    def importances(self):
        return getattr(self.m, "feature_importances_", None)


def make_classifier(key, **kw):
    if key == "mindist":
        return MinimumDistance(kw.get("max_dist"))
    if key == "maxlik":
        return MaximumLikelihood(kw.get("threshold", 0.0))
    if key == "sam":
        return SpectralAngle(kw.get("max_angle"))
    if key == "para":
        return Parallelepiped(kw.get("n_std", 2.0))
    return SklearnWrapper(key, **kw)


def separability(X, y):
    """Jeffries-Matusita distance between class pairs (0..2; >1.9 = well separated)."""
    classes = np.unique(y)
    stats = []
    for c in classes:
        Xi = X[y == c].astype("float64")
        C = np.atleast_2d(np.cov(Xi, rowvar=False)) if len(Xi) > 1 else np.eye(X.shape[1])
        stats.append((Xi.mean(0), C + np.eye(X.shape[1]) * 1e-6))
    n = len(classes)
    jm = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            m1, C1 = stats[i]
            m2, C2 = stats[j]
            C = (C1 + C2) / 2
            d = m1 - m2
            B = 0.125 * d @ np.linalg.pinv(C) @ d + 0.5 * (
                np.linalg.slogdet(C)[1] - 0.5 * (np.linalg.slogdet(C1)[1] + np.linalg.slogdet(C2)[1]))
            jm[i, j] = jm[j, i] = 2 * (1 - np.exp(-B))
    return classes, jm


# ===================================================================== accuracy
def confusion_matrix(ref, pred, labels=None):
    ref, pred = np.asarray(ref).ravel(), np.asarray(pred).ravel()
    if labels is None:
        labels = np.unique(np.concatenate([ref, pred]))
    labels = np.asarray(labels)
    idx = {v: i for i, v in enumerate(labels)}
    cm = np.zeros((len(labels), len(labels)), "int64")   # rows = classified, cols = reference
    for r, p in zip(ref, pred):
        if r in idx and p in idx:
            cm[idx[p], idx[r]] += 1
    return labels, cm


def accuracy_metrics(cm):
    cm = cm.astype("float64")
    n = cm.sum()
    diag = np.diag(cm)
    oa = diag.sum() / n if n else np.nan
    pe = (cm.sum(0) * cm.sum(1)).sum() / (n * n) if n else np.nan
    kappa = (oa - pe) / (1 - pe) if n and pe != 1 else np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        ua = diag / cm.sum(1)          # user's accuracy  (precision; 1 - commission)
        pa = diag / cm.sum(0)          # producer's accuracy (recall; 1 - omission)
        f1 = 2 * ua * pa / (ua + pa)
    return dict(oa=oa, kappa=kappa, ua=ua, pa=pa, f1=f1, n=int(n),
                commission=1 - ua, omission=1 - pa)


def stratified_sample(labels, n_per_class=50, nodata=0, seed=0):
    """Return (rows, cols, class) of random samples drawn per class from a classified map."""
    rng = np.random.default_rng(seed)
    rr, cc, kk = [], [], []
    for c in np.unique(labels):
        if c == nodata:
            continue
        r, col = np.nonzero(labels == c)
        if r.size == 0:
            continue
        pick = rng.choice(r.size, size=min(n_per_class, r.size), replace=False)
        rr.append(r[pick])
        cc.append(col[pick])
        kk.append(np.full(pick.size, c))
    if not rr:
        return np.array([]), np.array([]), np.array([])
    return np.concatenate(rr), np.concatenate(cc), np.concatenate(kk)


# ===================================================================== change & statistics
def class_areas(labels, pixel_area_m2, nodata=0):
    vals, cnt = np.unique(labels[labels != nodata], return_counts=True)
    tot = cnt.sum()
    return [(int(v), int(c), c * pixel_area_m2 / 1e4, 100.0 * c / tot if tot else 0) for v, c in zip(vals, cnt)]


def change_matrix(before, after, nodata=0):
    m = (before != nodata) & (after != nodata)
    labs = np.unique(np.concatenate([np.unique(before[m]), np.unique(after[m])]))
    idx = {v: i for i, v in enumerate(labs)}
    cm = np.zeros((len(labs), len(labs)), "int64")
    b, a = before[m].ravel(), after[m].ravel()
    ib = np.vectorize(idx.get)(b) if b.size else b
    ia = np.vectorize(idx.get)(a) if a.size else a
    np.add.at(cm, (ib, ia), 1)
    return labs, cm


def change_code(before, after, n_classes_max, nodata=0):
    """Encode from->to as before*1000+after (0 where nodata); class ids up to 999."""
    out = before.astype("int32") * 1000 + after.astype("int32")
    out[(before == nodata) | (after == nodata)] = 0
    return out


def difference_threshold(diff, k_std=1.5):
    """Classify a difference image into decrease(1) / no change(2) / increase(3) using mean ± k·σ."""
    v = diff[np.isfinite(diff)]
    m, s = (v.mean(), v.std()) if v.size else (0, 1)
    out = np.full(diff.shape, 2, "uint8")
    out[diff < m - k_std * s] = 1
    out[diff > m + k_std * s] = 3
    out[~np.isfinite(diff)] = 0
    return out, (m - k_std * s, m + k_std * s)


def otsu_threshold(a, nbins=256):
    """Otsu's automatic threshold (e.g. water vs land on MNDWI)."""
    v = a[np.isfinite(a)]
    hist, edges = np.histogram(v, bins=nbins)
    c = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * c) / np.maximum(w0, 1)
    m1 = ((hist * c).sum() - np.cumsum(hist * c)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(c[np.argmax(between)])


def area_adjusted(cm, map_pixels, pixel_area_m2):
    """Area-adjusted accuracy and area estimates with 95 % confidence intervals
    (Olofsson et al. 2014, Remote Sensing of Environment 148: 42–57; stratified estimator with the
    map classes as strata).  cm: rows = map, cols = reference (same label order as map_pixels).
    map_pixels: number of map pixels of each label."""
    cm = np.asarray(cm, "float64")
    npx = np.asarray(map_pixels, "float64")
    a_tot = npx.sum()
    if a_tot <= 0 or cm.sum() <= 0:
        return None
    w = npx / a_tot                                   # map-class area proportions (strata weights)
    ni = cm.sum(1)                                    # sample size per map class
    with np.errstate(divide="ignore", invalid="ignore"):
        pij = np.where(ni[:, None] > 0, w[:, None] * cm / ni[:, None], 0.0)   # estimated cell proportions
        p_j = pij.sum(0)                              # estimated area proportion of each (reference) class
        frac = np.where(ni[:, None] > 0, cm / ni[:, None], 0.0)
        denom = np.maximum(ni - 1, 1)[:, None]
        var_pj = (w[:, None] ** 2 * frac * (1 - frac) / denom).sum(0)
        oa = float(np.trace(pij))
        ua = np.where(ni > 0, np.diag(cm) / ni, np.nan)
        pa = np.where(p_j > 0, np.diag(pij) / p_j, np.nan)
        var_oa = float((w ** 2 * ua * (1 - ua) / np.maximum(ni - 1, 1))[ni > 0].sum())
    ha = pixel_area_m2 / 1e4
    return dict(mapped_ha=(npx * ha).tolist(), area_ha=(p_j * a_tot * ha).tolist(),
                ci_ha=(1.96 * np.sqrt(var_pj) * a_tot * ha).tolist(), pa=pa.tolist(), ua=ua.tolist(),
                oa=oa, oa_ci=float(1.96 * np.sqrt(var_oa)))
