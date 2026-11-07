"""Sensor catalogue: GEE collections, bands, roles, scaling, cloud masks, presets, indices.

A band "role" (blue, green, red, nir, swir1 ...) lets every teaching tool work
the same way for Sentinel-2, Landsat 5/7/8/9 or any downloaded file.
"""

# (band name, role, centre wavelength / description, native resolution m)
S2_BANDS = [
    ("B1", "coastal", "443 nm", 60), ("B2", "blue", "490 nm", 10), ("B3", "green", "560 nm", 10),
    ("B4", "red", "665 nm", 10), ("B5", "rededge1", "705 nm", 20), ("B6", "rededge2", "740 nm", 20),
    ("B7", "rededge3", "783 nm", 20), ("B8", "nir", "842 nm", 10), ("B8A", "nir2", "865 nm", 20),
    ("B9", "watervapour", "945 nm", 60), ("B11", "swir1", "1610 nm", 20), ("B12", "swir2", "2190 nm", 20),
]
S2_TOA_BANDS = S2_BANDS[:10] + [("B10", "cirrus", "1375 nm", 60)] + S2_BANDS[10:]

L89_L2_BANDS = [
    ("SR_B1", "coastal", "443 nm", 30), ("SR_B2", "blue", "482 nm", 30), ("SR_B3", "green", "561 nm", 30),
    ("SR_B4", "red", "655 nm", 30), ("SR_B5", "nir", "865 nm", 30), ("SR_B6", "swir1", "1609 nm", 30),
    ("SR_B7", "swir2", "2201 nm", 30), ("ST_B10", "tir", "10.9 um (Kelvin)", 100),
]
L89_TOA_BANDS = [
    ("B1", "coastal", "443 nm", 30), ("B2", "blue", "482 nm", 30), ("B3", "green", "561 nm", 30),
    ("B4", "red", "655 nm", 30), ("B5", "nir", "865 nm", 30), ("B6", "swir1", "1609 nm", 30),
    ("B7", "swir2", "2201 nm", 30), ("B8", "pan", "590 nm panchromatic", 15),
    ("B9", "cirrus", "1373 nm", 30), ("B10", "tir", "10.9 um", 100), ("B11", "tir2", "12.0 um", 100),
]
L57_L2_BANDS = [
    ("SR_B1", "blue", "485 nm", 30), ("SR_B2", "green", "560 nm", 30), ("SR_B3", "red", "660 nm", 30),
    ("SR_B4", "nir", "830 nm", 30), ("SR_B5", "swir1", "1650 nm", 30), ("SR_B7", "swir2", "2215 nm", 30),
    ("ST_B6", "tir", "11.45 um (Kelvin)", 120),
]
# Landsat 1-5 MSS, Collection 2 Level-1 (calibrated DN, 60 m) — band numbers as on Landsat 1-3
MSS_L1_BANDS = [
    ("B4", "green", "500-600 nm", 60), ("B5", "red", "600-700 nm", 60), ("B6", "nir2", "700-800 nm", 60),
    ("B7", "nir", "800-1100 nm", 60),
]
S1_BANDS = [("VV", "vv", "C-band VV (dB)", 10), ("VH", "vh", "C-band VH (dB)", 10)]

SENSORS = {
    "S2_SR": dict(label="Sentinel-2 MSI — Level-2A surface reflectance", coll="COPERNICUS/S2_SR_HARMONIZED",
                  bands=S2_BANDS, cloud="CLOUDY_PIXEL_PERCENTAGE", mask="s2", scale=1e-4, offset=0.0,
                  res=10, start="2017-03-28", family="s2"),
    "S2_TOA": dict(label="Sentinel-2 MSI — Level-1C top-of-atmosphere", coll="COPERNICUS/S2_HARMONIZED",
                   bands=S2_TOA_BANDS, cloud="CLOUDY_PIXEL_PERCENTAGE", mask="s2", scale=1e-4, offset=0.0,
                   res=10, start="2015-06-27", family="s2"),
    "L9_L2": dict(label="Landsat 9 OLI-2/TIRS-2 — Collection 2 Level-2", coll="LANDSAT/LC09/C02/T1_L2",
                  bands=L89_L2_BANDS, cloud="CLOUD_COVER", mask="landsat", scale=2.75e-5, offset=-0.2,
                  res=30, start="2021-10-31", family="landsat"),
    "L8_L2": dict(label="Landsat 8 OLI/TIRS — Collection 2 Level-2", coll="LANDSAT/LC08/C02/T1_L2",
                  bands=L89_L2_BANDS, cloud="CLOUD_COVER", mask="landsat", scale=2.75e-5, offset=-0.2,
                  res=30, start="2013-03-18", family="landsat"),
    "L9_TOA": dict(label="Landsat 9 — Collection 2 TOA reflectance (with pan band)", coll="LANDSAT/LC09/C02/T1_TOA",
                   bands=L89_TOA_BANDS, cloud="CLOUD_COVER", mask="landsat", scale=1.0, offset=0.0,
                   res=30, start="2021-10-31", family="landsat", float_native=True),
    "L8_TOA": dict(label="Landsat 8 — Collection 2 TOA reflectance (with pan band)", coll="LANDSAT/LC08/C02/T1_TOA",
                   bands=L89_TOA_BANDS, cloud="CLOUD_COVER", mask="landsat", scale=1.0, offset=0.0,
                   res=30, start="2013-03-18", family="landsat", float_native=True),
    "L7_L2": dict(label="Landsat 7 ETM+ — Collection 2 Level-2 (SLC-off after May 2003)", coll="LANDSAT/LE07/C02/T1_L2",
                  bands=L57_L2_BANDS, cloud="CLOUD_COVER", mask="landsat", scale=2.75e-5, offset=-0.2,
                  res=30, start="1999-05-28", family="landsat"),
    "L5_L2": dict(label="Landsat 5 TM — Collection 2 Level-2 (1984–2012)", coll="LANDSAT/LT05/C02/T1_L2",
                  bands=L57_L2_BANDS, cloud="CLOUD_COVER", mask="landsat", scale=2.75e-5, offset=-0.2,
                  res=30, start="1984-03-16", family="landsat"),
    "LM_L1": dict(label="Landsat 1–5 MSS — Collection 2 Level-1, raw DN (1972–2013)", coll="LANDSAT/LM0x/C02/T1",
                  bands=MSS_L1_BANDS, cloud="CLOUD_COVER", mask="landsat", scale=1.0, offset=0.0,
                  res=60, start="1972-07-25", end="2013-01-07", suggest=("1989-11-01", "1990-04-30"),
                  family="landsat", dn_only=True),
    "S1_GRD": dict(label="Sentinel-1 SAR GRD — IW, VV+VH, dB (radar, sees through cloud)", coll="COPERNICUS/S1_GRD",
                   bands=S1_BANDS, cloud=None, mask=None, scale=1.0, offset=0.0, res=10,
                   start="2014-10-03", family="s1", float_native=True),
}
SENSOR_ORDER = ["S2_SR", "S2_TOA", "L9_L2", "L8_L2", "L9_TOA", "L8_TOA", "L7_L2", "L5_L2", "LM_L1", "S1_GRD"]

# Thermal band scale for Landsat L2 (Kelvin)
LST_SCALE, LST_OFFSET = 0.00341802, 149.0

COMPOSITE_METHODS = [
    ("median", "Median composite (recommended)"),
    ("mean", "Mean composite"),
    ("p25", "25th percentile composite (darker, fewer clouds)"),
    ("min", "Minimum composite"),
    ("greenest", "Greenest pixel (max NDVI) mosaic"),
    ("leastcloudy", "Least-cloudy single scene"),
    ("selected", "Scene(s) selected in the table (mosaic)"),
]

# Band-combination presets defined by roles (R, G, B)
PRESETS = [
    ("True colour (natural)", ("red", "green", "blue"),
     "Looks like a photo. Vegetation green, water dark, urban grey."),
    ("False colour infrared (vegetation)", ("nir", "red", "green"),
     "Healthy vegetation is bright red because leaves reflect NIR strongly."),
    ("Agriculture", ("swir1", "nir", "blue"),
     "Active crops bright green; bare fields magenta/brown."),
    ("Urban / bare soil", ("swir2", "swir1", "red"),
     "Built-up areas and bare soil appear cyan-purple; vegetation green."),
    ("Land / water", ("nir", "swir1", "red"),
     "Clear land-water boundary: water is very dark blue-black."),
    ("Natural with atmospheric penetration", ("swir2", "nir", "green"),
     "Reduces haze; vegetation green, water black, burnt areas red."),
    ("Healthy vegetation", ("nir", "swir1", "blue"),
     "Separates crop types and vegetation vigour."),
    ("Shortwave infrared", ("swir2", "nir", "red"),
     "Moisture: wet soils dark, dense vegetation bright green."),
    ("Geology", ("swir2", "swir1", "blue"),
     "Highlights rock and soil differences."),
    ("Red-edge (Sentinel-2)", ("rededge3", "rededge1", "red"),
     "Sensitive to chlorophyll changes in crops."),
    ("SAR (VV, VH, VV/VH)", ("vv", "vh", "ratio"),
     "Radar: urban bright, water dark, vegetation green-ish."),
]

# Spectral indices: name -> (formula with roles, description, value range hint)
INDICES = {
    "NDVI": ("(nir - red) / (nir + red)", "Normalized Difference Vegetation Index — greenness", (-1, 1)),
    "EVI": ("2.5 * (nir - red) / (nir + 6*red - 7.5*blue + 1)", "Enhanced Vegetation Index — dense canopy", (-1, 1)),
    "SAVI": ("1.5 * (nir - red) / (nir + red + 0.5)", "Soil-Adjusted Vegetation Index (L = 0.5)", (-1, 1)),
    "NDWI": ("(green - nir) / (green + nir)", "McFeeters water index — open water", (-1, 1)),
    "MNDWI": ("(green - swir1) / (green + swir1)", "Modified NDWI — water in built-up areas", (-1, 1)),
    "NDMI": ("(nir - swir1) / (nir + swir1)", "Moisture index — leaf water content", (-1, 1)),
    "NDBI": ("(swir1 - nir) / (swir1 + nir)", "Built-up index", (-1, 1)),
    "NBR": ("(nir - swir2) / (nir + swir2)", "Normalized Burn Ratio — fire scars", (-1, 1)),
    "BSI": ("((swir1 + red) - (nir + blue)) / ((swir1 + red) + (nir + blue))", "Bare Soil Index", (-1, 1)),
    "GNDVI": ("(nir - green) / (nir + green)", "Green NDVI — chlorophyll", (-1, 1)),
    "NDRE": ("(nir - rededge1) / (nir + rededge1)", "Red-edge NDVI (Sentinel-2) — crop nitrogen", (-1, 1)),
    "UI": ("(swir2 - nir) / (swir2 + nir)", "Urban Index", (-1, 1)),
    "RVI_SAR": ("4 * vh_lin / (vv_lin + vh_lin)", "Radar Vegetation Index (Sentinel-1)", (0, 1)),
}

ALL_ROLES = ["coastal", "blue", "green", "red", "rededge1", "rededge2", "rededge3", "nir", "nir2",
             "swir1", "swir2", "pan", "tir", "vv", "vh"]


def band_desc(name, role, wl):
    """Text written into GeoTIFF band descriptions: 'B4 | red | 665 nm'."""
    return "%s | %s | %s" % (name, role, wl)


def role_from_desc(desc):
    if not desc:
        return None
    parts = [p.strip() for p in desc.split("|")]
    if len(parts) >= 2 and parts[1]:
        return parts[1].lower()
    d = desc.strip().upper()
    guess = {"B2": "blue", "B3": "green", "B4": "red", "B8": "nir", "B11": "swir1", "B12": "swir2",
             "SR_B2": "blue", "SR_B3": "green", "SR_B4": "red", "SR_B5": "nir", "SR_B6": "swir1",
             "SR_B7": "swir2", "VV": "vv", "VH": "vh"}
    return guess.get(d)
