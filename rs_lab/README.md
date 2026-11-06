# LEE RS Lab (QGIS plugin)

A remote sensing lab inside QGIS for teaching and research — **no account, no login**. English / ខ្មែរ.

## Install
QGIS → Plugins → Manage and Install Plugins → **Install from ZIP** → `rs_lab.zip`. The plugin has its own
**LEE RS Lab** toolbar with one icon (basemaps are in the panel header). Optional: 1 Data › Setup → *Install scikit-learn* (Random Forest / SVM / kNN).

## Data (all free, no login)
| Kind | Source |
|---|---|
| Sentinel-2 L2A, Landsat 5/7/8/9 C2 L2, Sentinel-1 RTC, MODIS NDVI, Copernicus DEM, NASADEM, ESA WorldCover, annual land cover, JRC surface water | Microsoft Planetary Computer (STAC + Cloud-Optimised GeoTIFF) |
| Sentinel-2 L2A | Earth Search — Element 84 / AWS Open Data |
| Admin boundaries | HDX COD-AB → Open Development Cambodia (KHM) → geoBoundaries (global), or your own file |
| Basemaps | Esri, Google, EOX Sentinel-2 cloudless, OSM, OpenTopoMap, CARTO |
| Local | band files from EarthExplorer / Copernicus Browser / SAFE folders |

Only the pixels inside the AOI are read; cloud masks are checked first; composites and analyses run on your computer.

## Tabs (each tab is a set of collapsible sections; one opens at a time)
| Tab | Sections |
|---|---|
| 1 Data | Area of interest · Imagery — search scenes · Composite & download · More datasets · Setup |
| 2 Enhance | Band combination & stretch · Spectral indices & band math · Spatial filter · PCA |
| 3 Classify | Classes (legend, schemes) · Unsupervised · Supervised — classes & training (incl. automatic training from ESA WorldCover) · Supervised — classify · Accuracy (Cochran sample design, point labelling, area-adjusted estimates, Olofsson et al. 2014) · Clean up & export polygons · Map layout |
| 4 Change | From → to (matrix, gains/losses, largest transitions, one-transition map) · image differencing |
| 5 LEE | Script editor, 32 examples, `lee.Image.open()` for project layers, send results to the other tabs |

Header: language · basemaps · Help · PDF report.

## LEE — Local Earth Engine (tab 5)
GEE-style scripts that run locally:
```python
s2 = (lee.ImageCollection("sentinel-2").filterDate("2025-11-01", "2026-03-31")
        .filterBounds(aoi).filterMetadata("eo:cloud_cover", "lt", 30).limit(8))
img = s2.median()
ndvi = img.normalizedDifference("nir", "red").rename("NDVI")
Map.addLayer(ndvi, {"min": 0, "max": 0.9, "palette": "RdYlGn"}, "NDVI")
print(ndvi.reduceRegion("mean"))
ts = s2.timeSeries()          # NDVI chart
dem = lee.Image.load("dem"); Map.addLayer(dem.hillshade(), {}, "Hillshade")
```
Image: select, rename, addBands, + − × ÷, gt/lt/eq, And/Or, updateMask, unmask, where, remap, normalizedDifference,
expression, focal_mean/median, gaussian, edges, slope, aspect, hillshade, reduceRegion, classAreas, histogram, otsu,
cluster, save. ImageCollection: filterDate, filterBounds, filterMetadata, filterCloud, limit, select, map, maskClouds,
median/mean/min/max/percentile/mosaic/count/qualityMosaic, toList, first, timeSeries, monthly, size, dates, getInfo.
Differences from GEE: eager computation on the AOI grid (UTM, `lee.config(scale=…)`), reflectance already 0–1.

Conventions follow EIA Screening 0.7.1: right-docked panel, scrollable tabs, English/ខ្មែរ switch (rebuilds in place,
renames layers, translates legends), reset on new project, admin AOI, sources catalogue `data/sources.json`, PDF report.

Author: YAM Sarath · github.com/khgeo
