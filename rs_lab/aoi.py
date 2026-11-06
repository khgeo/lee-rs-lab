"""Area of interest: Cambodia admin boundaries (HDX COD-AB -> Open Development Cambodia ->
geoBoundaries, or a local file — no login) and a rectangle-drawing map tool."""
import json
import os
import re
import urllib.request
import zipfile

from osgeo import ogr
from qgis.core import QgsApplication, QgsGeometry, QgsRectangle, QgsTask
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

from .compat import E, can_cancel, web_url
from .i18n import tr

HERE = os.path.dirname(__file__)
CACHE = os.path.join(QgsApplication.qgisSettingsDirPath(), "cache", "rs_lab")
WHOLE = "__whole__"


def _poly():
    try:
        from qgis.core import Qgis
        return Qgis.GeometryType.Polygon
    except Exception:  # noqa: BLE001
        from qgis.core import QgsWkbTypes
        return QgsWkbTypes.PolygonGeometry


def sources():
    with open(os.path.join(HERE, "data", "sources.json"), encoding="utf-8") as f:
        return json.load(f)["admin_boundaries"]["sources"]


# ------------------------------------------------------------------ vector (CKAN: HDX / ODC / local file)
KHMER = re.compile("[\u1780-\u17FF]")
LEVEL_WORDS = {0: ("adm0", "admin0", "adm_0", "admbnda_adm0", "country", "national"),
               1: ("province", "provinces", "khet", "adm1", "admin1", "adm_1", "admbnda_adm1"),
               2: ("district", "districts", "srok", "adm2", "admin2", "adm_2", "admbnda_adm2"),
               3: ("commune", "communes", "khum", "sangkat", "adm3", "admin3", "adm_3", "admbnda_adm3")}


def _ua():
    return {"User-Agent": "QGIS-RS-Lab/1.2"}


def _download(url, fn, timeout=600):
    req = urllib.request.Request(web_url(url), headers=_ua())  # noqa: S310  # nosec B310
    with urllib.request.urlopen(req, timeout=timeout) as r, open(fn + ".part", "wb") as f:  # noqa: S310  # nosec B310
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(fn + ".part", fn)
    return fn


def _level_of(text):
    t = text.lower()
    m = re.search(r"adm(?:in)?_?([0123])(?!\d)", t)
    if m:
        return int(m.group(1))
    for lev in (3, 2, 1, 0):
        if any(w in t for w in LEVEL_WORDS[lev]):
            return lev
    return None


def _clean_code(v):
    return re.sub(r"[^0-9]", "", str(v or ""))


class VectorAdmin:
    """Admin boundaries read from vector files (shapefile / GeoPackage / GeoJSON / FileGDB).

    Works with the OCHA COD-AB schema (ADM1_EN, ADM1_KM, ADM1_PCODE … or adm1_name, adm1_name1,
    adm1_pcode …) and, as a fallback, with any layer that has an English name field, a Khmer name
    field and a numeric code field (Cambodian codes nest: province 2 digits, district 4, commune 6).
    Units are matched to their parent by P-code, so equal names in different provinces are safe."""
    kind = "vector"

    def __init__(self, src, folder):
        self.src = src
        self.name = src.get("name", "vector")
        self.attribution = src.get("attribution", "")
        self.dir = folder
        self.layers = {}   # level -> dict(path, layer, en, km, code, parent)
        self._recs = {}    # level -> list of records

    # --------------------------------------------------------- discovery
    def ready(self):
        return self._scan()

    def _candidates(self):
        for root, dirs, files in os.walk(self.dir):
            for d in list(dirs):
                if d.lower().endswith(".gdb"):
                    yield os.path.join(root, d)
            for fn in files:
                if fn.lower().endswith((".shp", ".gpkg", ".geojson", ".json")) and not fn.endswith(".part"):
                    yield os.path.join(root, fn)

    def _scan(self):
        if not os.path.isdir(self.dir):
            return False
        found = {}
        for p in self._candidates():
            ds = ogr.Open(p)
            if ds is None:
                continue
            for i in range(ds.GetLayerCount()):
                lyr = ds.GetLayer(i)
                gt = ogr.GT_Flatten(lyr.GetGeomType())
                if gt not in (ogr.wkbPolygon, ogr.wkbMultiPolygon, ogr.wkbUnknown):
                    continue
                lev = _level_of(lyr.GetName()) or _level_of(os.path.basename(p))
                if lev is None or lev in found and not p.lower().endswith(".gpkg"):
                    continue
                f = self._fields(lyr, lev)
                if f:
                    found[lev] = dict(path=p, layer=lyr.GetName(), **f)
            ds = None
        self.layers = found
        self._recs = {}
        return 1 in found

    def _fields(self, lyr, lev):
        defn = lyr.GetLayerDefn()
        names = [defn.GetFieldDefn(k).GetName() for k in range(defn.GetFieldCount())]
        up = {n.upper(): n for n in names}

        def pick(cands):
            for c in cands:
                if c.upper() in up:
                    return up[c.upper()]
            return None
        f = dict(en=pick(["ADM%d_EN" % lev, "adm%d_name" % lev, "ADM%d_NAME" % lev, "NAME_%d" % lev, "shapeName"]),
                 km=pick(["ADM%d_KM" % lev, "ADM%d_KH" % lev, "adm%d_name1" % lev, "ADM%d_REF" % lev]),
                 code=pick(["ADM%d_PCODE" % lev, "adm%d_pcode" % lev, "shapeID", "GID_%d" % lev]),
                 parent=pick(["ADM%d_PCODE" % (lev - 1), "adm%d_pcode" % (lev - 1)]) if lev > 1 else None)
        # look at a few features to fill gaps (ODC and other non-COD schemas)
        sample = []
        lyr.ResetReading()
        for k, feat in enumerate(lyr):
            sample.append({n: feat.GetField(n) for n in names})
            if k >= 30:
                break
        lyr.ResetReading()
        if not sample:
            return None

        def is_text(n, khmer):
            vals = [r[n] for r in sample if isinstance(r[n], str) and r[n].strip()]
            if len(vals) < max(1, len(sample) // 2):
                return False
            hits = sum(1 for v in vals if KHMER.search(v))
            return hits > len(vals) / 2 if khmer else hits == 0
        if f["km"] and not is_text(f["km"], True):
            f["km"] = None
        if not f["km"]:
            f["km"] = next((n for n in names if is_text(n, True)), None)
        if not f["en"]:
            pref = [n for n in names if "name" in n.lower() and is_text(n, False)]
            pref.sort(key=lambda n: (not n.lower().endswith(("_en", "_e", "en")), len(n)))
            f["en"] = pref[0] if pref else None
        if not f["code"]:
            codes = [n for n in names if "code" in n.lower() and all(_clean_code(r[n]) for r in sample)]
            # the most specific code field has the most digits
            codes.sort(key=lambda n: -max(len(_clean_code(r[n])) for r in sample))
            f["code"] = codes[0] if codes else None
        if not f["en"] and not f["km"]:
            return None
        return f

    # --------------------------------------------------------- records
    def records(self, level):
        if level not in self._recs:
            L = self.layers[level]
            ds = ogr.Open(L["path"])
            lyr = ds.GetLayerByName(L["layer"])
            out = []
            for feat in lyr:
                en = feat.GetField(L["en"]) if L["en"] else None
                km = feat.GetField(L["km"]) if L["km"] else None
                code = feat.GetField(L["code"]) if L["code"] else None
                par = feat.GetField(L["parent"]) if L["parent"] else None
                out.append(dict(fid=feat.GetFID(), en=(en or km or "").strip(), km=(km or "").strip(),
                                code=str(code) if code is not None else "fid%d" % feat.GetFID(), parent=par))
            ds = None
            self._recs[level] = out
            if level >= 2 and not L["parent"] and (level - 1) in self.layers:
                self._link_parents(level)
        return self._recs[level]

    def _link_parents(self, level):
        """No parent field: use nested numeric codes if they nest, else find each unit's parent
        polygon spatially (geoBoundaries and other global data have no parent codes)."""
        kids, pars = self._recs[level], self.records(level - 1)
        pcodes = {_clean_code(p["code"]).lstrip("0") for p in pars}
        nested = sum(1 for k in kids if _clean_code(k["code"]).lstrip("0")[:-2] in pcodes)
        if kids and nested >= 0.5 * len(kids):
            return
        P = self.layers[level - 1]
        pds = ogr.Open(P["path"])
        plyr = pds.GetLayerByName(P["layer"])
        polys = []
        for rec in pars:
            feat = plyr.GetFeature(rec["fid"])          # keep the feature alive while using its geometry
            g = feat.GetGeometryRef() if feat is not None else None
            if g is not None:
                g = g.Clone()
                polys.append((rec["code"], g, g.GetEnvelope()))
        K = self.layers[level]
        kds = ogr.Open(K["path"])
        klyr = kds.GetLayerByName(K["layer"])
        for rec in kids:
            feat = klyr.GetFeature(rec["fid"])
            g = feat.GetGeometryRef() if feat is not None else None
            if g is None:
                continue
            pt = g.PointOnSurface()
            x, y = pt.GetX(), pt.GetY()
            for code, pg, (x0, x1, y0, y1) in polys:
                if x0 <= x <= x1 and y0 <= y <= y1 and pg.Contains(pt):
                    rec["parent"] = code
                    break
        pds = kds = None

    def has_level(self, level):
        return level in self.layers

    def _is_child(self, rec, parent_code, level):
        if rec["parent"] is not None:
            return str(rec["parent"]) == str(parent_code)
        # Cambodian codes nest by two digits: province 8 → district 801 → commune 80101
        a, b = _clean_code(rec["code"]).lstrip("0"), _clean_code(parent_code).lstrip("0")
        return bool(a and b) and len(a) > 2 and a[:-2] == b

    def units(self, level, parent_code=None):
        """[(code, english, khmer)] sorted by English name."""
        if level not in self.layers:
            return []
        if level == 1:
            parent_code = None
        seen, out = set(), []
        for r in self.records(level):
            if parent_code is not None and not self._is_child(r, parent_code, level):
                continue
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            out.append((r["code"], r["en"], r["km"]))
        return sorted(out, key=lambda t: t[1].lower())

    def geometry(self, level, code):
        """code None → the whole level (used for the country: level 0, or all provinces dissolved)."""
        from osgeo import osr
        if level == 0 and 0 not in self.layers:
            level, code = 1, None
        L = self.layers[level]
        fids = [r["fid"] for r in self.records(level) if code is None or r["code"] == code]
        ds = ogr.Open(L["path"])
        lyr = ds.GetLayerByName(L["layer"])
        srs = lyr.GetSpatialRef()
        tr4326 = None
        if srs is not None:
            t = osr.SpatialReference()
            t.ImportFromEPSG(4326)
            t.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            if not srs.IsSame(t):
                tr4326 = osr.CoordinateTransformation(srs, t)
        geoms = []
        for fid in fids:
            feat = lyr.GetFeature(fid)
            g = feat.GetGeometryRef().Clone()
            if tr4326 is not None:
                g.Transform(tr4326)
            geoms.append(QgsGeometry.fromWkt(g.ExportToWkt()))
        ds = None
        return QgsGeometry.unaryUnion(geoms) if geoms else None

    def describe(self):
        return ", ".join("L%d: %s (%s)" % (k, os.path.basename(v["path"]), v["layer"]) for k, v in sorted(self.layers.items()))


class CkanAdmin(VectorAdmin):
    """Downloads a CKAN package (HDX, Open Development Cambodia) once and caches it."""
    kind = "ckan"

    def __init__(self, src, iso3="KHM"):
        src = dict(src)
        src["package"] = src["package"].replace("{iso3}", iso3.lower()).replace("{ISO3}", iso3.upper())
        super().__init__(src, os.path.join(CACHE, src.get("cache", "ckan").replace("{iso3}", iso3.lower()),
                                           src["package"]))

    def download(self, task=None):
        os.makedirs(self.dir, exist_ok=True)
        req = urllib.request.Request(web_url(self.src["api"] + self.src["package"]), headers=_ua())  # noqa: S310  # nosec B310
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310  # nosec B310
            pkg = json.load(r)["result"]

        def score(x):
            n = (x.get("name", "") + " " + x.get("url", "")).lower()
            f = (x.get("format", "") or "").lower()
            s = 0
            if "gpkg" in f or "geopackage" in f or ".gpkg" in n:
                s = 5
            elif "shp" in f or "shapefile" in f or "shp.zip" in n:
                s = 4
            elif "geojson" in f or "geojson" in n:
                s = 3
            elif "gdb" in f or "geodatabase" in f or ".gdb" in n:
                s = 2
            if s and re.search(r"(^|[_ .-])(em|lines?|emf|line)([_ .-]|$)", n):
                s -= 2  # boundary lines, not polygons
            if s and ("xlsx" in n or "csv" in n or "pdf" in n):
                s = 0
            return s
        res = [x for x in sorted(pkg.get("resources", []), key=score, reverse=True) if score(x) > 0]
        if not res:
            raise IOError("No usable boundary file in package %s" % self.src["package"])
        errors = []
        for x in res:
            if task is not None and task.isCanceled():
                break
            url = x["url"]
            fn = os.path.join(self.dir, os.path.basename(url.split("?")[0]) or "boundaries.zip")
            try:
                if not os.path.exists(fn):
                    _download(url, fn)
                if fn.lower().endswith(".zip"):
                    sub = os.path.join(self.dir, os.path.splitext(os.path.basename(fn))[0])
                    if not os.path.isdir(sub):
                        with zipfile.ZipFile(fn) as z:
                            z.extractall(sub)
            except Exception as e:  # noqa: BLE001
                errors.append("%s: %s" % (os.path.basename(fn), e))
                continue
            if self._scan() and all(l in self.layers for l in (1, 2, 3)):
                break
        if not self._scan():
            raise IOError("; ".join(errors) or "no polygon layers with names found")
        return True


class GeoBoundariesAdmin(VectorAdmin):
    """geoBoundaries (William & Mary geoLab) — open boundaries for every country, CC BY 4.0, no login.
    Levels ADM0–ADM3 are downloaded as simplified GeoJSON and cached; parents are linked spatially."""
    kind = "geoboundaries"

    def __init__(self, src, iso3):
        self.iso3 = iso3.upper()
        super().__init__(dict(src, name="%s — %s" % (src["name"], self.iso3)),
                         os.path.join(CACHE, "geoboundaries", self.iso3))

    def download(self, task=None):
        os.makedirs(self.dir, exist_ok=True)
        got, errors = 0, []
        for lev in range(0, self.src.get("max_level", 3) + 1):
            if task is not None and task.isCanceled():
                break
            fn = os.path.join(self.dir, "%s_ADM%d.geojson" % (self.iso3, lev))
            if os.path.exists(fn):
                got += 1
                continue
            try:
                req = urllib.request.Request(web_url(self.src["api"] % (self.iso3), lev), headers=_ua())  # noqa: S310  # nosec B310
                with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310  # nosec B310
                    meta = json.load(r)
                if isinstance(meta, list):
                    meta = meta[0] if meta else {}
                url = meta.get("simplifiedGeometryGeoJSON") or meta.get("gjDownloadURL")
                if not url:
                    raise IOError("no download link")
                _download(url, fn)
                if lev == 0 and meta.get("boundaryLicense"):
                    self.attribution = "%s (%s)" % (self.src.get("attribution", ""), meta["boundaryLicense"])
                got += 1
            except Exception as e:  # noqa: BLE001
                errors.append("ADM%d: %s" % (lev, e))
                if lev >= 2:
                    break          # deeper levels do not exist for this country
        if not got:
            raise IOError("; ".join(errors) or "nothing downloaded")
        return self._scan()


class FileAdmin(VectorAdmin):
    """A boundary file the user chose (e.g. downloaded by hand from HDX or ODC)."""
    kind = "file"

    def __init__(self, path):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        super().__init__({"name": os.path.basename(path), "attribution": "Local file: %s" % path}, folder)
        self.only = None if os.path.isdir(path) else path

    def _candidates(self):
        if self.only:
            if self.only.lower().endswith(".zip"):
                yield "/vsizip/" + self.only
            else:
                yield self.only
            return
        yield from super()._candidates()


class AdminLoadTask(QgsTask):
    """Try the sources in data/sources.json in order (or only the one asked for) for one country.
    Sources with a "countries" list (ODC, the Cambodia GEE assets) are used only for those countries."""

    def __init__(self, only=None, file_path=None, iso3="KHM", country="Cambodia"):
        super().__init__(tr("Loading administrative boundaries"), can_cancel())
        self.backend, self.errors = None, []
        self.only, self.file_path = only, file_path
        self.iso3, self.country = iso3.upper(), country

    def run(self):
        if self.file_path:
            try:
                b = FileAdmin(self.file_path)
                if b.ready():
                    b.iso3 = self.iso3
                    self.backend = b
                    return True
                self.errors.append("%s: no polygon layer with names found" % self.file_path)
            except Exception as e:  # noqa: BLE001
                self.errors.append(str(e))
            return False
        for s in sources():
            if self.only and s.get("id") != self.only:
                continue
            if s.get("countries") and self.iso3 not in s["countries"]:
                continue
            if self.isCanceled():
                return False
            try:
                if s["type"] == "ckan":
                    b = CkanAdmin(s, self.iso3)
                    if not b.ready() or not all(b.has_level(l) for l in (1, 2)):
                        b.download(self)
                    if b.ready():
                        self.backend = b
                elif s["type"] == "geoboundaries":
                    b = GeoBoundariesAdmin(s, self.iso3)
                    if not b.ready() or not b.has_level(2):
                        b.download(self)
                    if b.ready():
                        self.backend = b
                if self.backend is not None:
                    self.backend.iso3 = self.iso3
                    return True
            except Exception as e:  # noqa: BLE001
                self.errors.append("%s: %s" % (s["name"], e))
        return False


def countries():
    with open(os.path.join(HERE, "data", "countries.json"), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ draw rectangle tool
class RectangleTool(QgsMapTool):
    drawn = pyqtSignal(QgsRectangle)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.rb = QgsRubberBand(canvas, _poly())
        self.rb.setColor(QColor(255, 120, 0, 60))
        self.rb.setStrokeColor(QColor(255, 120, 0))
        self.rb.setWidth(2)
        self.start = None
        self.setCursor(E(Qt, "CursorShape.CrossCursor"))

    def canvasPressEvent(self, e):
        self.start = self.toMapCoordinates(e.pos())
        self.rb.reset(_poly())

    def canvasMoveEvent(self, e):
        if self.start is None:
            return
        p = self.toMapCoordinates(e.pos())
        r = QgsRectangle(self.start, p)
        self.rb.setToGeometry(QgsGeometry.fromRect(r), None)

    def canvasReleaseEvent(self, e):
        if self.start is None:
            return
        p = self.toMapCoordinates(e.pos())
        r = QgsRectangle(self.start, p)
        self.start = None
        if r.width() > 0 and r.height() > 0:
            self.drawn.emit(r)

    def deactivate(self):
        self.rb.reset(_poly())
        super().deactivate()

