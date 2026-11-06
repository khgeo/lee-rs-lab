"""Ready-to-run LEE scripts (shown in tab 5 LEE). `aoi` is the AOI from tab 1."""

EXAMPLES = [
    ("True colour + NDVI (dry-season median)", '''# Cloud-masked median of the dry season, then NDVI — like GEE, but computed on your computer
s2 = (lee.ImageCollection("sentinel-2")
        .filterDate("2025-11-01", "2026-03-31")
        .filterBounds(aoi)
        .filterMetadata("eo:cloud_cover", "lt", 30)
        .limit(8))                       # 8 least-cloudy scenes
print("scenes found:", s2.size())
img = s2.median()
Map.addLayer(img, {"bands": ["red", "green", "blue"], "min": 0, "max": 0.3}, "True colour")
Map.addLayer(img, {"bands": ["nir", "red", "green"], "min": 0, "max": 0.4}, "False colour")
ndvi = img.normalizedDifference("nir", "red").rename("NDVI")
Map.addLayer(ndvi, {"min": 0, "max": 0.9, "palette": "RdYlGn"}, "NDVI")
print("mean NDVI in the AOI:", ndvi.reduceRegion("mean"))
'''),
    ("NDVI time series chart", '''# NDVI of every clear scene, averaged over the AOI → chart
lee.config(scale=30)                 # coarser pixels = faster for long series
s2 = (lee.ImageCollection("sentinel-2")
        .filterDate("2025-01-01", "2025-12-31")
        .filterMetadata("eo:cloud_cover", "lt", 40))
ts = s2.timeSeries(lambda img: img.normalizedDifference("nir", "red"), reducer="mean",
                   title="NDVI 2025 (AOI mean)")
for date, v in ts:
    print(date, round(v, 3))
'''),
    ("Monthly NDVI composites", '''lee.config(scale=20)
s2 = lee.ImageCollection("sentinel-2").filterDate("2025-06-01", "2025-11-30").filterMetadata("eo:cloud_cover", "lt", 60)
for month, img in s2.monthly("median").items():
    ndvi = img.normalizedDifference("nir", "red")
    Map.addLayer(ndvi, {"min": 0, "max": 0.9, "palette": "RdYlGn"}, "NDVI " + month)
    print(month, "images:", img.props["count"], "mean NDVI:", round(ndvi.reduceRegion("mean")["nd"], 3))
'''),
    ("Water mapping (MNDWI + Otsu)", '''img = (lee.ImageCollection("sentinel-2").filterDate("2025-09-01", "2025-11-30")
        .filterMetadata("eo:cloud_cover", "lt", 40).limit(6).median())
mndwi = img.normalizedDifference("green", "swir1").rename("MNDWI")
t = mndwi.otsu()                                  # automatic threshold from the histogram
water = mndwi.gt(t).rename("water")
print("Otsu threshold:", round(t, 3))
print("water area (ha):", round(water.reduceRegion("sum")["water"] * img.grid.scale ** 2 / 1e4, 1))
Map.addLayer(mndwi, {"min": -0.6, "max": 0.6, "palette": "Blues"}, "MNDWI")
Map.addLayer(water.updateMask(water), {"palette": "Blues"}, "Water")
'''),
    ("Terrain: slope, aspect, hillshade (Copernicus DEM)", '''lee.config(scale=30)
dem = lee.Image.load("dem")          # Copernicus GLO-30, tiles mosaicked and clipped to the AOI
print("elevation:", dem.reduceRegion("min"), dem.reduceRegion("max"))
Map.addLayer(dem, {"palette": "Spectral"}, "Elevation (m)")
Map.addLayer(dem.hillshade(315, 45), {}, "Hillshade")
Map.addLayer(dem.slope(), {"min": 0, "max": 30, "palette": "YlOrRd"}, "Slope (°)")
Map.addLayer(dem.aspect(), {"min": 0, "max": 360, "palette": "Spectral"}, "Aspect (°)")
'''),
    ("Land-cover statistics (ESA WorldCover)", '''wc = lee.Image.load("worldcover")    # newest year (2021); use lee.Image.load("worldcover", 2020) for 2020
Map.addLayer(wc, {}, "ESA WorldCover")
areas = wc.classAreas()
for name, ha in sorted(areas.items(), key=lambda x: -x[1]):
    print("%-28s %10.1f ha" % (name, ha))
Chart.bars(areas, "Land cover (ha)", "ha")
'''),
    ("Land-cover change 2017 → 2023 (annual LULC)", '''a = lee.Image.load("lulc-annual", 2017)
b = lee.Image.load("lulc-annual", 2023)
Map.addLayer(a, {}, "Land cover 2017")
Map.addLayer(b, {}, "Land cover 2023")
changed = a.neq(b).rename("changed")
print("changed area (ha):", round(changed.reduceRegion("sum")["changed"] * a.grid.scale ** 2 / 1e4, 1))
Map.addLayer(changed.updateMask(changed), {"palette": "Reds"}, "Changed 2017–2023")
'''),
    ("Burn severity dNBR (Landsat before / after)", '''pre = lee.ImageCollection("landsat-9").filterDate("2025-01-01", "2025-02-15").limit(4).median()
post = lee.ImageCollection("landsat-9").filterDate("2025-04-01", "2025-05-15").limit(4).median()
nbr = lambda i: i.normalizedDifference("nir", "swir2")
dnbr = (nbr(pre) - nbr(post)).rename("dNBR")
Map.addLayer(dnbr, {"min": -0.25, "max": 0.66, "palette": "YlOrRd"}, "dNBR")
burnt = dnbr.gt(0.27)                          # moderate-to-high severity (USGS classes)
print("burnt area > 0.27 (ha):", round(burnt.reduceRegion("sum")["dNBR"] * pre.grid.scale ** 2 / 1e4, 1))
'''),
    ("Flood mapping with Sentinel-1 (before / after)", '''# Radar sees through clouds: water is dark (low VV backscatter)
before = lee.ImageCollection("sentinel-1").filterDate("2025-04-01", "2025-04-30").select("VV").median()
after = lee.ImageCollection("sentinel-1").filterDate("2025-09-15", "2025-10-15").select("VV").median()
diff = (after - before).rename("dVV")
flood = after.lt(-18).And(diff.lt(-3)).rename("flood")
print("flooded area (ha):", round(flood.reduceRegion("sum")["flood"] * after.grid.scale ** 2 / 1e4, 1))
Map.addLayer(after, {"min": -25, "max": 0}, "VV after (dB)")
Map.addLayer(flood.updateMask(flood), {"palette": "Blues"}, "Flood")
'''),
    ("Greenest-pixel mosaic", '''s2 = (lee.ImageCollection("sentinel-2").filterDate("2025-06-01", "2025-12-31")
        .filterMetadata("eo:cloud_cover", "lt", 50).limit(10)
        .map(lambda i: i.addBands(i.normalizedDifference("nir", "red").rename("NDVI"))))
g = s2.qualityMosaic("NDVI")
Map.addLayer(g, {"bands": ["nir", "red", "green"], "min": 0, "max": 0.45}, "Greenest pixel (false colour)")
'''),
    ("Unsupervised clustering (K-means)", '''img = lee.ImageCollection("sentinel-2").filterDate("2025-12-01", "2026-02-28").limit(6).median()
cl = img.cluster(k=6, bands=["blue", "green", "red", "nir", "swir1", "swir2"])
Map.addLayer(cl, {"min": 1, "max": 6, "palette": "Spectral"}, "K-means 6 clusters")
print(cl.classAreas())
'''),
    ("Surface-water occurrence 1984–2021 (JRC)", '''occ = lee.Image.load("surface-water", bands="occurrence")
Map.addLayer(occ, {"min": 0, "max": 100, "palette": "Blues"}, "Water occurrence (%)")
perm = occ.gt(90)
print("permanent water > 90% (ha):", round(perm.reduceRegion("sum")["occurrence"] * occ.grid.scale ** 2 / 1e4, 1))
'''),
    ("MODIS NDVI 2015–2025 (long time series)", '''lee.config(scale=250)
modis = lee.ImageCollection("modis-ndvi").filterDate("2015-01-01", "2025-12-31").select("NDVI")
ts = modis.timeSeries(lambda i: i.select("NDVI"), "mean", title="MODIS NDVI (AOI mean)")
print(len(ts), "16-day values")
'''),
    ("Vegetation & soil indices pack (EVI, SAVI, NDMI, NDBI, BSI)", """# Several indices from one dry-season composite, with expression() for the formulas
img = (lee.ImageCollection("sentinel-2").filterDate("2025-12-01", "2026-03-31")
        .filterMetadata("eo:cloud_cover", "lt", 30).limit(8).median())
idx = {
    "EVI":  "2.5*(nir-red)/(nir+6*red-7.5*blue+1)",
    "SAVI": "1.5*(nir-red)/(nir+red+0.5)",
    "NDMI": "(nir-swir1)/(nir+swir1)",          # leaf / soil moisture
    "NDBI": "(swir1-nir)/(swir1+nir)",          # built-up
    "BSI":  "((swir1+red)-(nir+blue))/((swir1+red)+(nir+blue))",   # bare soil
}
ramps = {"EVI": "RdYlGn", "SAVI": "RdYlGn", "NDMI": "BrBG", "NDBI": "Reds", "BSI": "YlOrBr"}
means = {}
for name, f in idx.items():
    im = img.expression(f).rename(name)
    Map.addLayer(im, {"min": -0.5, "max": 0.8, "palette": ramps[name]}, name)
    means[name] = round(im.reduceRegion("mean")[name], 3)
print("AOI means:", means)
Chart.bars(means, "Mean index value in the AOI", "index")
"""),
    ("Red-edge chlorophyll (NDRE, CIre) — crops", """# Sentinel-2 red-edge bands react to chlorophyll / nitrogen earlier than NDVI
lee.config(scale=20)                     # red-edge bands are 20 m
img = (lee.ImageCollection("sentinel-2").filterDate("2025-08-01", "2025-10-31")
        .filterMetadata("eo:cloud_cover", "lt", 40).limit(6).median())
ndre = img.normalizedDifference("rededge3", "rededge1").rename("NDRE")
cire = img.expression("rededge3/rededge1 - 1").rename("CIre")
Map.addLayer(ndre, {"min": 0, "max": 0.6, "palette": "RdYlGn"}, "NDRE")
Map.addLayer(cire, {"min": 0, "max": 3, "palette": "YlGn"}, "Chlorophyll index (red edge)")
print("mean NDRE:", round(ndre.reduceRegion("mean")["NDRE"], 3))
"""),
    ("Land surface temperature (Landsat thermal)", """# Landsat 8/9 band ST_B10 is surface temperature in Kelvin → °C. Hot spots = built-up / bare land
l9 = (lee.ImageCollection("landsat-9").filterDate("2026-03-01", "2026-04-30")
        .filterMetadata("eo:cloud_cover", "lt", 30).limit(4))
lst = (l9.select("ST_B10").median() - 273.15).rename("LST")
Map.addLayer(lst, {"min": 25, "max": 50, "palette": "Spectral_r"}, "Land surface temperature (°C)")
st = {k: round(lst.reduceRegion(k)["LST"], 1) for k in ("min", "mean", "max")}
print("LST °C:", st)
hot = lst.gt(st["mean"] + 5).rename("hot")
print("hot spots > mean + 5 °C (ha):", round(hot.reduceRegion("sum")["hot"] * lst.grid.scale ** 2 / 1e4, 1))
Map.addLayer(hot.updateMask(hot), {"palette": "Reds"}, "Heat hot spots")
"""),
    ("Urban expansion: built-up 2016 → 2025 (NDBI, Landsat 8)", """lee.config(scale=30)
def builtup(y):
    img = (lee.ImageCollection("landsat-8").filterDate("%d-12-01" % (y - 1), "%d-03-31" % y)
             .filterMetadata("eo:cloud_cover", "lt", 30).limit(6).median())
    ndbi = img.normalizedDifference("swir1", "nir")
    ndvi = img.normalizedDifference("nir", "red")
    return ndbi.gt(0).And(ndvi.lt(0.3)).rename("built")      # bright in SWIR, little vegetation
a, b = builtup(2016), builtup(2025)
ha = lambda m: round(m.reduceRegion("sum")["built"] * a.grid.scale ** 2 / 1e4, 1)
new = b.And(a.Not()).rename("built")
print("built-up 2016:", ha(a), "ha · 2025:", ha(b), "ha · new:", ha(new), "ha")
Chart.bars({"2016": ha(a), "2025": ha(b)}, "Built-up area (ha)", "ha")
Map.addLayer(new.updateMask(new), {"palette": "Reds"}, "New built-up 2016–2025")
"""),
    ("Forest loss 2017 → 2024 (annual land cover)", """# Impact Observatory / Esri land cover: class 2 = trees
a = lee.Image.load("lulc-annual", 2017)
b = lee.Image.load("lulc-annual", 2024)
loss = a.eq(2).And(b.neq(2)).rename("loss")
gain = a.neq(2).And(b.eq(2)).rename("gain")
px = a.grid.scale ** 2 / 1e4
L = round(loss.reduceRegion("sum")["loss"] * px, 1)
G = round(gain.reduceRegion("sum")["gain"] * px, 1)
print("tree cover lost:", L, "ha · gained:", G, "ha · net:", round(G - L, 1), "ha")
Chart.bars({"Loss": L, "Gain": G}, "Tree cover 2017–2024 (ha)", "ha")
Map.addLayer(loss.updateMask(loss), {"palette": "Reds"}, "Tree loss 2017–2024")
Map.addLayer(gain.updateMask(gain), {"palette": "Greens"}, "Tree gain 2017–2024")
print("2024 land cover:", b.classAreas())
"""),
    ("Deforestation from NDVI drop (Landsat 2015 vs 2025)", """lee.config(scale=30)
def ndvi(sensor, y):
    return (lee.ImageCollection(sensor).filterDate("%d-12-01" % (y - 1), "%d-03-31" % y)
              .filterMetadata("eo:cloud_cover", "lt", 30).limit(6).median()
              .normalizedDifference("nir", "red").rename("NDVI"))
n1, n2 = ndvi("landsat-8", 2015), ndvi("landsat-9", 2025)
d = (n2 - n1).rename("dNDVI")
Map.addLayer(d, {"min": -0.5, "max": 0.5, "palette": "RdYlGn"}, "NDVI change 2015 → 2025")
cleared = n1.gt(0.6).And(d.lt(-0.3)).rename("cleared")     # was dense vegetation, lost > 0.3 NDVI
print("likely cleared (ha):", round(cleared.reduceRegion("sum")["cleared"] * n1.grid.scale ** 2 / 1e4, 1))
Map.addLayer(cleared.updateMask(cleared), {"palette": "Reds"}, "Likely clearing")
"""),
    ("Rice / crop calendar from Sentinel-1 VH", """# Radar works in the rainy season. Rice: VH drops at flooding/transplanting, rises as the crop grows,
# falls after harvest. Draw a small AOI on a rice field for a clean curve.
lee.config(scale=20)
s1 = lee.ImageCollection("sentinel-1").filterDate("2025-05-01", "2026-01-31")
ts = s1.timeSeries(lambda i: i.select("VH"), "mean", title="Sentinel-1 VH (dB) — AOI mean", ylabel="VH (dB)")
for d, v in ts:
    print(d, round(v, 2))
lo = min(ts, key=lambda x: x[1]) if ts else None
print("lowest VH (likely flooding / transplanting):", lo)
"""),
    ("Sentinel-1 radar colour composite (VV, VH, ratio)", """# Radar sees through clouds. R = VV, G = VH, B = VV−VH (dB): urban bright/pink, forest green, water dark
lee.config(scale=20)
s1 = lee.ImageCollection("sentinel-1").filterDate("2025-07-01", "2025-08-31").select("VV", "VH").median()
ratio = (s1.select("VV") - s1.select("VH")).rename("ratio")
rgb = s1.select("VV").rename("vv").addBands(s1.select("VH").rename("vh")).addBands(ratio)
Map.addLayer(rgb, {"min": -22, "max": 5}, "S1 VV / VH / ratio")
Map.addLayer(s1.select("VH"), {"min": -25, "max": -10}, "VH (dB)")
"""),
    ("Optical + radar fusion clustering (S2 + S1)", """# Stack Sentinel-2 reflectance with Sentinel-1 backscatter, then K-means on all bands
lee.config(scale=20)
s2 = (lee.ImageCollection("sentinel-2").filterDate("2025-12-01", "2026-02-28")
        .filterMetadata("eo:cloud_cover", "lt", 30).limit(6).median()
        .select("blue", "green", "red", "nir", "swir1", "swir2"))
s1 = lee.ImageCollection("sentinel-1").filterDate("2025-12-01", "2026-02-28").select("VV", "VH").median()
stack = s2.addBands(s1)
cl = stack.cluster(k=8)
Map.addLayer(cl, {"min": 1, "max": 8, "palette": "Spectral"}, "S2 + S1 clusters (k = 8)")
print("cluster areas (ha):", cl.classAreas())
print("Label the clusters in tab 3 → Unsupervised, or use them as a guide for training areas.")
"""),
    ("Seasonal water: dry vs rainy season (MNDWI)", """# Water extent in the dry season (March) and at the flood peak (October) — e.g. Tonle Sap
lee.config(scale=20)
def water(start, end):
    img = (lee.ImageCollection("sentinel-2").filterDate(start, end)
             .filterMetadata("eo:cloud_cover", "lt", 50).limit(6).median())
    return img.normalizedDifference("green", "swir1").gt(0).rename("water")
dry, wet = water("2026-02-15", "2026-04-15"), water("2025-09-15", "2025-11-15")
ha = lambda m: round(m.reduceRegion("sum")["water"] * dry.grid.scale ** 2 / 1e4, 1)
seasonal = wet.And(dry.Not()).rename("water")
print("dry season:", ha(dry), "ha · rainy season:", ha(wet), "ha · seasonal only:", ha(seasonal), "ha")
Chart.bars({"Dry": ha(dry), "Rainy": ha(wet)}, "Open water (ha)", "ha")
Map.addLayer(dry.updateMask(dry), {"palette": "Blues"}, "Water — dry season")
Map.addLayer(seasonal.updateMask(seasonal), {"palette": "PuBu"}, "Seasonal (flooded) water")
"""),
    ("Water seasonality & permanent water (JRC)", """sea = lee.Image.load("surface-water", bands="seasonality")   # months per year with water (0–12)
Map.addLayer(sea, {"min": 1, "max": 12, "palette": "Blues"}, "Water seasonality (months / year)")
px = sea.grid.scale ** 2 / 1e4
perm = sea.gte(12).rename("m")
seas = sea.gte(1).And(sea.lt(12)).rename("m")
print("permanent water (12 months):", round(perm.reduceRegion("sum")["m"] * px, 1), "ha")
print("seasonal water (1–11 months):", round(seas.reduceRegion("sum")["m"] * px, 1), "ha")
"""),
    ("Elevation zones & low-lying flood-prone land (DEM)", """lee.config(scale=30)
dem = lee.Image.load("dem")
zones = dem.expression("where(elevation < 10, 1, where(elevation < 50, 2, where(elevation < 200, 3, "
                       "where(elevation < 500, 4, 5))))").rename("zone").updateMask(dem.gte(-500))
names = {1: "< 10 m", 2: "10–50 m", 3: "50–200 m", 4: "200–500 m", 5: "> 500 m"}
areas = {names[int(k)]: round(v, 1) for k, v in zones.classAreas().items()}
print("area by elevation zone (ha):", areas)
Chart.bars(areas, "Area by elevation zone (ha)", "ha")
Map.addLayer(zones, {"min": 1, "max": 5, "palette": "Spectral_r"}, "Elevation zones")
flat = dem.slope().lt(2).And(dem.lt(10)).rename("low")
Map.addLayer(flat.updateMask(flat), {"palette": "Blues"}, "Low & flat (< 10 m, slope < 2°)")
"""),
    ("Drought check: NDVI anomaly vs 10-year mean (MODIS)", """# Dry-season NDVI this year compared with the 2015–2024 average for the same months
lee.config(scale=250)
def season(y):
    return (lee.ImageCollection("modis-ndvi").filterDate("%d-01-01" % y, "%d-04-30" % y)
              .select("NDVI").median())
ref = [season(y) for y in range(2015, 2025)]
mean = ref[0]
for r in ref[1:]:
    mean = mean + r
mean = (mean / len(ref)).rename("NDVI")
now = season(2026)
anom = (now - mean).rename("anomaly")
Map.addLayer(anom, {"min": -0.2, "max": 0.2, "palette": "BrBG"}, "NDVI anomaly Jan–Apr 2026")
print("mean anomaly:", round(anom.reduceRegion("mean")["anomaly"], 3), "(negative = drier / less green)")
"""),
    ("Historical look: Landsat 5 (1990s) vs today", """lee.config(scale=30)
old = (lee.ImageCollection("landsat-5").filterDate("1995-11-01", "1997-03-31")
         .filterMetadata("eo:cloud_cover", "lt", 30).limit(6).median())
new = (lee.ImageCollection("landsat-9").filterDate("2025-11-01", "2026-03-31")
         .filterMetadata("eo:cloud_cover", "lt", 30).limit(6).median())
Map.addLayer(old, {"bands": ["red", "green", "blue"], "min": 0, "max": 0.25}, "Landsat 5 · 1996")
Map.addLayer(new, {"bands": ["red", "green", "blue"], "min": 0, "max": 0.25}, "Landsat 9 · 2026")
n0 = old.normalizedDifference("nir", "red").reduceRegion("mean")["nd"]
n1 = new.normalizedDifference("nir", "red").reduceRegion("mean")["nd"]
print("mean NDVI 1996:", round(n0, 3), "· 2026:", round(n1, 3))
"""),
    ("Data quality: clear observations per pixel", """# How many cloud-free Sentinel-2 views each pixel had — plan field work / composites with it
lee.config(scale=30)
s2 = (lee.ImageCollection("sentinel-2").filterDate("2025-06-01", "2025-10-31")
        .filterMetadata("eo:cloud_cover", "lt", 80).select("red"))
print("scenes:", s2.size())
n = s2.count().rename("clear")
Map.addLayer(n, {"min": 0, "max": 15, "palette": "Viridis"}, "Clear observations (rainy season)")
print("clear views per pixel:", {k: round(n.reduceRegion(k)["clear"], 1) for k in ("min", "mean", "max")})
"""),
    ("Haze-free composite (25th percentile)", """# In hazy / smoky months the median can stay bright; a low percentile keeps the clearest values
s2 = (lee.ImageCollection("sentinel-2").filterDate("2026-02-01", "2026-04-30")
        .filterMetadata("eo:cloud_cover", "lt", 40).limit(10))
Map.addLayer(s2.median(), {"bands": ["red", "green", "blue"], "min": 0, "max": 0.3}, "Median")
Map.addLayer(s2.percentile(25), {"bands": ["red", "green", "blue"], "min": 0, "max": 0.3}, "25th percentile")
"""),
    ("Mangroves & wetlands (ESA WorldCover)", """wc = lee.Image.load("worldcover")
mg = wc.eq(95).rename("m")        # 95 = mangroves
wl = wc.eq(90).rename("m")        # 90 = herbaceous wetland
px = wc.grid.scale ** 2 / 1e4
print("mangroves:", round(mg.reduceRegion("sum")["m"] * px, 1), "ha · wetland:",
      round(wl.reduceRegion("sum")["m"] * px, 1), "ha")
Map.addLayer(mg.updateMask(mg), {"palette": "Greens"}, "Mangroves")
Map.addLayer(wl.updateMask(wl), {"palette": "PuBuGn"}, "Herbaceous wetland")
"""),
    ("Use a map from the project (Image.open)", '''# Bring a layer from the QGIS project (or a .tif path) into LEE, e.g. your classification
# from tab 3, and compare it with ESA WorldCover. Change the name to one of your layers.
mine = lee.Image.open("Classification (Random Forest)")
print(mine.classAreas())
Chart.bars(mine.classAreas(), "My classification (ha)", "ha")
wc = lee.Image.load("worldcover")
Map.addLayer(wc, {}, "ESA WorldCover")
'''),
    ("List the datasets", '''print(lee.datasets())
print()
print("Sentinel-2 bands can be called by name (B4) or role (red). Landsat: SR_B4 or red.")
'''),
]
