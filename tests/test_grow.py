"""Magic wand (click-to-grow) training areas (rs_lab/grow.py)."""
import numpy as np
import pytest

from conftest import HAS_GDAL
from rs_lab import grow


def scene(seed=0):
    """3-band image 200×200 with a bright 40×60 'field' (rows 50–89, cols 60–119) on noisy background."""
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 0.02, (3, 200, 200))
    X[:, 50:90, 60:120] += np.array([0.5, 0.4, 0.3])[:, None, None]
    return X


def test_grows_exactly_the_field():
    m = grow.grow_from_array(scene(), (70, 80), tolerance=1.0, max_pixels=10000)
    assert m.sum() == 40 * 60
    assert m[50:90, 60:120].all()


def test_does_not_cross_to_disconnected_similar_area():
    X = scene()
    X[:, 150:170, 150:170] += np.array([0.5, 0.4, 0.3])[:, None, None]   # same spectrum, not connected
    m = grow.grow_from_array(X, (70, 80), 1.0, 10000)
    assert not m[150:170, 150:170].any()


def test_max_pixels_cap():
    m = grow.grow_from_array(scene(), (70, 80), 1.0, max_pixels=500)
    assert m.sum() == 500
    assert m[70, 80]


def test_nodata_seed_gives_empty_region():
    X = scene()
    X[:, 70, 80] = np.nan
    assert grow.grow_from_array(X, (70, 80), 1.0, 1000).sum() == 0


def test_fill_small_holes():
    m = np.zeros((20, 20), bool)
    m[5:15, 5:15] = True
    m[9, 9] = False                              # a noisy pixel inside the field
    out = grow.fill_small_holes(m)
    assert out[9, 9]
    assert out.sum() == 100                      # the outside is not filled


def test_fill_small_holes_keeps_large_hole():
    m = np.zeros((30, 30), bool)
    m[2:28, 2:28] = True
    m[8:22, 8:22] = False                        # a pond inside a field: too large to fill
    assert not grow.fill_small_holes(m)[15, 15]


def test_trim_removes_edge_ring():
    m = np.zeros((20, 20), bool)
    m[5:15, 5:15] = True
    t = grow.trim(m)
    assert t.sum() == 64 and t[6:14, 6:14].all()


def test_trim_keeps_thin_region():
    m = np.zeros((10, 10), bool)
    m[5, 1:9] = True                             # one pixel wide: erosion would delete it
    assert (grow.trim(m) == m).all()


@pytest.mark.skipif(not HAS_GDAL, reason="GDAL not installed")
def test_grow_region_and_polygon(tmp_path):
    from osgeo import gdal, ogr, osr
    X = scene()
    path = str(tmp_path / "img.tif")
    ds = gdal.GetDriverByName("GTiff").Create(path, 200, 200, 3, gdal.GDT_Float32)
    ds.SetGeoTransform((500000, 10, 0, 1300000, 0, -10))          # 10 m pixels, UTM 48N
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32648)
    ds.SetProjection(srs.ExportToWkt())
    for i in range(3):
        ds.GetRasterBand(i + 1).WriteArray(X[i])
    ds = None
    assert grow.pixel_area_m2(path) == pytest.approx(100.0)
    x, y = 500000 + 80 * 10 + 5, 1300000 - 70 * 10 - 5            # centre of pixel (row 70, col 80)
    m, info = grow.grow_region(path, [1, 2, 3], x, y, tolerance=1.0, max_ha=100)
    assert not info["capped"]
    assert info["n"] == 38 * 58                                    # field minus the trimmed edge ring
    g = ogr.CreateGeometryFromWkt(grow.mask_to_wkt(m, info))
    assert g.GetArea() == pytest.approx(38 * 58 * 100.0)
    assert g.Contains(ogr.CreateGeometryFromWkt("POINT (%f %f)" % (x, y)))
    # max. area per click: 5 ha of 10 m pixels = 500 pixels
    m, info = grow.grow_region(path, [1, 2, 3], x, y, tolerance=1.0, max_ha=5)
    assert info["capped"]
    assert grow.grow_region(path, [1, 2, 3], 0, 0)[0] is None      # outside the image
