"""Core image-processing and accuracy algorithms (rs_lab/rs_core.py)."""
import numpy as np
import pytest

from rs_lab import rs_core as R


def blobs(seed=0, n=200):
    """Three well separated 2-band classes (ids 1, 2, 3)."""
    rng = np.random.default_rng(seed)
    centres = np.array([[0.05, 0.40], [0.30, 0.10], [0.15, 0.25]])
    X = np.vstack([c + rng.normal(0, 0.01, (n, 2)) for c in centres])
    y = np.repeat([1, 2, 3], n)
    return X, y, centres


# ---------------------------------------------------------------- band math, thresholds
def test_band_math_ndvi():
    out = R.band_math("(nir-red)/(nir+red)", {"nir": [0.5, 0.3, 0.0], "red": [0.1, 0.3, 0.0]})
    assert out[0] == pytest.approx(4 / 6)
    assert out[1] == pytest.approx(0.0)
    assert np.isnan(out[2])                      # 0/0 becomes NaN, not an error


@pytest.mark.parametrize("expr", ["__import__('os')", "open('x')", "(lambda: 1)()"])
def test_band_math_rejects_unsafe(expr):
    with pytest.raises(ValueError):
        R.band_math(expr, {"nir": [1.0]})


def test_otsu_separates_two_modes():
    rng = np.random.default_rng(1)
    a = np.concatenate([rng.normal(-0.4, 0.05, 5000), rng.normal(0.3, 0.05, 5000)])   # land vs water MNDWI
    t = R.otsu_threshold(a)
    assert -0.25 < t < 0.15


def test_db_to_linear():
    assert R.db_to_linear([0, 10, -10]) == pytest.approx([1, 10, 0.1])


# ---------------------------------------------------------------- filters
def test_majority_filter_removes_isolated_pixel():
    cls = np.ones((7, 7), "int32")
    cls[3, 3] = 2
    out = R.majority_filter(cls, k=3)
    assert (out == 1).all()


def test_majority_filter_keeps_nodata():
    cls = np.ones((5, 5), "int32")
    cls[0, 0] = 0
    assert R.majority_filter(cls, k=3)[0, 0] == 0


def test_median_filter_removes_spike():
    a = np.zeros((9, 9))
    a[4, 4] = 100.0
    assert R.median_filter(a, k=3)[4, 4] == 0.0


# ---------------------------------------------------------------- PCA, clustering
def test_pca_explained_variance():
    rng = np.random.default_rng(2)
    t = rng.normal(0, 1, 1000)
    X = np.column_stack([t, 2 * t + rng.normal(0, 0.01, 1000), rng.normal(0, 0.01, 1000)])
    mu, w, V, exp = R.pca(X)
    assert exp.sum() == pytest.approx(100.0)
    assert exp[0] > 99.0                          # almost all variance on PC1
    assert np.all(np.diff(w) <= 1e-12)            # eigenvalues sorted descending


def test_kmeans_recovers_clusters():
    X, y, centres = blobs()
    c, labels, inertia = R.kmeans(X, k=3, seed=0)
    assert inertia >= 0
    # every true class maps to exactly one cluster
    for cls in (1, 2, 3):
        assert len(np.unique(labels[y == cls])) == 1
    assert len(np.unique(labels)) == 3
    # found centres are close to the true ones
    for t in centres:
        assert np.min(np.linalg.norm(c - t, axis=1)) < 0.01


def test_kmeans_is_reproducible():
    X, _, _ = blobs()
    a = R.kmeans(X, k=3, seed=5)[1]
    b = R.kmeans(X, k=3, seed=5)[1]
    assert (a == b).all()


def test_nearest_centre():
    X = np.array([[0.0, 0.0], [1.0, 1.0], [0.9, 1.2]])
    labels, d = R.nearest_centre(X, np.array([[0.0, 0.0], [1.0, 1.0]]))
    assert labels.tolist() == [0, 1, 1]
    assert d[0] == pytest.approx(0.0)


# ---------------------------------------------------------------- supervised
@pytest.mark.parametrize("key", ["mindist", "maxlik", "sam"])
def test_classifiers_on_separable_data(key):
    X, y, _ = blobs()
    clf = R.make_classifier(key).fit(X, y)
    pred = clf.predict(X)
    assert (pred == y).mean() > 0.95


def test_parallelepiped_box():
    """Pixels outside every ±2σ box stay unclassified (0); the rest is correct."""
    X, y, _ = blobs()
    pred = R.make_classifier("para", n_std=2.0).fit(X, y).predict(X)
    inside = pred != 0
    assert (pred[inside] == y[inside]).all()
    assert 0.85 < inside.mean() < 0.97          # ≈ 0.954² of a 2-band Gaussian lies inside the box


def test_jeffries_matusita_separability():
    X, y, _ = blobs()
    classes, jm = R.separability(X, y)
    assert classes.tolist() == [1, 2, 3]
    off = jm[~np.eye(3, dtype=bool)]
    assert np.all(off > 1.9) and np.all(off <= 2.0)      # well separated, JM is bounded by 2
    assert np.allclose(jm, jm.T)


# ---------------------------------------------------------------- accuracy
def test_confusion_matrix_orientation():
    ref = [1, 1, 2, 2, 2]
    pred = [1, 2, 2, 2, 1]
    labels, cm = R.confusion_matrix(ref, pred)
    assert labels.tolist() == [1, 2]
    assert cm.tolist() == [[1, 1], [1, 2]]               # rows = map, cols = reference


def test_accuracy_metrics_known_values():
    cm = np.array([[45, 5], [10, 40]])                    # rows = map, cols = reference
    m = R.accuracy_metrics(cm)
    assert m["oa"] == pytest.approx(0.85)
    pe = (55 * 50 + 45 * 50) / 100 ** 2
    assert m["kappa"] == pytest.approx((0.85 - pe) / (1 - pe))
    assert m["ua"] == pytest.approx([0.9, 0.8])
    assert m["pa"] == pytest.approx([45 / 55, 40 / 45])


def test_area_adjusted_reproduces_olofsson_2014():
    """Worked example of Olofsson et al. (2014, RSE 148: 42-57, Table 8): 30 m Landsat pixels,
    classes deforestation, forest gain, stable forest, stable non-forest."""
    cm = np.array([[66, 0, 5, 4],
                   [0, 55, 8, 12],
                   [1, 0, 153, 11],
                   [2, 1, 9, 313]])
    pixels = [200000, 150000, 3200000, 6450000]
    r = R.area_adjusted(cm, pixels, 900.0)
    assert r["area_ha"][0] == pytest.approx(21158, abs=5)
    assert r["area_ha"][1] == pytest.approx(11686, abs=5)
    assert r["area_ha"][2] == pytest.approx(285770, abs=10)
    assert r["area_ha"][3] == pytest.approx(581386, abs=10)
    assert r["ci_ha"][0] == pytest.approx(6158, abs=5)
    assert r["oa"] == pytest.approx(0.947, abs=0.001)
    assert r["ua"][0] == pytest.approx(0.88)
    assert r["pa"][0] == pytest.approx(0.75, abs=0.005)
    assert sum(r["area_ha"]) == pytest.approx(sum(r["mapped_ha"]))   # total area is preserved


def test_stratified_sample():
    lab = np.zeros((20, 20), "int32")
    lab[:10] = 1
    lab[10:] = 2
    lab[0, 0] = 0
    rr, cc, kk = R.stratified_sample(lab, n_per_class=15, seed=3)
    assert sorted(np.unique(kk).tolist()) == [1, 2]
    assert (kk == 1).sum() == 15 and (kk == 2).sum() == 15
    assert (lab[rr, cc] == kk).all()


# ---------------------------------------------------------------- change
def test_class_areas():
    lab = np.array([[1, 1], [2, 0]])
    areas = R.class_areas(lab, 900.0)
    assert areas[0][:3] == (1, 2, pytest.approx(0.18))
    assert areas[1][3] == pytest.approx(100 / 3)


def test_change_matrix_and_code():
    before = np.array([[1, 1, 2, 0]])
    after = np.array([[1, 2, 2, 1]])
    labs, cm = R.change_matrix(before, after)
    assert labs.tolist() == [1, 2]
    assert cm.tolist() == [[1, 1], [0, 1]]               # nodata pixel ignored
    code = R.change_code(before, after, 2)
    assert code.tolist() == [[1001, 1002, 2002, 0]]


def test_difference_threshold():
    d = np.zeros(100)
    d[0], d[1] = -10, 10
    out, (lo, hi) = R.difference_threshold(d, 1.5)
    assert out[0] == 1 and out[1] == 3 and out[50] == 2
