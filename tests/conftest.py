"""Tests for the parts of LEE RS Lab that do not need QGIS (NumPy algorithms, GDAL helpers, translations,
lessons, metadata). Run from the repository root:  python -m pytest -q tests"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import osgeo  # noqa: F401
    HAS_GDAL = True
except ImportError:                      # pure-NumPy tests still run; GDAL tests are skipped
    HAS_GDAL = False
    stub = types.ModuleType("osgeo")
    stub.gdal = types.SimpleNamespace(UseExceptions=lambda: None)
    stub.ogr = stub.osr = None
    sys.modules["osgeo"] = stub
