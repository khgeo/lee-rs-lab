"""Main dock panel: Setup · Download · Composite · Indices · Filters/PCA · Unsupervised ·
Supervised · Accuracy · Change.  Rebuilt in place when the language changes."""
import csv
import os
import random
import re
import tempfile

import numpy as np
from qgis.core import (QgsSettings, QgsContrastEnhancement, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsField, QgsFields, QgsGeometry, QgsMultiBandColorRenderer,
                       QgsProject, QgsRasterMinMaxOrigin, QgsSymbol,
                       QgsVectorFileWriter, QgsVectorLayer)
from qgis.gui import QgsFileWidget, QgsMapLayerComboBox, QgsFieldComboBox
from qgis.PyQt.QtCore import QDate, QPoint, QRect, QSize
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QAbstractButton, QAbstractItemView, QCheckBox, QColorDialog, QComboBox,
                                 QDateEdit, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                                 QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                                 QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                                 QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from . import aoi as aoimod
from . import catalog, deps, plots, procs, report, tasks
from . import raster_io as rio
from .compat import (CHECKABLE, CHECKED, EDITABLE, NO_EDIT, RESIZE_CONTENTS, SCROLL_OFF, SELECT_ROWS,
                     UNCHECKED, USER_ROLE, YES, NO, E, Qt, field_filter_numeric, layer_filter,
                     set_layer_filters, label_font)
from .i18n import language, set_language, tr
from .lessons import lesson

GROUP = "LEE RS Lab"
HERE = os.path.dirname(__file__)


def _ftype(kind):
    from qgis.core import Qgis
    if Qgis.QGIS_VERSION_INT >= 33800:
        from qgis.PyQt.QtCore import QMetaType
        return E(QMetaType, "Type.Int" if kind == "int" else "Type.QString")
    from qgis.PyQt.QtCore import QVariant
    return QVariant.Int if kind == "int" else QVariant.String


def _multipolygon():
    try:
        from qgis.core import Qgis
        return Qgis.WkbType.MultiPolygon
    except Exception:  # noqa: BLE001
        from qgis.core import QgsWkbTypes
        return QgsWkbTypes.MultiPolygon


def default_out_dir():
    from qgis.core import QgsSettings
    d = QgsSettings().value("rs_lab/out_dir", "", type=str)
    return d or os.path.join(os.path.expanduser("~"), "RS_Lab")


# ============================================================ small widget helpers
def btn(text, cb=None, tip=None, primary=False):
    b = QPushButton(text)
    if cb:
        b.clicked.connect(cb)
    if tip:
        b.setToolTip(tip)
    if primary:
        b.setStyleSheet("QPushButton{background:#1f6fb2;color:white;font-weight:bold;padding:5px 10px;"
                        "border-radius:4px}QPushButton:disabled{background:#9db8cf}")
    return b


class FlowLayout(QLayout):
    """Lays buttons out left to right and wraps them onto the next line when the dock is narrow,
    so a row of buttons never forces the panel to be wide."""

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        try:
            return Qt.Orientations()            # Qt5
        except AttributeError:
            return Qt.Orientation(0)            # Qt6

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        w = h = 0
        for it in self._items:
            s = it.sizeHint()
            w += s.width() + self.spacing()
            h = max(h, s.height())
        return QSize(w, h)

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, line_h, sp = rect.x(), rect.y(), 0, self.spacing()
        for it in self._items:
            if it.isEmpty():
                continue
            hint = it.sizeHint()
            nx = x + hint.width()
            if nx > rect.right() + 1 and line_h > 0:
                x, y = rect.x(), y + line_h + sp
                nx, line_h = x + hint.width(), 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nx + sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


def row(*widgets):
    """A row of widgets. A row made only of buttons wraps (FlowLayout); mixed rows stay horizontal."""
    real = [w for w in widgets if not (isinstance(w, str) and w == "stretch")]
    if len(real) >= 2 and all(isinstance(w, QAbstractButton) for w in real):
        h = FlowLayout()
        for w in real:
            h.addWidget(w)
        return h
    h = QHBoxLayout()
    for w in widgets:
        if isinstance(w, str) and w == "stretch":
            h.addStretch(1)
        else:
            h.addWidget(w)
    return h


def sub(lay, text):
    """Small bold sub-heading inside a section (replaces a framed QGroupBox)."""
    lab = QLabel("<b>%s</b>" % text)
    lab.setStyleSheet("color:#1f5f8b;margin-top:4px")
    lay.addWidget(lab)
    return lab


class Section(QWidget):
    """Collapsible section: a header button and a body. Sections of one page form an accordion
    (opening one closes the others) so a page is only as tall as the step you are working on."""

    def __init__(self, key, title, lesson_key=None, accordion=None):
        super().__init__()
        self.key, self.accordion = key, accordion
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.title = title
        self.head = QPushButton("▸  " + title.replace("&", "&&"))
        self.head.setObjectName("sec_" + key)
        self.head.setCheckable(True)
        self.head.setFlat(True)
        self.head.setSizePolicy(E(QSizePolicy, "Policy.Ignored"), E(QSizePolicy, "Policy.Fixed"))
        self.head.setToolTip(title)
        self.head.setStyleSheet("QPushButton{font-weight:bold;text-align:left;padding:5px 6px;border:none;"
                                "border-bottom:1px solid #d5e1ec;background:#f3f7fb;color:#1f3f5b}"
                                "QPushButton:checked{background:#e3eef8;color:#1f5f8b}")
        v.addWidget(self.head)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(6, 4, 4, 8)
        self.body.setVisible(False)
        v.addWidget(self.body)
        if lesson_key:
            lab = QLabel(lesson(lesson_key, language()))
            lab.setWordWrap(True)
            lab.setTextFormat(E(Qt, "TextFormat.RichText"))
            lab.setStyleSheet("background:#f4f8fb;padding:6px;border-radius:4px")
            lab.setVisible(False)
            lb = QToolButton()
            lb.setText("📖 " + tr("Lesson"))
            lb.setCheckable(True)
            lb.setAutoRaise(True)
            lb.setStyleSheet("QToolButton{color:#1f6fb2;font-size:11px}")
            lb.toggled.connect(lab.setVisible)
            self.lay.addWidget(lb)
            self.lay.addWidget(lab)
        self.head.toggled.connect(self._toggled)

    def _toggled(self, on):
        self.body.setVisible(on)
        self.head.setText(("▾  " if on else "▸  ") + self.title.replace("&", "&&"))
        if on and self.accordion is not None:
            for s in self.accordion:
                if s is not self and s.head.isChecked():
                    s.head.setChecked(False)

    def open(self):
        self.head.setChecked(True)


class Page:
    """One tab: a scroll area holding collapsible sections (or plain content)."""

    def __init__(self, tabs, title, tip=None):
        self.inner = QWidget()
        self.lay = QVBoxLayout(self.inner)
        self.lay.setContentsMargins(2, 2, 2, 2)
        self.lay.setSpacing(2)
        self.sections = []
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(SCROLL_OFF)
        self.scroll.setFrameShape(E(QScrollArea, "Shape.NoFrame"))
        self.scroll.setWidget(self.inner)
        i = tabs.addTab(self.scroll, title)
        if tip:
            tabs.setTabToolTip(i, tip)

    def section(self, key, title, lesson_key=None):
        s = Section(key, title, lesson_key, self.sections)
        self.sections.append(s)
        self.lay.addWidget(s)
        return s


def table(headers, min_h=96, editable=False, hidden=False):
    """hidden=True for result tables: they stay out of the way until fill() puts rows in."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setMinimumHeight(min(min_h, 120))
    t.setMaximumHeight(max(min_h, 120) + 140)
    if hidden:
        t.setVisible(False)
    t.setSelectionBehavior(SELECT_ROWS)
    if not editable:
        t.setEditTriggers(NO_EDIT)
    t.horizontalHeader().setSectionResizeMode(RESIZE_CONTENTS)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    return t


def fill(t, rows, fmt="%.3f"):
    t.setVisible(True)
    t.setRowCount(0)
    for r in rows:
        i = t.rowCount()
        t.insertRow(i)
        for j, v in enumerate(r):
            if isinstance(v, float):
                s = "—" if v != v else fmt % v
            else:
                s = str(v)
            t.setItem(i, j, QTableWidgetItem(s))


def non_file_providers():
    """Every raster provider except GDAL: web basemaps (XYZ/WMS like Google Satellite), WCS,
    ArcGIS services, PostGIS rasters, virtual rasters… — they have no file whose bands we can read."""
    try:
        from qgis.core import QgsProviderRegistry
        return [p for p in QgsProviderRegistry.instance().providerList() if p != "gdal"]
    except Exception:  # noqa: BLE001
        return ["wms", "wcs", "arcgismapserver", "postgresraster", "virtualraster", "mdal", "pdal", "vectortile"]


def raster_combo():
    c = QgsMapLayerComboBox()
    c.setFilters(layer_filter("raster"))
    c.setExcludedProviders(non_file_providers())   # only file-based (GDAL) rasters
    c.setAllowEmptyLayer(True)
    c.setLayer(None)                                # start empty instead of picking a random layer
    return c


def checklist(min_h=96):
    lw = QListWidget()
    lw.setMinimumHeight(min(min_h, 96))
    lw.setMaximumHeight(170)
    return lw


def checked_data(lw):
    out = []
    for i in range(lw.count()):
        it = lw.item(i)
        if it.checkState() == CHECKED:
            out.append(it.data(USER_ROLE))
    return out


def add_check(lw, text, data, on=True, tip=None):
    it = QListWidgetItem(text)
    it.setFlags(it.flags() | CHECKABLE)
    it.setCheckState(CHECKED if on else UNCHECKED)
    it.setData(USER_ROLE, data)
    if tip:
        it.setToolTip(tip)
    lw.addItem(it)
    return it


def layer_path(lyr):
    return rio.raster_path(lyr) if lyr is not None else None


def layer_info(lyr):
    """RasterInfo of a file-based raster layer, or None (web layer, missing file, unreadable)."""
    if lyr is None:
        return None
    try:
        if lyr.providerType() != "gdal":
            return None
        return rio.RasterInfo(layer_path(lyr))
    except Exception:  # noqa: BLE001
        return None


# ============================================================ panel
class Panel(QWidget):
    def __init__(self, plugin):
        super().__init__()
        self.plugin, self.iface = plugin, plugin.iface
        self.canvas = self.iface.mapCanvas()
        self.session = plugin.session
        self.aoi_geom = plugin.session.get("aoi_geom")
        self.admin = plugin.admin_backend
        self.rect_tool = None
        self.profile_tool = None
        self.proc = None
        self.build()

    # ------------------------------------------------------------ layout
    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(3, 3, 3, 3)
        root.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(3)
        title = QLabel("<b><span style='color:#1f6fb2'>LEE</span> RS Lab</b>")
        title.setToolTip("LEE · " + tr("Local Earth Engine") + " — " +
                         tr("Earth-Engine-style analysis that runs on your computer with free data — no login"))
        self.lang = QComboBox()
        self.lang.setObjectName("_lang")
        self.lang.addItem("English", "en")
        self.lang.addItem("ខ្មែរ (Khmer)", "km")
        self.lang.setCurrentIndex(1 if language() == "km" else 0)
        self.lang.currentIndexChanged.connect(self.on_lang)
        from . import basemaps as bmod
        from qgis.PyQt.QtGui import QIcon
        bmb = QToolButton()                       # basemaps live in the panel (no second toolbar icon)
        bmb.setIcon(QIcon(os.path.join(HERE, "icons", "basemap.svg")))
        bmb.setToolTip(tr("Add a basemap"))
        bmb.setAutoRaise(True)
        bmb.setPopupMode(E(QToolButton, "ToolButtonPopupMode.InstantPopup"))
        bmb.setMenu(bmod.build_menu(bmb, tr))
        helpb = QToolButton()
        helpb.setText(tr("Help"))
        helpb.clicked.connect(self.show_help)
        repb = QToolButton()
        repb.setText("PDF")
        repb.setToolTip(tr("Report") + " — " + tr("Export a PDF lab report of the current results"))
        repb.clicked.connect(self.export_report)
        top.addWidget(title)
        top.addStretch(1)
        for w in (self.lang, bmb, helpb, repb):
            top.addWidget(w)
        root.addLayout(top)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#555;font-size:11px")
        root.addWidget(self.status)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("_tabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)      # macOS default is False → a long tab bar widens the dock
        self.tabs.setElideMode(E(Qt, "TextElideMode.ElideRight"))
        root.addWidget(self.tabs, 1)
        self.sec = {}
        P = self.p_data = Page(self.tabs, tr("1 Data"), tr("Area of interest, imagery download, more datasets, setup"))
        self.build_download(P)
        self.build_setup(P)
        P = self.p_enh = Page(self.tabs, tr("2 Enhance"), tr("Band combination, indices, filters, PCA"))
        self.build_composite(P)
        self.build_indices(P)
        self.build_filters(P)
        P = self.p_cls = Page(self.tabs, tr("3 Classify"), tr("Unsupervised, supervised, accuracy, clean-up"))
        self.build_classes(P)
        self.build_unsup(P)
        self.build_sup(P)
        self.build_accuracy(P)
        self.build_vector(P)
        self.build_layout(P)
        P = self.p_chg = Page(self.tabs, tr("4 Change"))
        self.build_change(P)
        P = self.p_lee = Page(self.tabs, tr("5 LEE"), "LEE · " + tr("Local Earth Engine"))
        self.build_script(P)
        self.script_page = P.scroll
        for p in (self.p_data, self.p_enh, self.p_cls, self.p_chg, self.p_lee):
            for s in p.sections:
                self.sec[s.key] = s
            p.lay.addStretch(1)
        for k in ("aoi", "composite", "unsup", "change"):   # first step of each page starts open
            self.sec[k].open()
        self.compact()
        self.refresh_status()

    def compact(self):
        """Let every widget shrink so the dock can be narrow: combo boxes do not size to their longest
        item, form labels go above their field when there is no room, long labels wrap."""
        policy = E(QComboBox, "SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon")
        for cb in self.findChildren(QComboBox):
            if cb is self.lang:
                continue
            cb.setSizeAdjustPolicy(policy)
            cb.setMinimumContentsLength(8)
            cb.setSizePolicy(E(QSizePolicy, "Policy.Expanding"), cb.sizePolicy().verticalPolicy())
            if not cb.toolTip():
                cb.currentIndexChanged.connect(lambda _=0, c=cb: c.setToolTip(c.currentText()))
                cb.setToolTip(cb.currentText())
        wrap = E(QFormLayout, "RowWrapPolicy.WrapLongRows")
        grow = E(QFormLayout, "FieldGrowthPolicy.AllNonFixedFieldsGrow")
        for f in self.findChildren(QFormLayout):
            f.setRowWrapPolicy(wrap)
            f.setFieldGrowthPolicy(grow)
            f.setVerticalSpacing(4)
        for lab in self.findChildren(QLabel):
            if not lab.wordWrap() and len(lab.text()) > 34:
                lab.setWordWrap(True)

    def go(self, key):
        """Show the section `key` (switch tab, open it)."""
        s = self.sec.get(key)
        if s is None:
            return
        for p in (self.p_data, self.p_enh, self.p_cls, self.p_chg, self.p_lee):
            if s in p.sections:
                self.tabs.setCurrentWidget(p.scroll)
                break
        s.open()

    # ------------------------------------------------------------ background tasks that survive a panel rebuild
    def _task(self, title, fn, args=(), kwargs=None, on_done=None):
        """tasks.run() owned by this panel. If the panel is rebuilt (language switch, new project) while
        the task runs, this panel is only hidden (retired) — its callbacks still find their widgets; the
        results are shown in the new panel, and this one is deleted when its last task ends."""
        self._busy = getattr(self, "_busy", 0) + 1

        def done(ok, res, err):
            try:
                if on_done:
                    on_done(ok, res, err)
            except RuntimeError as e:                    # a widget was deleted meanwhile — keep going
                from qgis.core import QgsMessageLog
                QgsMessageLog.logMessage("Panel callback skipped: %s" % e, "LEE RS Lab")
            finally:
                self._busy -= 1
                if getattr(self, "_retired", False):
                    live = self.plugin.panel
                    if live is not None and live is not self:
                        try:
                            live.rerender()
                            live.msg(tr("%s — finished.") % title, "ok" if ok else "warn")
                        except RuntimeError:
                            pass
                    if self._busy <= 0:
                        self.plugin.release_panel(self)
        return tasks.run(title, fn, args, kwargs, done)

    def _bind_progress(self, task, bar_name):
        """Progress goes to the bar of the panel that is on screen now (not a deleted one)."""
        def upd(v):
            panel = self.plugin.panel or self
            bar = getattr(panel, bar_name, None)
            try:
                if bar is not None:
                    bar.setValue(int(v))
            except RuntimeError:
                pass
        task.progressChanged.connect(upd)

    def _live(self):
        """The panel currently in the dock: results of a task go there, even if this panel was replaced."""
        P = self.plugin.panel
        try:
            if P is not None and P is not self and not getattr(P, "_retired", False):
                P.objectName()
                return P
        except RuntimeError:
            pass
        return self

    def busy(self):
        return getattr(self, "_busy", 0) > 0

    def out_dir(self):
        d = self.outdir.filePath() or default_out_dir()
        os.makedirs(d, exist_ok=True)
        return d

    def msg(self, text, level="info"):
        self.status.setText(text)
        from qgis.core import Qgis
        lv = {"info": Qgis.Info, "ok": Qgis.Success, "warn": Qgis.Warning, "err": Qgis.Critical}[level]
        if level != "info":
            self.iface.messageBar().pushMessage("LEE RS Lab", text, lv, 8)

    def name(self, key, suffix=""):
        return tr(key) + suffix

    def tag(self, lyr, key, suffix=""):
        if lyr is not None:
            lyr.setCustomProperty("rslab_key", key)
            lyr.setCustomProperty("rslab_suffix", suffix)
        return lyr

    # ============================================================ 0 SETUP
    def build_setup(self, P):
        lay = P.section("setup", "⚙ " + tr("Setup"), "setup").lay
        self.ee_status = QLabel()
        self.ee_status.setWordWrap(True)
        lay.addWidget(self.ee_status)
        sub(lay, tr("Output folder"))
        self.outdir = QgsFileWidget()
        self.outdir.setObjectName("outdir")
        self.outdir.setStorageMode(E(QgsFileWidget, "StorageMode.GetDirectory"))
        self.outdir.setFilePath(default_out_dir())
        self.outdir.fileChanged.connect(lambda p: QgsSettings().setValue("rs_lab/out_dir", p))
        lay.addWidget(self.outdir)
        sub(lay, tr("Data sources (free, no login)"))
        lay.addWidget(QLabel("<small>%s</small>" % tr(
            "Imagery: Microsoft Planetary Computer and Earth Search (AWS). Boundaries: HDX, "
            "Open Development Cambodia, geoBoundaries. Basemaps: Esri, OSM, EOX, CARTO, Google.")))
        self.src_table = table([tr("Source"), tr("Status")], 110, hidden=True)
        lay.addWidget(self.src_table)
        self.cache_lab = QLabel()
        lay.addLayout(row(btn(tr("Test connections"), self.test_sources, primary=True),
                          btn(tr("Clear boundary cache"), self.clear_cache)))
        lay.addWidget(self.cache_lab)
        sub(lay, tr("Optional Python package"))
        self.btn_sk = btn(tr("Install scikit-learn (Random Forest, SVM)"), self.install_sklearn)
        self.btn_sk.setToolTip("%s %s" % (tr("Packages go to:"), deps.LIB_DIR))
        lay.addLayout(row(self.btn_sk, "stretch"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(70)
        self.log.setMaximumHeight(160)
        self.log.setPlaceholderText(tr("Log"))
        lay.addWidget(self.log)

    SOURCE_TESTS = [
        ("Microsoft Planetary Computer (STAC)", "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a"),
        ("Earth Search — AWS (STAC)", "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a"),
        ("HDX (boundaries)", "https://data.humdata.org/api/3/action/package_show?id=cod-ab-khm"),
        ("geoBoundaries (boundaries)", "https://www.geoboundaries.org/api/current/gbOpen/KHM/ADM0/"),
        ("Open Development Cambodia", "https://data.opendevelopmentcambodia.net/api/3/action/site_read"),
    ]

    def test_sources(self):
        rows = [[n, tr("testing…")] for n, _ in self.SOURCE_TESTS]
        fill(self.src_table, rows)
        tests = list(self.SOURCE_TESTS)

        def job(task):
            import time
            import urllib.request
            out = []
            for name, url in tests:
                t0 = time.time()
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "QGIS-RS-Lab/2.0"})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        r.read(2048)
                    out.append([name, "OK (%.1f s)" % (time.time() - t0)])
                except Exception as e:  # noqa: BLE001
                    out.append([name, "%s: %s" % (tr("failed"), str(e)[:80])])
            return out

        def done(ok, res, err):
            if res:
                fill(self.src_table, res)
        self._task(tr("Testing data sources"), job, on_done=done)

    def clear_cache(self):
        import shutil
        shutil.rmtree(aoimod.CACHE, ignore_errors=True)
        self.admin = self.plugin.admin_backend = None
        for cb in (self.cb_prov, self.cb_dist, self.cb_comm):
            cb.clear()
        self.refresh_status()
        self.msg(tr("Boundary cache cleared."), "ok")

    def refresh_status(self):
        def size(d):
            tot = 0
            for root, _, files in os.walk(d):
                for f in files:
                    try:
                        tot += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return tot / 1e6
        sk = deps.has_module("sklearn")
        self.ee_status.setText("%s · scikit-learn: <b>%s</b>" % (
            tr("No account or login needed"), tr("yes") if sk else tr("no")))
        self.btn_sk.setText(("✓ " + tr("scikit-learn is installed (Random Forest, SVM, kNN ready)")) if sk else
                            tr("Install scikit-learn (Random Forest, SVM)"))
        self.btn_sk.setEnabled(not sk)
        self.cache_lab.setText("<small>%s: %.0f MB</small>" % (tr("Boundary cache"), size(aoimod.CACHE)))
        self.status.setText("● " + tr("Ready — data from free open catalogues"))

    def _pip(self, pkgs):
        self.log.appendPlainText("pip install %s → %s" % (" ".join(pkgs), deps.LIB_DIR))
        os.makedirs(deps.LIB_DIR, exist_ok=True)

        def job(task):
            code, out = deps.run_pip(pkgs, True)
            return code, "%s\n%s" % (" ".join(deps.pip_command(pkgs, True)), out)

        def done(ok, res, err):
            if res:
                code, out = res
                self.log.appendPlainText(out[-4000:])
                ok = code == 0
            deps.add_lib_path()
            if ok:
                self.msg(tr("Installation finished. Restart QGIS if the module is not detected."), "ok")
            else:
                self.msg(tr("Installation failed — see the log.") + " " + (err or ""), "err")
            self.refresh_status()
        self._task(tr("Installing %s") % ", ".join(pkgs), job, on_done=done)
        self.msg(tr("Installing… this can take a few minutes."))

    def install_sklearn(self):
        self._pip(["scikit-learn"])

    # ============================================================ 1 DOWNLOAD
    def build_download(self, P):
        gl = P.section("aoi", tr("Area of interest (AOI)"), "download").lay
        self.aoi_src = QComboBox()
        self.aoi_src.setObjectName("aoi_src")
        for k, t in (("admin", tr("Administrative unit (country / province / district / commune)")),
                     ("canvas", tr("Current map extent")), ("draw", tr("Draw a rectangle on the map")),
                     ("layer", tr("Polygon layer (selected features, or all)"))):
            self.aoi_src.addItem(t, k)
        gl.addWidget(self.aoi_src)
        self.aoi_layer = QgsMapLayerComboBox()
        self.aoi_layer.setFilters(layer_filter("polygon"))
        gl.addWidget(self.aoi_layer)
        self.admin_box = QWidget()
        al = QFormLayout(self.admin_box)
        al.setContentsMargins(0, 0, 0, 0)
        self.cb_country = QComboBox()
        self.cb_country.setObjectName("cb_country")
        self.cb_country.setMinimumContentsLength(18)
        km_ui = language() == "km"
        for c in aoimod.countries():
            self.cb_country.addItem("%s (%s)" % (c["km"], c["name"]) if km_ui and c.get("km") else c["name"],
                                    [c["iso3"], c["name"]])
        want = QgsSettings().value("rs_lab/country", "KHM", type=str) or "KHM"
        for i in range(self.cb_country.count()):
            if self.cb_country.itemData(i)[0] == want:
                self.cb_country.setCurrentIndex(i)
                break
        al.addRow(tr("Country"), self.cb_country)
        self.cb_prov, self.cb_dist, self.cb_comm = QComboBox(), QComboBox(), QComboBox()
        for c, n in ((self.cb_prov, "cb_prov"), (self.cb_dist, "cb_dist"), (self.cb_comm, "cb_comm")):
            c.setObjectName(n)
            c.setMinimumContentsLength(18)
        al.addRow(tr("Province"), self.cb_prov)
        al.addRow(tr("District"), self.cb_dist)
        al.addRow(tr("Commune"), self.cb_comm)
        self.admin_srcsel = QComboBox()
        self.admin_srcsel.setObjectName("admin_srcsel")
        self.admin_srcsel.addItem(tr("Auto (best source for the country)"), "auto")
        for src in aoimod.sources():
            self.admin_srcsel.addItem(src["name"], src.get("id"))
        al.addRow(tr("Source"), self.admin_srcsel)
        self.btn_admin = btn(tr("Load boundaries"), lambda: self.load_admin())
        al.addRow(row(self.btn_admin, btn(tr("From file…"), self.pick_admin_file)))
        self.admin_lab = QLabel()
        self.admin_lab.setWordWrap(True)
        self.admin_lab.setStyleSheet("color:#567")
        al.addRow(self.admin_lab)
        gl.addWidget(self.admin_box)
        self.cb_country.currentIndexChanged.connect(self.on_country)
        self.cb_prov.currentIndexChanged.connect(self.on_prov)
        self.cb_dist.currentIndexChanged.connect(self.on_dist)
        gl.addLayout(row(btn(tr("Use this AOI"), self.set_aoi, primary=True), btn(tr("Zoom"), self.zoom_aoi),
                         btn(tr("Add AOI to map"), self.add_aoi_layer)))
        self.aoi_lab = QLabel(tr("No AOI yet."))
        self.aoi_lab.setWordWrap(True)
        gl.addWidget(self.aoi_lab)
        self.auto_crs = QCheckBox(tr("Set the project CRS to the AOI's UTM zone"))
        self.auto_crs.setObjectName("auto_crs")
        self.auto_crs.setChecked(QgsSettings().value("rs_lab/auto_crs", True, type=bool))
        self.auto_crs.toggled.connect(lambda v: QgsSettings().setValue("rs_lab/auto_crs", v))
        gl.addWidget(self.auto_crs)
        self.aoi_src.currentIndexChanged.connect(self.on_aoi_src)
        self.on_aoi_src()

        gl = P.section("imagery", tr("Imagery — search scenes")).lay
        sf = QFormLayout()
        self.src = QComboBox()
        self.src.setObjectName("src")
        self.src.addItem(tr("Microsoft Planetary Computer (S2, Landsat, S1; free, no login)"), "planetary_computer")
        self.src.addItem(tr("Earth Search — AWS (Sentinel-2, free, no login)"), "earth_search")
        self.src.addItem(tr("Local files — band set from EarthExplorer / Copernicus Browser"), "local")
        sf.addRow(tr("Source"), self.src)
        gl.addLayout(sf)
        self.src_note = QLabel()
        self.src_note.setWordWrap(True)
        self.src_note.setStyleSheet("color:#356;font-size:11px")
        gl.addWidget(self.src_note)
        # ---- remote (GEE / STAC) settings
        self.remote_box = QWidget()
        f = QFormLayout(self.remote_box)
        f.setContentsMargins(0, 0, 0, 0)
        self.sensor = QComboBox()
        self.sensor.setObjectName("sensor")
        f.addRow(tr("Sensor"), self.sensor)
        today = QDate.currentDate()
        y = today.year() if today.month() >= 4 else today.year() - 1
        self.d0, self.d1 = QDateEdit(QDate(y - 1, 11, 1)), QDateEdit(QDate(y, 3, 31))
        for d, n in ((self.d0, "d0"), (self.d1, "d1")):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
            d.setObjectName(n)
        f.addRow(tr("Start date"), self.d0)
        f.addRow(tr("End date"), self.d1)
        self.cloud = QSpinBox()
        self.cloud.setObjectName("cloud")
        self.cloud.setRange(0, 100)
        self.cloud.setValue(30)
        self.cloud.setSuffix(" %")
        f.addRow(tr("Max scene cloud cover"), self.cloud)
        self.s1pass = QComboBox()
        self.s1pass.setObjectName("s1pass")
        for k in ("BOTH", "ASCENDING", "DESCENDING"):
            self.s1pass.addItem(k, k)
        f.addRow(tr("Sentinel-1 orbit pass"), self.s1pass)
        self.max_scenes = QSpinBox()
        self.max_scenes.setObjectName("max_scenes")
        self.max_scenes.setRange(1, 60)
        self.max_scenes.setValue(10)
        self.max_scenes_lab = QLabel(tr("Max scenes in composite (least cloudy first)"))
        f.addRow(self.max_scenes_lab, self.max_scenes)
        self.par = QSpinBox()
        self.par.setObjectName("par")
        self.par.setRange(1, 32)
        self.par.setValue(8)
        self.par.setToolTip(tr("More connections = faster on a good line; use 2–4 on a slow or shared connection."))
        self.par_lab = QLabel(tr("Parallel connections"))
        f.addRow(self.par_lab, self.par)
        f.addRow(row(btn(tr("Search scenes"), self.search, primary=True), "stretch"))
        self.scenes = table([tr("Date"), tr("Cloud %"), tr("Tile / path-row"), tr("Scene ID")], 120, hidden=True)
        self.scenes.setSelectionMode(E(QAbstractItemView, "SelectionMode.ExtendedSelection"))
        f.addRow(self.scenes)
        gl.addWidget(self.remote_box)
        # ---- local band files
        self.local_box = QWidget()
        ll = QVBoxLayout(self.local_box)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel(tr("Add the band files (GeoTIFF / JP2). Bands are recognised from the file names "
                               "(…_SR_B4.TIF, …_B04_10m.jp2) and put in band order.")))
        ll.addLayout(row(btn(tr("Add files…"), self.local_add_files), btn(tr("Add folder…"), self.local_add_folder),
                         btn(tr("Clear"), self.local_clear)))
        self.local_table = table([tr("Use"), tr("Band"), tr("Role"), tr("Pixel size"), tr("File")], 120, hidden=True)
        ll.addWidget(self.local_table)
        gl.addWidget(self.local_box)

        lay = P.section("product", tr("Composite & download")).lay
        f = QFormLayout()
        lay.addLayout(f)
        self.prod_box = QWidget()
        pf = QFormLayout(self.prod_box)
        pf.setContentsMargins(0, 0, 0, 0)
        self.method = QComboBox()
        self.method.setObjectName("method")
        for k, t in catalog.COMPOSITE_METHODS:
            self.method.addItem(tr(t), k)
        pf.addRow(tr("Composite"), self.method)
        self.product = QComboBox()
        self.product.setObjectName("product")
        self.product.addItem(tr("Raw DN (integers as stored, smaller files)"), "raw")
        self.product.addItem(tr("Scaled reflectance 0–1 (float)"), "scaled")
        pf.addRow(tr("Values"), self.product)
        self.mask = QCheckBox(tr("Mask clouds and shadows (SCL / QA_PIXEL)"))
        self.mask.setObjectName("mask")
        self.mask.setChecked(True)
        pf.addRow(self.mask)
        self.bands = checklist(110)
        pf.addRow(tr("Bands"), self.bands)
        pf.addRow(row(btn(tr("All"), lambda: self.set_all(self.bands, True)),
                      btn(tr("None"), lambda: self.set_all(self.bands, False)),
                      btn(tr("Visible + NIR + SWIR"), self.vnir_swir)))
        f.addRow(self.prod_box)
        self.scale = QSpinBox()
        self.scale.setObjectName("scale")
        self.scale.setRange(0, 1000)
        self.scale.setSuffix(" m")
        self.scale.setSpecialValueText(tr("finest band"))
        f.addRow(tr("Pixel size"), self.scale)
        self.crs = QComboBox()
        self.crs.setObjectName("crs")
        self.crs.addItem(tr("Auto UTM zone"), 0)
        self.crs.addItem("EPSG:32648 — WGS 84 / UTM 48N", 32648)
        self.crs.addItem("EPSG:32647 — WGS 84 / UTM 47N", 32647)
        f.addRow(tr("CRS"), self.crs)
        self.clip = QCheckBox(tr("Clip to AOI shape"))
        self.clip.setObjectName("clip")
        self.clip.setChecked(True)
        f.addRow(self.clip)
        self.fname = QLineEdit()
        self.fname.setObjectName("fname")
        self.fname.setPlaceholderText(tr("auto"))
        f.addRow(tr("File name"), self.fname)
        self.dl_prog = QProgressBar()
        self.dl_prog.setValue(0)
        self.btn_preview = btn(tr("Preview on map"), self.preview)
        self.btn_download = btn(tr("Download"), self.download, primary=True)
        lay.addLayout(row(self.btn_preview, self.btn_download))
        lay.addWidget(self.dl_prog)
        lay = P.section("datasets", tr("More datasets (DEM, land cover, water…)")).lay
        lay.addWidget(QLabel("<small>%s</small>" % tr("More datasets for the AOI (free, no login)")))
        mf = QFormLayout()
        lay.addLayout(mf)
        self.ds = QComboBox()
        self.ds.setObjectName("ds")
        for k, t in self.DATASET_CHOICES:
            self.ds.addItem(tr(t), k)
        mf.addRow(tr("Dataset"), self.ds)
        self.ds_year = QSpinBox()
        self.ds_year.setObjectName("ds_year")
        self.ds_year.setRange(2000, 2030)
        self.ds_year.setValue(2023)
        mf.addRow(tr("Year (land cover)"), self.ds_year)
        mf.addRow(row(btn(tr("Add to map"), self.add_dataset, primary=True),
                      btn(tr("Open as script"), lambda: self.add_dataset(as_script=True))))
        self.local_items = []
        self.stac_scenes = None
        self.sensor.currentIndexChanged.connect(self.on_sensor)
        self.src.setCurrentIndex(self.src.findData("planetary_computer"))   # default: free, no login, all sensors
        self.src.currentIndexChanged.connect(self.on_source)
        self.on_source()

    def set_all(self, lw, on):
        for i in range(lw.count()):
            lw.item(i).setCheckState(CHECKED if on else UNCHECKED)

    def vnir_swir(self):
        want = {"blue", "green", "red", "nir", "swir1", "swir2", "rededge1", "rededge2", "rededge3", "nir2",
                "vv", "vh"}
        for i in range(self.bands.count()):
            it = self.bands.item(i)
            it.setCheckState(CHECKED if it.data(USER_ROLE)[1] in want else UNCHECKED)

    def on_sensor(self):
        k = self.sensor.currentData()
        if k is None:
            return
        s = catalog.SENSORS[k]
        self.bands.clear()
        avail = None
        if self.source() != "local":
            from . import stac_engine as se
            avail = set(se.STAC[self.source()][k]["assets"])
        for b, role, wl, res in s["bands"]:
            if avail is not None and b not in avail:
                continue
            add_check(self.bands, "%s — %s (%s, %d m)" % (b, role, wl, res), (b, role, wl),
                      on=role in ("blue", "green", "red", "nir", "swir1", "swir2", "vv", "vh"))
        self.scale.setValue(s["res"])
        self.s1pass.setEnabled(k == "S1_GRD")
        self.cloud.setEnabled(k != "S1_GRD")
        self.mask.setEnabled(k != "S1_GRD")
        self.product.setEnabled(not s.get("float_native", False))
        self.scenes.setRowCount(0)
        self.stac_scenes = None

    def on_aoi_src(self):
        k = self.aoi_src.currentData()
        self.aoi_layer.setVisible(k == "layer")
        self.admin_box.setVisible(k == "admin")
        if k == "draw":
            if self.rect_tool is None:
                self.rect_tool = aoimod.RectangleTool(self.canvas)
                self.rect_tool.drawn.connect(self.on_drawn)
            self.canvas.setMapTool(self.rect_tool)
            self.msg(tr("Drag a rectangle on the map."))
        if k == "admin":
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(200, self.auto_load_admin)

    def on_drawn(self, rect):
        g = QgsGeometry.fromRect(rect)
        self._set_aoi_geom(g, self.canvas.mapSettings().destinationCrs(), tr("Drawn rectangle"))

    def _set_aoi_geom(self, g, crs, label):
        g = QgsGeometry(g)
        if crs.authid() != "EPSG:4326":
            g.transform(QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()))
        self.aoi_geom = g
        self.session["aoi_geom"] = g
        self.session["aoi_label"] = label
        from qgis.core import QgsDistanceArea
        da = QgsDistanceArea()
        da.setSourceCrs(QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance().transformContext())
        da.setEllipsoid("WGS84")
        km2 = da.measureArea(g) / 1e6
        self.session["aoi_km2"] = km2
        self.aoi_lab.setText("<b>%s</b> — %.1f km²" % (label, km2))
        self.apply_aoi_crs()
        self.plugin.show_aoi(g)

    def apply_aoi_crs(self):
        """Download CRS 'Auto' shows the AOI's UTM zone; optionally switch the project CRS to it."""
        from . import basemaps as bmod
        if self.aoi_geom is None:
            return
        epsg, lab = bmod.utm_for(self.aoi_geom)
        self.crs.setItemText(0, "%s — EPSG:%d (%s)" % (tr("Auto UTM zone"), epsg, lab))
        if not self.auto_crs.isChecked():
            return
        crs, txt = bmod.display_crs_for(self.aoi_geom)
        proj = QgsProject.instance()
        if proj.crs().authid() != crs.authid():
            proj.setCrs(crs)
            self.canvas.setDestinationCrs(crs)
            self.msg(tr("Project CRS set to %s") % txt)

    def set_aoi(self):
        k = self.aoi_src.currentData()
        if k == "canvas":
            self._set_aoi_geom(QgsGeometry.fromRect(self.canvas.extent()), self.canvas.mapSettings().destinationCrs(),
                               tr("Map extent"))
        elif k == "draw":
            self.on_aoi_src()
        elif k == "layer":
            lyr = self.aoi_layer.currentLayer()
            if lyr is None:
                self.msg(tr("Choose a polygon layer."), "warn")
                return
            feats = lyr.selectedFeatures() or list(lyr.getFeatures())
            if not feats:
                self.msg(tr("The layer has no features."), "warn")
                return
            g = QgsGeometry.unaryUnion([f.geometry() for f in feats])
            self._set_aoi_geom(g, lyr.crs(), "%s (%d)" % (lyr.name(), len(feats)))
        else:
            self.admin_aoi()

    def zoom_aoi(self):
        if self.aoi_geom is None:
            return
        g = QgsGeometry(self.aoi_geom)
        g.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                           self.canvas.mapSettings().destinationCrs(), QgsProject.instance()))
        self.canvas.setExtent(g.boundingBox().buffered(g.boundingBox().width() * 0.05))
        self.canvas.refresh()

    # --- admin boundaries
    def load_admin(self, file_path=None):
        self.btn_admin.setEnabled(False)
        only = self.admin_srcsel.currentData()
        iso3, cname = self.country()
        t = aoimod.AdminLoadTask(None if only == "auto" else only, file_path, iso3, cname)
        self._admin_loading = True

        def done(ok):
            self._admin_loading = False
            try:
                self.btn_admin.setEnabled(True)
            except RuntimeError:      # panel was rebuilt meanwhile (language switch)
                return
            if t.backend is not None:
                self.admin = self.plugin.admin_backend = t.backend
                self.session.setdefault("sources", [])
                if t.backend.attribution not in self.session["sources"]:
                    self.session["sources"].append(t.backend.attribution)
                self.admin_lab.setText("<small>%s<br>%s</small>" % (t.backend.name, t.backend.describe()))
                self.msg(tr("Boundaries loaded from %s") % t.backend.name, "ok")
                self.fill_prov()
            else:
                self.msg(tr("Could not load boundaries: %s") % "; ".join(t.errors)[:600], "err")
        t.taskCompleted.connect(lambda: done(True))
        t.taskTerminated.connect(lambda: done(False))
        tasks._live.append(t)
        from qgis.core import QgsApplication
        QgsApplication.taskManager().addTask(t)
        self.msg(tr("Loading boundaries…"))

    def country(self):
        d = self.cb_country.currentData() or ["KHM", "Cambodia"]
        return d[0], d[1]

    def on_country(self):
        iso3, _ = self.country()
        QgsSettings().setValue("rs_lab/country", iso3)
        for cb in (self.cb_prov, self.cb_dist, self.cb_comm):
            cb.clear()
        if self.admin is not None and getattr(self.admin, "iso3", None) != iso3:
            self.admin = self.plugin.admin_backend = None
            self.admin_lab.setText("")
        self.auto_load_admin()

    def auto_load_admin(self):
        """Boundaries of the chosen country load by themselves (cached after the first time)."""
        if self.aoi_src.currentData() != "admin" or getattr(self, "_admin_loading", False):
            return
        if self.admin is not None and getattr(self.admin, "iso3", "KHM") == self.country()[0]:
            if self.cb_prov.count() == 0:
                self.fill_prov()
            return
        self.load_admin()

    def pick_admin_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, tr("Boundary file (shapefile, GeoPackage, GeoJSON or zip)"), "",
                                            "Vector (*.shp *.gpkg *.geojson *.json *.zip)")
        if fn:
            self.load_admin(fn)

    def _unit_text(self, en, km):
        if language() == "km" and km:
            return "%s (%s)" % (km, en) if en and en != km else km
        return "%s  %s" % (en, km) if km else en

    def _fill(self, cb, items, whole=None):
        cb.blockSignals(True)
        cb.clear()
        if whole:
            cb.addItem(whole, aoimod.WHOLE)
        for code, en, km in items:
            cb.addItem(self._unit_text(en, km), [code, en, km])
        cb.blockSignals(False)

    @staticmethod
    def _code(cb):
        d = cb.currentData()
        return None if d is None or d == aoimod.WHOLE else d[0]

    def fill_prov(self):
        if self.admin is None:
            return
        try:
            self._fill(self.cb_prov, self.admin.units(1), tr("— whole country —"))
        except Exception as e:  # noqa: BLE001
            self.msg(str(e), "err")
            return
        self.on_prov()

    def on_prov(self):
        p = self._code(self.cb_prov)
        if self.admin is None or p is None:
            return
        try:
            items = self.admin.units(2, p) if self.admin.has_level(2) else []
        except Exception:  # noqa: BLE001
            items = []
        self._fill(self.cb_dist, items, tr("— whole province —"))
        self.on_dist()

    def on_dist(self):
        if self.admin is None:
            return
        d = self._code(self.cb_dist)
        items = []
        if d is not None and self.admin.has_level(3):
            try:
                items = self.admin.units(3, d)
            except Exception:  # noqa: BLE001
                items = []
        self._fill(self.cb_comm, items, tr("— whole district —"))

    def admin_aoi(self):
        if self.admin is None:
            self.msg(tr("Load the boundaries first."), "warn")
            return
        chain = [cb.currentData() for cb in (self.cb_prov, self.cb_dist, self.cb_comm)]
        chain = [d for d in chain if d is not None and d != aoimod.WHOLE]
        level = len(chain)
        code = chain[-1][0] if chain else None
        try:
            g = self.admin.geometry(level, code)
        except Exception as e:  # noqa: BLE001
            self.msg(str(e), "err")
            return
        if g is None or g.isEmpty():
            self.msg(tr("Boundary not found."), "warn")
            return
        iso3, cname = self.country()
        ckm = next((c.get("km", "") for c in aoimod.countries() if c["iso3"] == iso3), "")
        en = ", ".join([d[1] for d in reversed(chain)] + ([] if chain else [cname]))
        km = ", ".join([d[2] for d in reversed(chain) if d[2]] + ([] if chain or not ckm else [ckm]))
        self.session["aoi_admin"] = dict(level=level, en=en, km=km, codes=[d[0] for d in chain])
        lab = km if (language() == "km" and km) else en
        self._set_aoi_geom(g, QgsCoordinateReferenceSystem("EPSG:4326"), lab)
        self.zoom_aoi()

    def add_aoi_layer(self):
        if self.aoi_geom is None:
            self.msg(tr("Set an AOI first."), "warn")
            return
        from qgis.core import QgsFeature
        vl = QgsVectorLayer("Polygon?crs=EPSG:4326&field=name:string(254)&field=name_km:string(254)",
                            self.name("AOI"), "memory")
        f = QgsFeature(vl.fields())
        f.setGeometry(self.aoi_geom)
        adm = self.session.get("aoi_admin") or {}
        f.setAttributes([adm.get("en") or self.session.get("aoi_label", ""), adm.get("km", "")])
        vl.dataProvider().addFeatures([f])
        vl.updateExtents()
        sym = QgsSymbol.defaultSymbol(vl.geometryType())
        sym.setColor(QColor(0, 0, 0, 0))
        try:
            sym.symbolLayer(0).setStrokeColor(QColor("#ff5a00"))
            sym.symbolLayer(0).setStrokeWidth(0.8)
        except Exception:  # noqa: BLE001
            pass
        from qgis.core import QgsSingleSymbolRenderer
        vl.setRenderer(QgsSingleSymbolRenderer(sym))
        QgsProject.instance().addMapLayer(vl)
        self.tag(vl, "AOI")

    # --- source switching
    SOURCE_NOTES = {
        "earth_search": "Free, no login. Sentinel-2 L2A Cloud-Optimised GeoTIFFs on AWS. Only the AOI pixels are "
                        "read; clouds masked with the SCL band; composite computed on your computer.",
        "planetary_computer": "Free, no login. Sentinel-2 L2A, Landsat 5/7/8/9 Level-2 and Sentinel-1 RTC "
                              "(terrain-corrected, dB). Clouds masked with SCL / QA_PIXEL; composite on your computer.",
        "local": "No internet needed. Use band files you downloaded yourself (USGS EarthExplorer, Copernicus "
                 "Browser, a SAFE folder…). They are stacked into one image with band roles.",
    }

    def source(self):
        return self.src.currentData()

    def on_source(self):
        src = self.source()
        self.src_note.setText(tr(self.SOURCE_NOTES[src]))
        local = src == "local"
        self.remote_box.setVisible(not local)
        self.prod_box.setVisible(not local)
        self.local_box.setVisible(local)
        self.btn_preview.setText(tr("Quick look (coarse)"))
        self.btn_preview.setEnabled(not local)
        self.max_scenes.setVisible(src != "local")
        self.max_scenes_lab.setVisible(src != "local")
        self.par.setVisible(src != "local")
        self.par_lab.setVisible(src != "local")
        if local:
            self.scale.setValue(0)
            return
        from . import stac_engine as se
        keys = se.sensors_for(src)
        cur = self.sensor.currentData()
        self.sensor.blockSignals(True)
        self.sensor.clear()
        for k in keys:
            lab = catalog.SENSORS[k]["label"]
            if src == "planetary_computer" and k == "S1_GRD":
                lab = "Sentinel-1 SAR RTC — VV+VH, dB (terrain corrected)"
            self.sensor.addItem(lab, k)
        if cur in keys:
            self.sensor.setCurrentIndex(keys.index(cur))
        self.sensor.blockSignals(False)
        self.stac_scenes = None
        self.on_sensor()

    def _check_ready(self):
        if self.aoi_geom is None and self.source() != "local":
            self.msg(tr("Set an AOI first."), "warn")
            return False
        return True

    def _show_scenes(self, res):
        fill(self.scenes, [[r["date"], ("%.1f" % r["cloud"]) if r["cloud"] is not None else "—", r["tile"], r["id"]]
                           for r in res])
        self.msg(tr("%d scenes found.") % len(res), "ok" if res else "warn")

    def search(self):
        if not self._check_ready():
            return
        from . import stac_engine as se
        p, src, geom = self._params(), self.source(), QgsGeometry(self.aoi_geom)

        def job(task):
            return se.search(src, p["sensor"], geom, p["start"], p["end"], p["max_cloud"], p["s1_pass"], task=task)

        def done(ok, res, err):
            if not ok:
                self.msg(tr("Search failed: %s") % err, "err")
                return
            self.stac_scenes = dict(key=(src, p["sensor"], p["start"], p["end"], p["max_cloud"], p["s1_pass"]),
                                    scenes=res)
            self._show_scenes(res)
        self._task(tr("Searching scenes"), job, on_done=done)
        self.msg(tr("Searching…"))

    def _params(self):
        """Search/download parameters from the Imagery section (restored — were missing in 2.0.2)."""
        return dict(sensor=self.sensor.currentData(), start=self.d0.date().toString("yyyy-MM-dd"),
                    end=self.d1.date().toString("yyyy-MM-dd"), max_cloud=self.cloud.value(),
                    s1_pass=self.s1pass.currentData() or "BOTH")

    def _selected_ids(self):
        """Scene ids of the rows selected in the scenes table (column 'Scene ID')."""
        rows = sorted({i.row() for i in self.scenes.selectedIndexes()})
        col = self.scenes.columnCount() - 1
        return [self.scenes.item(r, col).text() for r in rows if self.scenes.item(r, col)]

    def preview(self):
        if not self._check_ready():
            return
        self._download_stac(quick=True)

    def download(self):
        if not self._check_ready():
            return
        src = self.source()
        if src == "local":
            return self._download_local()
        self._download_stac()

    def _out_path(self, default):
        fn = self.fname.text().strip() or default
        return os.path.join(self.out_dir(), fn if fn.lower().endswith(".tif") else fn + ".tif")

    def _after_download(self, out, p, bands, product, epsg, source_text, name=None, tag=None):
        self.dl_prog.setValue(100)
        info = rio.RasterInfo(out)
        rgb = [info.roles.get(r, 1) for r in ("red", "green", "blue")] if "red" in info.roles else [1, 1, 1]
        if "vv" in info.roles and "red" not in info.roles:
            rgb = [info.roles.get("vv", 1), info.roles.get("vh", 1), info.roles.get("vv", 1)]
        if info.count < 3 and "red" not in info.roles:
            rgb = [1, min(2, info.count), 1]
        lyr = rio.add_rgb_layer(out, name or os.path.basename(out)[:-4], rgb, "clip", tr(GROUP))
        if tag:
            self.tag(lyr, *tag)
        if p is not None:
            s = catalog.SENSORS.get(p["sensor"], {})
            self.session["download"] = {"Sensor": s.get("label", p["sensor"]), "Collection": source_text,
                                        "Dates": "%s → %s" % (p["start"], self.d1.date().toString("yyyy-MM-dd")),
                                        "Composite": self.method.currentText(), "Values": product,
                                        "Bands": ", ".join(bands), "Pixel size": "%g m" % abs(info.gt[1]),
                                        "CRS": "EPSG:%d" % epsg if epsg else info.wkt[:40],
                                        "AOI": self.session.get("aoi_label", ""), "File": out}
        else:
            self.session["download"] = {"Collection": source_text, "Bands": ", ".join(info.descs),
                                        "Pixel size": "%g m" % abs(info.gt[1]), "File": out}
        self.session.setdefault("sources", [])
        if source_text not in self.session["sources"]:
            self.session["sources"].append(source_text)
        if lyr and not tag:
            for cb in (self.c_raster, self.i_raster, self.f_raster, self.u_raster, self.s_raster):
                cb.setLayer(lyr)
        self.msg(tr("Saved %s") % out, "ok")

    # --- STAC (Earth Search / Planetary Computer)
    def _download_stac(self, quick=False):
        from . import basemaps as bmod
        from . import stac_engine as se
        src, p = self.source(), self._params()
        s = catalog.SENSORS[p["sensor"]]
        chosen = checked_data(self.bands)
        if quick:
            roles = {r: (b, r, w) for b, r, w, _ in s["bands"]}
            chosen = [roles[r] for r in (("vv", "vh") if p["sensor"] == "S1_GRD" else ("red", "green", "blue"))]
        if not chosen:
            self.msg(tr("Choose at least one band."), "warn")
            return
        bands = [c[0] for c in chosen]
        method = "leastcloudy" if quick and self.method.currentData() not in ("selected",) else self.method.currentData()
        ids = self._selected_ids()
        if method == "selected" and not ids:
            self.msg(tr("Select one or more scenes in the table first."), "warn")
            return
        scale = (self.scale.value() or s["res"]) * (8 if quick else 1)
        epsg = self.crs.currentData() or bmod.utm_for(self.aoi_geom)[0]
        product = "scaled" if (self.product.currentData() == "scaled" or p["sensor"] == "S1_GRD") else "raw"
        nsc = 1 if method in ("leastcloudy", "selected") else self.max_scenes.value()
        mb_tmp, mb_out = se.estimate_mb(self.aoi_geom, epsg, scale, len(bands), nsc)
        if not quick and mb_tmp > 1500:
            r = QMessageBox.question(self, tr("Large download"),
                                     tr("About %.0f MB will be downloaded. Continue?") % mb_tmp, YES | NO)
            if r != YES:
                return
        key = (src, p["sensor"], p["start"], p["end"], p["max_cloud"], p["s1_pass"])
        scenes = self.stac_scenes["scenes"] if self.stac_scenes and self.stac_scenes["key"] == key else None
        if quick:
            out = os.path.join(tempfile.mkdtemp(prefix="rslab_ql_"), "quicklook.tif")
        else:
            out = self._out_path("%s_%s_%s_%s_%s" % (p["sensor"], src.split("_")[0], p["start"].replace("-", ""),
                                                      p["end"].replace("-", ""), method))
        descs = [catalog.band_desc(b, r, w) for b, r, w in chosen]
        meta = dict(RSLAB_SENSOR=p["sensor"], RSLAB_PRODUCT=product, RSLAB_START=p["start"], RSLAB_END=p["end"],
                    RSLAB_METHOD=method, RSLAB_SCALE=s["scale"] if product == "raw" else 1.0,
                    RSLAB_OFFSET=s["offset"] if product == "raw" else 0.0,
                    RSLAB_COLLECTION="%s / %s" % (src, se.STAC[src][p["sensor"]]["coll"]),
                    RSLAB_AOI=self.session.get("aoi_label", ""))
        log = []
        geom = QgsGeometry(self.aoi_geom)
        workers = self.par.value()

        def job(task):
            return se.download(task, src, p["sensor"], geom, p["start"], p["end"], p["max_cloud"], method, bands,
                               product, out, scale, epsg, self.clip.isChecked(), self.mask.isChecked(),
                               p["s1_pass"], self.max_scenes.value(), ids, scenes, descs, meta, log,
                               workers=workers)

        def done(ok, res, err):
            P = self._live()
            for line in log:
                P.log.appendPlainText(line)
            if not ok or not res:
                P.msg(tr("Download failed: %s") % (err or tr("cancelled")), "err")
                return
            src_text = "%s — %s" % (s["label"], se._cfg()[src]["attribution"])
            if quick:
                sfx = " %s %s" % (p["sensor"], p["start"])
                P._after_download(res["path"], None, bands, product, epsg, src_text,
                                  self.name("Quick look", sfx), ("Quick look", sfx))
            else:
                P._after_download(res["path"], p, bands, product, epsg, src_text)
                P.msg(tr("Saved %s") % res["path"] + " — " + tr("%d scenes used.") % res["n_scenes"] +
                         " (%.0f s)" % res.get("seconds", 0), "ok")
        t = self._task(tr("Downloading imagery (%s)") % self.src.currentText().split(" (")[0], job, on_done=done)
        self._bind_progress(t, "dl_prog")
        self.dl_prog.setValue(0)
        self.msg(tr("Reading scenes from the cloud… (see progress bar; details in the Setup log)"))

    # --- local band files
    def local_add_files(self):
        fns, _ = QFileDialog.getOpenFileNames(self, tr("Band files"), "", "Raster (*.tif *.TIF *.tiff *.jp2)")
        if fns:
            self._local_plan([d["path"] for d in self.local_items] + fns)

    def local_add_folder(self):
        d = QFileDialog.getExistingDirectory(self, tr("Folder with band files"))
        if d:
            from . import local_stack as ls
            self._local_plan([x["path"] for x in self.local_items] + ls.scan_folder(d))

    def local_clear(self):
        self.local_items = []
        self.local_table.setRowCount(0)

    def _local_plan(self, paths):
        from . import local_stack as ls
        self.local_items = ls.plan(paths)
        self.local_table.setRowCount(0)
        self.local_table.setVisible(bool(self.local_items))
        for d in self.local_items:
            r = self.local_table.rowCount()
            self.local_table.insertRow(r)
            use = QTableWidgetItem()
            use.setFlags(use.flags() | CHECKABLE)
            skip = d["role"] in ("cirrus", "watervapour", "tir2")
            use.setCheckState(UNCHECKED if skip else CHECKED)
            self.local_table.setItem(r, 0, use)
            for j, v in enumerate([d["band"], d["role"] or "—", "%g m" % d["pixel"], os.path.basename(d["path"])], 1):
                self.local_table.setItem(r, j, QTableWidgetItem(v))
        sens = sorted(set(d["sensor"] for d in self.local_items))
        self.msg(tr("%d band files recognised (%s).") % (len(self.local_items), ", ".join(sens)), "ok"
                 if self.local_items else "warn")

    def _download_local(self):
        from . import local_stack as ls
        items = [d for r, d in enumerate(self.local_items)
                 if self.local_table.item(r, 0) and self.local_table.item(r, 0).checkState() == CHECKED]
        if not items:
            self.msg(tr("Add band files first."), "warn")
            return
        default = os.path.splitext(os.path.basename(items[0]["path"]))[0]
        default = re.sub(r"_(SR_|ST_)?B\d+[A]?(_\d+m)?$", "", default, flags=re.I) + "_stack"
        out = self._out_path(default)
        geom = QgsGeometry(self.aoi_geom) if self.aoi_geom is not None else None
        epsg = self.crs.currentData() or None

        def job(task):
            return ls.build(task, items, out, self.scale.value() or None, geom, epsg, self.clip.isChecked())

        def done(ok, res, err):
            if not ok or not res:
                self.msg(tr("Could not build the band set: %s") % (err or tr("cancelled")), "err")
                return
            self._live()._after_download(res["path"], None, [], "raw", None, "Local files (%s)" % res["sensor"])
        t = self._task(tr("Building band set"), job, on_done=done)
        self._bind_progress(t, "dl_prog")

    # --- search / preview / download
    # ============================================================ 2 COMPOSITE & ENHANCE
    def band_combo(self, objname):
        c = QComboBox()
        c.setObjectName(objname)
        return c

    def fill_bands(self, lyr, combos, defaults=None):
        info = layer_info(lyr)
        if info is None:
            for c in combos:
                c.clear()
            return None
        for k, c in enumerate(combos):
            cur = c.currentData()
            c.blockSignals(True)
            c.clear()
            for i in range(1, info.count + 1):
                c.addItem(info.label(i), i)
            if defaults and k < len(defaults) and defaults[k]:
                c.setCurrentIndex(defaults[k] - 1)
            elif cur and cur <= info.count:
                c.setCurrentIndex(cur - 1)
            c.blockSignals(False)
        return info

    def fill_band_list(self, lyr, lw, prefer=None):
        lw.clear()
        info = layer_info(lyr)
        if info is None:
            return None
        skip = {"tir", "tir2", "cirrus", "watervapour", "coastal", "pan"}
        for i in range(1, info.count + 1):
            role = catalog.role_from_desc(info.descs[i - 1])
            add_check(lw, info.label(i), i, on=(role not in skip) if prefer is None else (i in prefer))
        return info

    def build_composite(self, P):
        lay = P.section("composite", tr("Band combination & stretch"), "composite").lay
        f = QFormLayout()
        self.c_raster = raster_combo()
        self.c_raster.setObjectName("c_raster")
        f.addRow(tr("Image"), self.c_raster)
        self.preset = QComboBox()
        self.preset.setObjectName("preset")
        f.addRow(tr("Band combination"), self.preset)
        self.preset_note = QLabel()
        self.preset_note.setWordWrap(True)
        self.preset_note.setStyleSheet("color:#356")
        f.addRow(self.preset_note)
        self.cr, self.cg, self.cbb = self.band_combo("cr"), self.band_combo("cg"), self.band_combo("cbb")
        f.addRow(tr("Red channel"), self.cr)
        f.addRow(tr("Green channel"), self.cg)
        f.addRow(tr("Blue channel"), self.cbb)
        self.stretch = QComboBox()
        self.stretch.setObjectName("stretch")
        for k, t in (("clip", tr("Percent clip")), ("minmax", tr("Min–max")), ("std", tr("Standard deviation")),
                     ("histeq", tr("Histogram equalization (saved file)"))):
            self.stretch.addItem(t, k)
        f.addRow(tr("Contrast stretch"), self.stretch)
        self.plo, self.phi, self.nstd, self.gamma = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for w, n, lo, hi, v, st in ((self.plo, "plo", 0, 49, 2, 0.5), (self.phi, "phi", 51, 100, 98, 0.5),
                                    (self.nstd, "nstd", 0.5, 5, 2, 0.5), (self.gamma, "gamma", 0.2, 5, 1, 0.1)):
            w.setObjectName(n)
            w.setRange(lo, hi)
            w.setValue(v)
            w.setSingleStep(st)
        f.addRow(tr("Clip low / high %"), row(self.plo, self.phi))
        f.addRow(tr("Std. dev. (n) / gamma"), row(self.nstd, self.gamma))
        lay.addLayout(f)
        lay.addLayout(row(btn(tr("Apply to layer"), self.apply_composite, primary=True),
                          btn(tr("Save enhanced RGB"), self.save_enhanced)))
        lay.addLayout(row(btn(tr("Histogram"), self.show_hist), btn(tr("Band statistics"), self.show_stats),
                          btn(tr("Spectral profile tool"), self.toggle_profile)))
        self.c_stats = table([tr("Band"), "Min", "Max", tr("Mean"), tr("Std"), tr("Median")], 110, hidden=True)
        lay.addWidget(self.c_stats)
        self.c_raster.layerChanged.connect(self.on_c_raster)
        self.preset.currentIndexChanged.connect(self.on_preset)
        self.on_c_raster(self.c_raster.currentLayer())

    def on_c_raster(self, lyr):
        info = self.fill_bands(lyr, [self.cr, self.cg, self.cbb])
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItem(tr("Custom"), None)
        if info:
            for name, roles, note in catalog.PRESETS:
                if all(r in info.roles or r == "ratio" for r in roles) and not (roles[2] == "ratio"):
                    self.preset.addItem(tr(name), [info.roles[r] for r in roles] + [note])
            if "vv" in info.roles and "vh" in info.roles:
                self.preset.addItem(tr("SAR (VV, VH, VV)"), [info.roles["vv"], info.roles["vh"], info.roles["vv"],
                                                            "Radar: urban bright, water dark, vegetation green-ish."])
        self.preset.blockSignals(False)
        if self.preset.count() > 1:
            self.preset.setCurrentIndex(1)
            self.on_preset()

    def on_preset(self):
        d = self.preset.currentData()
        if not d:
            self.preset_note.setText("")
            return
        for c, b in zip((self.cr, self.cg, self.cbb), d[:3]):
            c.setCurrentIndex(b - 1)
        self.preset_note.setText(tr(d[3]))

    def apply_composite(self):
        lyr = self.c_raster.currentLayer()
        if lyr is None:
            return
        rgb = (self.cr.currentData(), self.cg.currentData(), self.cbb.currentData())
        if None in rgb:
            return
        ren = QgsMultiBandColorRenderer(lyr.dataProvider(), *rgb)
        lyr.setRenderer(ren)
        k = self.stretch.currentData()
        if k == "histeq":
            self.save_enhanced()
            return
        origin = {"clip": QgsRasterMinMaxOrigin.CumulativeCut, "minmax": QgsRasterMinMaxOrigin.MinMax,
                  "std": QgsRasterMinMaxOrigin.StdDev}[k]
        lyr.setContrastEnhancement(QgsContrastEnhancement.StretchToMinimumMaximum, origin,
                                   lyr.extent(), 250000,
                                   ) if False else None
        # set limits explicitly so the clip % and n·σ from the panel are used
        ren = lyr.renderer()
        mm = ren.minMaxOrigin() if hasattr(ren, "minMaxOrigin") else None
        if mm is not None:
            mm.setLimits(origin)
            mm.setCumulativeCutLower(self.plo.value() / 100.0)
            mm.setCumulativeCutUpper(self.phi.value() / 100.0)
            mm.setStdDevFactor(self.nstd.value())
            ren.setMinMaxOrigin(mm)
        provider = lyr.dataProvider()
        for band, setter in zip(rgb, ("setRedContrastEnhancement", "setGreenContrastEnhancement",
                                      "setBlueContrastEnhancement")):
            st = provider.bandStatistics(band)
            if k == "minmax":
                lo, hi = st.minimumValue, st.maximumValue
            elif k == "std":
                lo, hi = st.mean - self.nstd.value() * st.stdDev, st.mean + self.nstd.value() * st.stdDev
            else:
                lo, hi = provider.cumulativeCut(band, self.plo.value() / 100.0, self.phi.value() / 100.0)
            ce = QgsContrastEnhancement(provider.dataType(band))
            ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum)
            ce.setMinimumValue(lo)
            ce.setMaximumValue(hi)
            getattr(ren, setter)(ce)
        lyr.triggerRepaint()
        self.iface.layerTreeView().refreshLayerSymbology(lyr.id())
        self.msg(tr("Composite %s applied.") % "%d-%d-%d" % rgb)

    def save_enhanced(self):
        lyr = self.c_raster.currentLayer()
        if lyr is None:
            return
        rgb = (self.cr.currentData(), self.cg.currentData(), self.cbb.currentData())
        k = self.stretch.currentData()
        out_dir = self.out_dir()

        def done(ok, res, err):
            if not ok:
                self.msg(err or tr("failed"), "err")
                return
            sfx = " %d-%d-%d %s" % (rgb + (k,))
            l2 = rio.add_rgb_layer(res["path"], self.name("Enhanced", sfx), (1, 2, 3), "minmax", tr(GROUP))
            self.tag(l2, "Enhanced", sfx)
            self.msg(tr("Saved %s") % res["path"], "ok")
        self._task(tr("Enhancing image"), procs.enhance,
                  (layer_path(lyr), rgb, k, out_dir, self.plo.value(), self.phi.value(), self.nstd.value(),
                   self.gamma.value(), "enhanced_%d%d%d_%s" % (rgb + (k,))), on_done=done)

    def show_hist(self):
        lyr = self.c_raster.currentLayer()
        if lyr is None:
            return
        bands = [self.cr.currentData(), self.cg.currentData(), self.cbb.currentData()]

        def done(ok, res, err):
            if ok:
                plots.plot_histograms(tr("Histogram"), res["hists"], self)
            else:
                self.msg(err, "err")
        self._task(tr("Computing histograms"), procs.histograms, (layer_path(lyr), bands), on_done=done)

    def show_stats(self):
        lyr = self.c_raster.currentLayer()
        if lyr is None:
            return

        def done(ok, res, err):
            if ok:
                fill(self.c_stats, res["rows"], "%.4f")
            else:
                self.msg(err, "err")
        self._task(tr("Computing statistics"), procs.stats_table, (layer_path(lyr),), on_done=done)

    def toggle_profile(self):
        if self.profile_tool is None:
            self.profile_tool = plots.SpectralProfileTool(self.canvas, lambda: self.c_raster.currentLayer())
        if self.canvas.mapTool() is self.profile_tool:
            self.canvas.unsetMapTool(self.profile_tool)
            self.profile_tool.clear()
        else:
            self.profile_tool.clear()
            self.canvas.setMapTool(self.profile_tool)
            self.msg(tr("Click pixels on the map to plot their spectra (up to 8)."))

    # ============================================================ 3 INDICES
    def build_indices(self, P):
        lay = P.section("indices", tr("Spectral indices & band math"), "indices").lay
        f = QFormLayout()
        self.i_raster = raster_combo()
        self.i_raster.setObjectName("i_raster")
        f.addRow(tr("Image"), self.i_raster)
        lay.addLayout(f)
        self.role_table = table([tr("Role"), tr("Band")], 110, editable=False)
        roles_btn = QToolButton()
        roles_btn.setText(tr("Band roles (read from band names; change if needed):"))
        roles_btn.setCheckable(True)
        roles_btn.setAutoRaise(True)
        roles_btn.setToolButtonStyle(E(Qt, "ToolButtonStyle.ToolButtonTextBesideIcon"))
        roles_btn.setArrowType(E(Qt, "ArrowType.RightArrow"))
        roles_btn.toggled.connect(lambda on: (self.role_table.setVisible(on), roles_btn.setArrowType(
            E(Qt, "ArrowType.DownArrow" if on else "ArrowType.RightArrow"))))
        self.role_table.setVisible(False)
        lay.addWidget(roles_btn)
        lay.addWidget(self.role_table)
        self.do_scale = QCheckBox(tr("Convert raw DN to reflectance first"))
        self.do_scale.setObjectName("do_scale")
        self.i_scale, self.i_off = QDoubleSpinBox(), QDoubleSpinBox()
        for w, n in ((self.i_scale, "i_scale"), (self.i_off, "i_off")):
            w.setObjectName(n)
            w.setDecimals(7)
            w.setRange(-10, 10)
        self.i_scale.setValue(1)
        lay.addWidget(self.do_scale)
        lay.addLayout(row(QLabel(tr("scale")), self.i_scale, QLabel(tr("offset")), self.i_off))
        self.idx_list = checklist(120)
        for k, (fm, desc, _) in catalog.INDICES.items():
            add_check(self.idx_list, "%s — %s" % (k, tr(desc)), k, on=k in ("NDVI", "MNDWI"), tip=fm)
        lay.addWidget(self.idx_list)
        sub(lay, tr("Band math (your own formula)"))
        gf = QFormLayout()
        self.cx_name, self.cx_expr = QLineEdit("my_index"), QLineEdit()
        self.cx_name.setObjectName("cx_name")
        self.cx_expr.setObjectName("cx_expr")
        self.cx_expr.setPlaceholderText("(nir - red) / (nir + red)    or    b8 / b4")
        gf.addRow(tr("Name"), self.cx_name)
        gf.addRow(tr("Expression"), self.cx_expr)
        gf.addRow(QLabel("<small>%s</small>" % tr("Use role names (blue, green, red, nir, swir1, swir2, rededge1…) "
                                                  "or band numbers b1, b2…; functions: sqrt, log, exp, abs, where.")))
        lay.addLayout(gf)
        self.otsu = QCheckBox(tr("Also make an Otsu threshold mask (e.g. water / land)"))
        self.otsu.setObjectName("otsu")
        lay.addWidget(self.otsu)
        lay.addLayout(row(btn(tr("Compute indices"), self.run_indices, primary=True), "stretch"))
        self.idx_res = table([tr("Index"), "Min", "Max", tr("Mean"), "P2", "P98", "Otsu"], 110, hidden=True)
        lay.addWidget(self.idx_res)
        self.i_raster.layerChanged.connect(self.on_i_raster)
        self.on_i_raster(self.i_raster.currentLayer())

    def on_i_raster(self, lyr):
        self.role_table.setRowCount(0)
        info = layer_info(lyr)
        if info is None:
            return
        for role in catalog.ALL_ROLES:
            i = self.role_table.rowCount()
            self.role_table.insertRow(i)
            self.role_table.setItem(i, 0, QTableWidgetItem(role))
            cb = QComboBox()
            cb.addItem("—", None)
            for b in range(1, info.count + 1):
                cb.addItem(info.label(b), b)
            if role in info.roles:
                cb.setCurrentIndex(info.roles[role])
            self.role_table.setCellWidget(i, 1, cb)
        prod = info.meta.get("RSLAB_PRODUCT", "")
        self.do_scale.setChecked(prod == "raw")
        self.i_scale.setValue(float(info.meta.get("RSLAB_SCALE", 1.0)))
        self.i_off.setValue(float(info.meta.get("RSLAB_OFFSET", 0.0)))

    def roles_from_table(self):
        out = {}
        for i in range(self.role_table.rowCount()):
            cb = self.role_table.cellWidget(i, 1)
            if cb and cb.currentData():
                out[self.role_table.item(i, 0).text()] = cb.currentData()
        return out

    def run_indices(self):
        lyr = self.i_raster.currentLayer()
        if lyr is None:
            return
        names = checked_data(self.idx_list)
        custom = (self.cx_name.text().strip(), self.cx_expr.text().strip()) if self.cx_expr.text().strip() else None
        sc, off = (self.i_scale.value(), self.i_off.value()) if self.do_scale.isChecked() else (1.0, 0.0)
        ramps = {"NDVI": "RdYlGn", "EVI": "RdYlGn", "SAVI": "RdYlGn", "GNDVI": "RdYlGn", "NDRE": "RdYlGn",
                 "NDWI": "Blues", "MNDWI": "Blues", "NDMI": "BrBG", "NDBI": "Reds", "UI": "Reds",
                 "NBR": "RdYlGn", "BSI": "YlOrBr", "RVI_SAR": "Greens"}

        def done(ok, res, err):
            if not ok:
                self.msg(err, "err")
                return
            rows = []
            self.session["indices"] = []
            for r in res["results"]:
                if "error" in r:
                    rows.append([r["name"], r["error"], "", "", "", "", ""])
                    continue
                l2 = rio.add_single_layer(r["path"], r["name"], ramps.get(r["name"], "Spectral"), r["p2"], r["p98"],
                                          tr(GROUP))
                if "mask" in r:
                    mk = rio.add_class_layer(r["mask"], self.name("Otsu mask", " " + r["name"]),
                                             [(1, "below threshold", "#e8e2c8"), (2, "above threshold", "#2b83ba")],
                                             tr(GROUP))
                    self.tag(mk, "Otsu mask", " " + r["name"])
                rows.append([r["name"], r["min"], r["max"], r["mean"], r["p2"], r["p98"], r.get("otsu", float("nan"))])
                self.session["indices"].append(rows[-1])
                _ = l2
            fill(self.idx_res, rows)
            self.msg(tr("Indices computed."), "ok")
        self._task(tr("Computing indices"), procs.compute_indices,
                  (layer_path(lyr), self.roles_from_table(), names, self.out_dir(), custom, sc, off,
                   self.otsu.isChecked()), on_done=done)

    # ============================================================ 4 FILTERS & PCA
    def build_filters(self, P):
        lay = P.section("filter", tr("Spatial filter"), "filters").lay
        f = QFormLayout()
        lay.addLayout(f)
        self.f_raster = raster_combo()
        self.f_raster.setObjectName("f_raster")
        f.addRow(tr("Image"), self.f_raster)
        self.f_band = self.band_combo("f_band")
        f.addRow(tr("Band"), self.f_band)
        self.f_kind = QComboBox()
        self.f_kind.setObjectName("f_kind")
        for k, t in (("mean", tr("Mean (low-pass)")), ("median", tr("Median (removes speckle/noise)")),
                     ("gaussian", tr("Gaussian (low-pass)")), ("highpass", tr("High-pass sharpen")),
                     ("sobel", tr("Sobel edges")), ("laplacian", tr("Laplacian edges")), ("emboss", tr("Emboss"))):
            self.f_kind.addItem(t, k)
        f.addRow(tr("Filter"), self.f_kind)
        self.f_k = QSpinBox()
        self.f_k.setObjectName("f_k")
        self.f_k.setRange(3, 15)
        self.f_k.setSingleStep(2)
        self.f_sigma = QDoubleSpinBox()
        self.f_sigma.setObjectName("f_sigma")
        self.f_sigma.setRange(0.3, 5)
        self.f_sigma.setValue(1.0)
        f.addRow(tr("Kernel size / sigma"), row(self.f_k, self.f_sigma))
        f.addRow(btn(tr("Apply filter"), self.run_filter, primary=True))
        gl = P.section("pca", tr("Principal Component Analysis")).lay
        self.p_raster_lab = QLabel()
        self.p_raster_lab.setStyleSheet("color:#567;font-size:11px")
        gl.addWidget(self.p_raster_lab)
        self.p_bands = checklist(100)
        gl.addWidget(self.p_bands)
        self.p_n = QSpinBox()
        self.p_n.setObjectName("p_n")
        self.p_n.setRange(1, 20)
        self.p_n.setValue(3)
        gl.addLayout(row(QLabel(tr("Components")), self.p_n, btn(tr("Run PCA"), self.run_pca, primary=True)))
        self.p_table = table([tr("Component"), tr("Eigenvalue"), tr("Explained %"), tr("Cumulative %")], 110,
                             hidden=True)
        gl.addWidget(self.p_table)
        self.p_load = table([tr("Band"), "PC1", "PC2", "PC3"], 110, hidden=True)
        self.p_load.setToolTip(tr("Loadings (eigenvectors):"))
        gl.addWidget(self.p_load)
        self.f_raster.layerChanged.connect(self.on_f_raster)
        self.on_f_raster(self.f_raster.currentLayer())

    def on_f_raster(self, lyr):
        info = self.fill_bands(lyr, [self.f_band])
        if info and "nir" in info.roles:
            self.f_band.setCurrentIndex(info.roles["nir"] - 1)
        self.fill_band_list(lyr, self.p_bands)
        self.p_raster_lab.setText("%s: <b>%s</b>" % (tr("Image"), lyr.name()) if lyr is not None else
                                  tr("Choose the image in the Spatial filter section."))

    def run_filter(self):
        lyr = self.f_raster.currentLayer()
        if lyr is None or self.f_band.currentData() is None:
            return
        k = self.f_kind.currentData()

        def done(ok, res, err):
            if ok:
                sfx = " %s b%d" % (k, self.f_band.currentData() or 0)
                self.tag(rio.add_gray_layer(res["path"], self.name("Filter", sfx), tr(GROUP)), "Filter", sfx)
                self.msg(tr("Saved %s") % res["path"], "ok")
            else:
                self.msg(err, "err")
        self._task(tr("Filtering"), procs.spatial_filter,
                  (layer_path(lyr), self.f_band.currentData(), k, self.out_dir(), self.f_k.value(),
                   self.f_sigma.value()), on_done=done)

    def run_pca(self):
        lyr = self.f_raster.currentLayer()
        bands = checked_data(self.p_bands)
        if lyr is None or len(bands) < 2:
            self.msg(tr("Choose an image and at least two bands."), "warn")
            return

        def done(ok, res, err):
            if not ok:
                self.msg(err, "err")
                return
            self.session["pca"] = res
            self.render_pca()
            n = res["n"]
            l2 = rio.add_rgb_layer(res["path"], self.name("PCA"), (1, min(2, n), min(3, n)), "std", tr(GROUP))
            self.tag(l2, "PCA")
            self.msg(tr("PCA done."), "ok")
        self._task(tr("PCA"), procs.pca_job, (layer_path(lyr), bands, self.p_n.value(), self.out_dir()), on_done=done)

    def render_pca(self):
        res = self.session.get("pca")
        if not res:
            return
        cum = np.cumsum(res["explained"])
        fill(self.p_table, [["PC%d" % (i + 1), float(e), float(x), float(c)]
                            for i, (e, x, c) in enumerate(zip(res["eig"], res["explained"], cum))], "%.3f")
        n = res["n"]
        self.p_load.setColumnCount(n + 1)
        self.p_load.setHorizontalHeaderLabels([tr("Band")] + ["PC%d" % (i + 1) for i in range(n)])
        fill(self.p_load, [[r[0].split("|")[0]] + r[1:] for r in res["loadings"]])

    # ============================================================ 5 UNSUPERVISED
    def build_unsup(self, P):
        lay = P.section("unsup", tr("Unsupervised (K-means, ISODATA)"), "unsupervised").lay
        f = QFormLayout()
        self.u_form = f
        self.u_raster = raster_combo()
        self.u_raster.setObjectName("u_raster")
        f.addRow(tr("Image"), self.u_raster)
        self.u_bands = checklist(100)
        f.addRow(tr("Bands"), self.u_bands)
        self.u_method = QComboBox()
        self.u_method.setObjectName("u_method")
        self.u_method.addItem("K-means", "kmeans")
        self.u_method.addItem("ISODATA", "isodata")
        f.addRow(tr("Method"), self.u_method)
        self.u_k, self.u_kmin, self.u_kmax, self.u_it = QSpinBox(), QSpinBox(), QSpinBox(), QSpinBox()
        for w, n, lo, hi, v in ((self.u_k, "u_k", 2, 50, 8), (self.u_kmin, "u_kmin", 2, 50, 3),
                                (self.u_kmax, "u_kmax", 2, 60, 16), (self.u_it, "u_it", 1, 200, 20)):
            w.setObjectName(n)
            w.setRange(lo, hi)
            w.setValue(v)
        f.addRow(tr("Number of clusters (k)"), self.u_k)
        self.u_iso_row = row(self.u_kmin, self.u_kmax)
        f.addRow(tr("ISODATA min / max clusters"), self.u_iso_row)
        self.u_method.currentIndexChanged.connect(self.on_u_method)
        f.addRow(tr("Iterations"), self.u_it)
        self.u_sample = QSpinBox()
        self.u_sample.setObjectName("u_sample")
        self.u_sample.setRange(1000, 1000000)
        self.u_sample.setSingleStep(10000)
        self.u_sample.setValue(60000)
        f.addRow(tr("Training sample (pixels)"), self.u_sample)
        self.u_maj = QSpinBox()
        self.u_maj.setObjectName("u_maj")
        self.u_maj.setRange(0, 15)
        self.u_maj.setSingleStep(2)
        f.addRow(tr("Majority filter (0 = off)"), self.u_maj)
        lay.addLayout(f)
        lay.addLayout(row(btn(tr("Run clustering"), self.run_unsup, primary=True), "stretch"))
        self.u_help = QLabel("<small>%s</small>" % tr(
            "Label the clusters: choose a class for each cluster — clusters with the same class are merged."))
        self.u_help.setWordWrap(True)
        self.u_table = table([tr("Cluster"), tr("Area (ha)"), "%", tr("→ class"), tr("Mean spectrum")], 120,
                             editable=False, hidden=True)
        self.u_after = QWidget()
        ua = QVBoxLayout(self.u_after)
        ua.setContentsMargins(0, 0, 0, 0)
        ua.addWidget(self.u_help)
        ua.addWidget(self.u_table)
        ua.addLayout(row(btn("🎨 " + tr("Classes (legend)") + "…", lambda: self.go("classes")),
                         btn(tr("Plot cluster spectra"), self.plot_clusters),
                         btn(tr("Create labelled map"), self.recode_clusters, primary=True)))
        self.u_after.setVisible(False)
        lay.addWidget(self.u_after)
        self.on_u_method()
        self.u_raster.layerChanged.connect(lambda l: self.fill_band_list(l, self.u_bands))
        self.fill_band_list(self.u_raster.currentLayer(), self.u_bands)

    def on_u_method(self):
        """K-means: k clusters. ISODATA: k is only the starting number; it splits/merges between min and max."""
        iso = self.u_method.currentData() == "isodata"
        self.u_kmin.setEnabled(iso)
        self.u_kmax.setEnabled(iso)
        lab = self.u_form.labelForField(self.u_k)
        if lab is not None:
            lab.setText(tr("Initial clusters (ISODATA)") if iso else tr("Number of clusters (k)"))
        for w in (self.u_kmin, self.u_kmax):
            w.setToolTip("" if iso else tr("Used by ISODATA only."))

    def run_unsup(self):
        lyr = self.u_raster.currentLayer()
        bands = checked_data(self.u_bands)
        if lyr is None or not bands:
            self.msg(tr("Choose an image and bands."), "warn")
            return
        m = self.u_method.currentData()

        def done(ok, res, err):
            if not ok or not res:
                self.msg(err or tr("cancelled"), "err")
                return
            res["method"] = "K-means" if m == "kmeans" else "ISODATA"
            self.session["unsupervised"] = res
            self.render_unsup()
            self.style_clusters()
            self.msg(tr("%d clusters.") % res["k"], "ok")
        self._task(tr("Clustering"), procs.unsupervised,
                  (layer_path(lyr), bands, m, self.u_k.value(), self.out_dir(), self.u_it.value(),
                   self.u_sample.value(), self.u_kmin.value(), self.u_kmax.value(), self.u_maj.value()),
                  on_done=done)

    def style_clusters(self):
        res = self.session.get("unsupervised")
        if not res:
            return
        cls = [(i + 1, "%s %s", rio.PALETTE[i % len(rio.PALETTE)], ["Cluster", str(i + 1)]) for i in range(res["k"])]
        l2 = rio.add_class_layer(res["path"], self.name("Clusters", " (%s)" % res["method"]), cls, tr(GROUP))
        self.tag(l2, "Clusters", " (%s)" % res["method"])

    def render_unsup(self):
        res = self.session.get("unsupervised")
        if not res:
            return
        area = {v: (a, p) for v, _, a, p in res["areas"]}
        self.u_after.setVisible(True)
        self.u_table.setVisible(True)
        self.u_table.setRowCount(0)
        for i, c in enumerate(res["centres"]):
            r = self.u_table.rowCount()
            self.u_table.insertRow(r)
            a, p = area.get(i + 1, (0.0, 0.0))
            vals = ["%d" % (i + 1), "%.1f" % a, "%.1f" % p, "", ", ".join("%.3g" % v for v in c)]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setBackground(QColor(rio.PALETTE[i % len(rio.PALETTE)]) if j == 0 else it.background())
                self.u_table.setItem(r, j, it)
            cb = QComboBox()
            cb.addItem("—", None)
            for cid, n, col in self.classes():
                cb.addItem("%d  %s" % (cid, tr(n)), cid)
                cb.setItemData(cb.count() - 1, QColor(col), E(Qt, "ItemDataRole.DecorationRole"))
            want = res.get("recode", {}).get(str(i + 1))
            if want is not None and cb.findData(want) >= 0:
                cb.setCurrentIndex(cb.findData(want))
            cb.currentIndexChanged.connect(lambda *_: self._save_recode())
            self.u_table.setCellWidget(r, 3, cb)

    def plot_clusters(self):
        res = self.session.get("unsupervised")
        if not res:
            return
        series = [("%s %d" % ("Cluster", i + 1), c, rio.PALETTE[i % len(rio.PALETTE)], None)
                  for i, c in enumerate(res["centres"])]
        plots.plot_signatures(tr("Cluster mean spectra"), res["bands"], series, self)

    def _save_recode(self):
        res = self.session.get("unsupervised")
        mapping = {}
        for r in range(self.u_table.rowCount()):
            cb = self.u_table.cellWidget(r, 3)
            if cb is not None and cb.currentData() is not None:
                mapping[r + 1] = int(cb.currentData())
        if res is not None:
            res["recode"] = {str(k): v for k, v in mapping.items()}
        return mapping

    def recode_clusters(self):
        res = self.session.get("unsupervised")
        if not res:
            return
        mapping = self._save_recode()
        if not mapping:
            self.msg(tr("Choose a class for each cluster first."), "warn")
            return
        res["recode"] = {str(k): v for k, v in mapping.items()}
        from osgeo import gdal
        info = rio.RasterInfo(res["path"])
        a = np.nan_to_num(rio.read_band(res["path"], 1)).astype("int32")
        lut = np.zeros(max(a.max(), max(mapping)) + 1, "int32")
        for k, v in mapping.items():
            lut[k] = v
        out = res["path"][:-4] + "_labelled.tif"
        rio.write_array(out, lut[a].astype("uint8"), info, gdal.GDT_Byte, 0)
        cl = [c for c in self.classes() if c[0] in set(mapping.values())]  # English keys, shown translated
        l2 = rio.add_class_layer(out, self.name("Labelled clusters"), cl, tr(GROUP))
        self.tag(l2, "Labelled clusters")
        a2 = lut[a]
        res["areas"] = procs.R.class_areas(a2, info.pixel_area)
        res["labelled"] = out
        self.msg(tr("Saved %s") % out, "ok")

    # ============================================================ 6 SUPERVISED
    def classes(self):
        cl = self.session.get("classes") or [list(c) for c in rio.DEFAULT_CLASSES]
        return [tuple(c) for c in cl]

    SCHEMES = [
        ("rslab", "RS Lab default (6 classes)", [list(c) for c in rio.DEFAULT_CLASSES]),
        ("lulc", "LULC level 1 (8 classes)", [[1, "Forest", "#1a7431"], [2, "Agriculture", "#f2d43d"],
                                              [3, "Barren", "#b08a5a"], [4, "Built-up", "#d7191c"],
                                              [5, "Water", "#2b7cb5"], [6, "Grassland", "#a3d977"],
                                              [7, "Shrubland", "#8f9e3d"], [8, "Other", "#9e9e9e"]]),
        ("auto7", "Auto 7 (from ESA WorldCover)", [[1, "Water", "#0000ff"], [2, "Vegetation", "#228b22"],
                                                   [3, "Built-up", "#ff0000"], [4, "Bare soil", "#d2b48c"],
                                                   [5, "Wetland", "#00ced1"], [6, "Cropland", "#ffa500"],
                                                   [7, "Grassland", "#7fff00"]]),
        ("worldcover", "ESA WorldCover (11 classes)", [[10, "Tree cover", "#006400"], [20, "Shrubland", "#ffbb22"],
                                                       [30, "Grassland", "#ffff4c"], [40, "Cropland", "#f096ff"],
                                                       [50, "Built-up", "#fa0000"],
                                                       [60, "Bare / sparse vegetation", "#b4b4b4"],
                                                       [70, "Snow and ice", "#f0f0f0"], [80, "Permanent water", "#0064c8"],
                                                       [90, "Herbaceous wetland", "#0096a0"],
                                                       [95, "Mangroves", "#00cf75"], [100, "Moss and lichen", "#fae6a0"]]),
    ]
    # ESA WorldCover code → class id of the "auto7" scheme (as in the GEE LULC Toolkit)
    WC_TO_AUTO7 = {10: 2, 20: 2, 30: 7, 40: 6, 50: 3, 60: 4, 70: 4, 80: 1, 90: 5, 95: 5, 100: 2}

    def build_classes(self, P):
        lay = P.section("classes", "🎨 " + tr("Classes (legend)")).lay
        lay.addWidget(QLabel("<small>%s</small>" % tr(
            "One class list for the whole tab: cluster labelling, training, classification, accuracy, change and "
            "polygons. Double-click a colour to change it.")))
        self.scheme = QComboBox()
        self.scheme.setObjectName("scheme")
        for k, t, _ in self.SCHEMES:
            self.scheme.addItem(tr(t), k)
        lay.addLayout(row(self.scheme, btn(tr("Use scheme"), self.apply_scheme)))
        self.cls_table = table([tr("ID"), tr("Name"), tr("Colour")], 120, editable=True)
        self.cls_table.cellDoubleClicked.connect(self.pick_colour)
        lay.addWidget(self.cls_table)
        lay.addLayout(row(btn(tr("Add"), self.add_class), btn(tr("Remove"), self.del_class),
                          btn(tr("Save…"), self.save_classes), btn(tr("Open…"), self.load_classes)))
        self.render_classes()

    def apply_scheme(self):
        k = self.scheme.currentData()
        self.session["classes"] = [list(c) for c in next(c for kk, _, c in self.SCHEMES if kk == k)]
        self.render_classes()
        self.msg(tr("Class scheme: %s") % self.scheme.currentText(), "ok")

    def save_classes(self):
        fn, _ = QFileDialog.getSaveFileName(self, tr("Save classes"), os.path.join(self.out_dir(), "classes.csv"),
                                            "CSV (*.csv)")
        if fn:
            with open(fn, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["id", "name", "colour"])
                w.writerows([list(c) for c in self.classes()])

    def load_classes(self):
        fn, _ = QFileDialog.getOpenFileName(self, tr("Open classes"), self.out_dir(), "CSV (*.csv)")
        if not fn:
            return
        out = []
        with open(fn, encoding="utf-8-sig") as f:
            for r in csv.reader(f):
                if len(r) >= 2 and r[0].strip().isdigit():
                    out.append([int(r[0]), r[1].strip(), (r[2].strip() if len(r) > 2 and r[2].strip() else
                                                           rio.PALETTE[(int(r[0]) - 1) % len(rio.PALETTE)])])
        if out:
            self.session["classes"] = out
            self.render_classes()

    def build_sup(self, P):
        lay = P.section("sup_train", tr("Supervised — classes & training"), "supervised").lay
        f = QFormLayout()
        self.s_raster = raster_combo()
        self.s_raster.setObjectName("s_raster")
        f.addRow(tr("Image"), self.s_raster)
        self.s_bands = checklist(100)
        f.addRow(tr("Bands"), self.s_bands)
        lay.addLayout(f)
        lay.addLayout(row(btn("🎨 " + tr("Classes (legend)") + "…", lambda: self.go("classes")), "stretch"))
        sub(lay, tr("Training data"))
        gf = QFormLayout()
        lay.addLayout(gf)
        gf.addRow(btn(tr("Create training layer (polygons)"), self.create_training, primary=True))
        self.t_layer = QgsMapLayerComboBox()
        self.t_layer.setObjectName("t_layer")
        set_layer_filters(self.t_layer, "polygon", "point")
        gf.addRow(tr("Training layer"), self.t_layer)
        self.t_field = QgsFieldComboBox()
        self.t_field.setObjectName("t_field")
        self.t_field.setFilters(field_filter_numeric())
        self.t_layer.layerChanged.connect(self.t_field.setLayer)
        self.t_field.setLayer(self.t_layer.currentLayer())
        gf.addRow(tr("Class id field"), self.t_field)
        # ---- automatic training points from ESA WorldCover (idea from the GEE LULC Toolkit "Auto LULC")
        self.wc_box = QWidget()
        wl = QVBoxLayout(self.wc_box)
        wl.setContentsMargins(0, 2, 0, 2)
        wl.addWidget(QLabel("<small>%s</small>" % tr(
            "Or let ESA WorldCover (10 m, 2021) label the training points: classes become the Auto 7 scheme "
            "(water, vegetation, built-up, bare, wetland, cropland, grassland); only 'pure' pixels are used. "
            "WorldCover is a 2021 map — check the points if your image is from another year.")))
        self.wc_layer = raster_combo()
        self.wc_layer.setObjectName("wc_layer")
        self.wc_n = QSpinBox()
        self.wc_n.setObjectName("wc_n")
        self.wc_n.setRange(20, 5000)
        self.wc_n.setValue(300)
        self.wc_n.setSuffix(" / " + tr("class"))
        wf = QFormLayout()
        wf.addRow(tr("WorldCover layer"), self.wc_layer)
        wf.addRow(tr("Points per class"), self.wc_n)
        wl.addLayout(wf)
        wl.addLayout(row(btn(tr("Get WorldCover for the AOI"), self.get_worldcover),
                         btn(tr("Make training points"), self.wc_training, primary=True)))
        wbtn = QToolButton()
        wbtn.setText(tr("Automatic training from ESA WorldCover"))
        wbtn.setCheckable(True)
        wbtn.setAutoRaise(True)
        wbtn.setToolButtonStyle(E(Qt, "ToolButtonStyle.ToolButtonTextBesideIcon"))
        wbtn.setArrowType(E(Qt, "ArrowType.RightArrow"))
        wbtn.toggled.connect(lambda on: (self.wc_box.setVisible(on), wbtn.setArrowType(
            E(Qt, "ArrowType.DownArrow" if on else "ArrowType.RightArrow"))))
        self.wc_box.setVisible(False)
        gf.addRow(wbtn)
        gf.addRow(self.wc_box)
        gf.addRow(row(btn(tr("Signatures + separability"), self.run_signatures),
                      btn(tr("Feature space plot"), self.feature_space)))
        self.fs_x, self.fs_y = self.band_combo("fs_x"), self.band_combo("fs_y")
        gf.addRow(tr("Feature space X / Y"), row(self.fs_x, self.fs_y))
        self.jm_table = table([tr("Class pair"), "J–M"], 110, hidden=True)
        lay.addWidget(self.jm_table)
        lay = P.section("sup_clf", tr("Supervised — classify")).lay
        self.s_img_lab = QLabel()
        self.s_img_lab.setWordWrap(True)
        self.s_img_lab.setStyleSheet("color:#567;font-size:11px")
        lay.addWidget(self.s_img_lab)
        gf = QFormLayout()
        lay.addLayout(gf)
        self.clf = QComboBox()
        self.clf.setObjectName("clf")
        for k, t in (("maxlik", tr("Maximum Likelihood")), ("mindist", tr("Minimum Distance")),
                     ("sam", tr("Spectral Angle Mapper")), ("para", tr("Parallelepiped")),
                     ("rf", tr("Random Forest (scikit-learn)")), ("svm", tr("Support Vector Machine (scikit-learn)")),
                     ("knn", tr("k-Nearest Neighbours (scikit-learn)"))):
            self.clf.addItem(t, k)
        gf.addRow(tr("Algorithm"), self.clf)
        self.clf_param = QDoubleSpinBox()
        self.clf_param.setObjectName("clf_param")
        self.clf_param.setDecimals(3)
        self.clf_param.setRange(0, 100000)
        self.clf_param_lab = QLabel()
        gf.addRow(self.clf_param_lab, self.clf_param)
        self.s_maj = QSpinBox()
        self.s_maj.setObjectName("s_maj")
        self.s_maj.setRange(0, 15)
        self.s_maj.setSingleStep(2)
        gf.addRow(tr("Majority filter (0 = off)"), self.s_maj)
        self.s_raster2 = raster_combo()
        self.s_raster2.setObjectName("s_raster2")
        self.s_raster2.setToolTip(tr("Optional: an image of another date with the same bands (e.g. Landsat 2024). "
                                     "It is classified with the SAME trained model, ready for from → to change."))
        gf.addRow(tr("Also classify another date"), self.s_raster2)
        self.clf.currentIndexChanged.connect(self.on_clf)
        self.on_clf()
        lay.addLayout(row(btn(tr("Classify"), self.run_sup, primary=True), btn(tr("Area chart"), self.area_chart)))
        self.s_info = QLabel()
        self.s_info.setWordWrap(True)
        lay.addWidget(self.s_info)
        self.s_areas = table([tr("Class"), tr("Pixels"), tr("Area (ha)"), "%"], 110, hidden=True)
        lay.addWidget(self.s_areas)
        self.s_raster.layerChanged.connect(self.on_s_raster)
        self.t_layer.layerChanged.connect(lambda *_: self.update_s_img_lab())
        self.t_field.fieldChanged.connect(lambda *_: self.update_s_img_lab())
        self.on_s_raster(self.s_raster.currentLayer())

    def on_s_raster(self, lyr):
        info = self.fill_band_list(lyr, self.s_bands)
        d = [info.roles.get("red"), info.roles.get("nir")] if info else None
        self.fill_bands(lyr, [self.fs_x, self.fs_y], d)
        self.update_s_img_lab()

    def update_s_img_lab(self):
        lyr, tl = self.s_raster.currentLayer(), self.t_layer.currentLayer()
        self.s_img_lab.setText("%s: <b>%s</b> · %s: <b>%s</b> (%s)" % (
            tr("Image"), lyr.name() if lyr else "—", tr("Training layer"), tl.name() if tl else "—",
            self.t_field.currentField() or "—"))

    def on_clf(self):
        k = self.clf.currentData()
        lab, val, en = {"maxlik": (tr("Min. probability (0 = classify all)"), 0.0, True),
                        "mindist": (tr("Max. distance (0 = none)"), 0.0, True),
                        "sam": (tr("Max. angle, radians (0 = none)"), 0.0, True),
                        "para": (tr("Box size (± n std. dev.)"), 2.0, True),
                        "rf": (tr("Number of trees"), 200, True), "svm": (tr("C (regularisation)"), 10.0, True),
                        "knn": (tr("k neighbours"), 5, True)}[k]
        self.clf_param_lab.setText(lab)
        self.clf_param.setValue(val)
        self.clf_param.setEnabled(en)

    def render_classes(self):
        self.cls_table.setRowCount(0)
        for v, n, c in self.classes():
            r = self.cls_table.rowCount()
            self.cls_table.insertRow(r)
            self.cls_table.setItem(r, 0, QTableWidgetItem(str(v)))
            nit = QTableWidgetItem(tr(n))
            nit.setData(USER_ROLE, n)
            self.cls_table.setItem(r, 1, nit)
            it = QTableWidgetItem(c)
            it.setBackground(QColor(c))
            it.setFlags(it.flags() & ~EDITABLE)
            self.cls_table.setItem(r, 2, it)
        try:
            self.cls_table.itemChanged.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.cls_table.itemChanged.connect(self.read_classes)
        self._refresh_class_users()

    def _refresh_class_users(self):
        """Class names changed → update the cluster → class drop-downs and the labelling buttons."""
        if getattr(self, "lb_flow", None) is not None:
            self.lb_build_buttons()
        if getattr(self, "u_table", None) is not None and self.session.get("unsupervised"):
            self._save_recode()
            self.render_unsup()

    def read_classes(self, *a):
        out = []
        for r in range(self.cls_table.rowCount()):
            try:
                v = int(self.cls_table.item(r, 0).text())
            except Exception:  # noqa: BLE001
                continue
            it = self.cls_table.item(r, 1)
            n = it.text() if it else str(v)
            key = it.data(USER_ROLE) if it else None
            if key and n == tr(key):
                n = key          # unchanged → keep the English key so it can be re-translated
            c = self.cls_table.item(r, 2).text() if self.cls_table.item(r, 2) else "#888888"
            out.append([v, n, c])
        self.session["classes"] = out
        self._refresh_class_users()

    def pick_colour(self, r, c):
        if c != 2:
            return
        col = QColorDialog.getColor(QColor(self.cls_table.item(r, 2).text()), self)
        if col.isValid():
            it = self.cls_table.item(r, 2)
            it.setText(col.name())
            it.setBackground(col)
            self.read_classes()

    def add_class(self):
        cl = self.classes()
        nid = max([c[0] for c in cl] + [0]) + 1
        cl.append((nid, "Class %d" % nid, rio.PALETTE[(nid - 1) % len(rio.PALETTE)]))
        self.session["classes"] = [list(c) for c in cl]
        self.render_classes()

    def del_class(self):
        r = self.cls_table.currentRow()
        if r < 0:
            return
        cl = self.classes()
        del cl[r]
        self.session["classes"] = [list(c) for c in cl]
        self.render_classes()

    def default_classes(self):
        self.session["classes"] = [list(c) for c in rio.DEFAULT_CLASSES]
        self.render_classes()

    def create_training(self):
        lyr = self.s_raster.currentLayer()
        crs = lyr.crs() if lyr else self.canvas.mapSettings().destinationCrs()
        fn, _ = QFileDialog.getSaveFileName(self, tr("Save training layer"), os.path.join(self.out_dir(), "training.gpkg"),
                                            "GeoPackage (*.gpkg)")
        if not fn:
            return
        fields = QgsFields()
        fields.append(QgsField("class_id", _ftype("int")))
        fields.append(QgsField("class_name", _ftype("str")))
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = "training"
        w = QgsVectorFileWriter.create(fn, fields, _multipolygon(), crs,
                                       QgsProject.instance().transformContext(), opts)
        del w
        vl = QgsVectorLayer(fn + "|layername=training", self.name("Training areas"), "ogr")
        self.style_training(vl)
        QgsProject.instance().addMapLayer(vl)
        self.tag(vl, "Training areas")
        self.t_layer.setLayer(vl)
        self.t_field.setField("class_id")
        vl.startEditing()
        self.iface.setActiveLayer(vl)
        self.iface.actionAddFeature().trigger()
        self.msg(tr("Draw polygons; type the class id in the form. Save edits when finished."), "ok")

    def get_worldcover(self):
        if self.aoi_geom is None:
            self.msg(tr("Set an AOI first."), "warn")
            self.go("aoi")
            return
        self._wc_wait = True
        self.run_script(self._dataset_script("worldcover"), switch=False)
        self.msg(tr("Downloading ESA WorldCover for the AOI…"))

    def wc_training(self):
        img, wc = self.s_raster.currentLayer(), self.wc_layer.currentLayer()
        if layer_info(img) is None or layer_info(wc) is None:
            self.msg(tr("Choose the image (above) and the WorldCover layer."), "warn")
            return
        out = os.path.join(self.out_dir(), "training_worldcover.gpkg")
        i = 1
        while os.path.exists(out):
            out = os.path.join(self.out_dir(), "training_worldcover_%d.gpkg" % i)
            i += 1
        auto7 = next(c for k, _, c in self.SCHEMES if k == "auto7")

        def done(ok, res, err):
            if not ok or not res:
                self.msg(err or tr("cancelled"), "err")
                return
            self.session["classes"] = [list(c) for c in auto7]
            self.scheme.setCurrentIndex(self.scheme.findData("auto7"))
            self.render_classes()
            vl = QgsVectorLayer(res["path"] + "|layername=training", self.name("Training points (WorldCover)"), "ogr")
            self.style_training(vl)
            QgsProject.instance().addMapLayer(vl)
            self.tag(vl, "Training points (WorldCover)")
            self.t_layer.setLayer(vl)
            self.t_field.setField("class_id")
            names = {c[0]: tr(c[1]) for c in auto7}
            self.msg(tr("%d training points: %s") % (res["n"], ", ".join(
                "%s %d" % (names.get(k, k), v) for k, v in sorted(res["counts"].items()))), "ok")
        self._task(tr("Training points from WorldCover"), procs.training_from_reference,
                  (layer_path(img), layer_path(wc), self.WC_TO_AUTO7, self.wc_n.value(), out), on_done=done)

    def style_training(self, vl):
        rio.set_vector_classes(vl, self.classes(), "class_id")

    def _training(self):
        lyr = self.s_raster.currentLayer()
        tl = self.t_layer.currentLayer()
        fld = self.t_field.currentField()
        bands = checked_data(self.s_bands)
        if lyr is None or tl is None or not fld or not bands:
            self.msg(tr("Choose the image, bands, training layer and class id field."), "warn")
            return None
        if tl.isEditable() and tl.isModified():
            tl.commitChanges()
            tl.startEditing()
        info = layer_info(lyr)
        if info is None:
            self.msg(tr("Choose an image file layer (not a web basemap)."), "warn")
            return None
        vec, ln = rio.export_vector_for_raster(tl, info)
        return lyr, bands, vec, ln, fld

    def run_signatures(self):
        t = self._training()
        if not t:
            return
        lyr, bands, vec, ln, fld = t
        names = {c[0]: (tr(c[1]), c[2]) for c in self.classes()}

        def done(ok, res, err):
            if not ok:
                self.msg(err, "err")
                return
            self.session["signatures"] = res
            series = [("%s (n=%d)" % (names.get(c, (str(c), None))[0], n), m, names.get(c, (None, None))[1], np.array(s))
                      for c, m, s, n in zip(res["classes"], res["means"], res["stds"], res["counts"])]
            plots.plot_signatures(tr("Training signatures (mean ± 1 std)"), res["bands"], series, self)
            rows = []
            cl = res["classes"]
            for i in range(len(cl)):
                for j in range(i + 1, len(cl)):
                    rows.append(["%s — %s" % (names.get(cl[i], (cl[i],))[0], names.get(cl[j], (cl[j],))[0]),
                                 float(res["jm"][i][j])])
            rows.sort(key=lambda r: r[1])
            fill(self.jm_table, rows)
            poor = [r[0] for r in rows if r[1] < 1.0]
            self.msg(tr("Poorly separated pairs: %s") % ", ".join(poor) if poor else tr("All class pairs separable."),
                     "warn" if poor else "ok")
        self._task(tr("Training signatures"), procs.signatures, (layer_path(lyr), bands, vec, ln, fld), on_done=done)

    def feature_space(self):
        t = self._training()
        if not t:
            return
        lyr, _, vec, ln, fld = t
        bx, by = self.fs_x.currentData(), self.fs_y.currentData()
        names = {c[0]: (tr(c[1]), c[2]) for c in self.classes()}

        def job(task):
            X, y = procs.training_samples(layer_path(lyr), [bx, by], vec, ln, fld, 3000)
            return X, y

        def done(ok, res, err):
            if not ok:
                self.msg(err, "err")
                return
            X, y = res
            cl = sorted(set(y.tolist()))
            plots.plot_scatter(tr("Feature space"), [X[y == c, 0] for c in cl], [X[y == c, 1] for c in cl],
                               self.fs_x.currentText(), self.fs_y.currentText(),
                               [names.get(c, (str(c),))[0] for c in cl], [names.get(c, (0, None))[1] for c in cl], self)
        self._task(tr("Feature space"), job, on_done=done)

    def run_sup(self):
        t = self._training()
        if not t:
            return
        lyr, bands, vec, ln, fld = t
        k = self.clf.currentData()
        if k in ("rf", "svm", "knn") and not deps.has_module("sklearn"):
            self.msg(tr("scikit-learn is not installed — install it in 1 Data › Setup."), "warn")
            self.go("setup")
            return
        pv = self.clf_param.value()
        params = {"maxlik": {"threshold": pv}, "mindist": {"max_dist": pv or None}, "sam": {"max_angle": pv or None},
                  "para": {"n_std": pv or 2.0}, "rf": {"n_trees": int(pv) or 200}, "svm": {"C": pv or 10.0},
                  "knn": {"k": int(pv) or 5}}[k]

        lyr2 = self.s_raster2.currentLayer()
        path2 = layer_path(lyr2) if (lyr2 is not None and lyr2 is not lyr and layer_info(lyr2)) else None

        def done(ok, res, err):
            if not ok or not res:
                self.msg(err or tr("cancelled"), "err")
                return
            self.session["supervised"] = res
            self.render_sup()
            cl = self.classes()
            if res.get("path2"):
                n2 = " (%s) — %s" % (tr(res["classifier"]), lyr2.name())
                l3 = rio.add_class_layer(res["path2"], self.name("Classification", n2),
                                         list(cl) + [(0, "Unclassified", "#000000")], tr(GROUP))
                self.tag(l3, "Classification", " (%s) — %s" % (res["classifier"], lyr2.name()))
                n1 = " (%s) — %s" % (tr(res["classifier"]), lyr.name())
                d1, d2 = self._layer_date(lyr), self._layer_date(lyr2)
                first_is_1 = (d1 or "") <= (d2 or "")
                self._pending_change = (n1, l3, first_is_1)
            l2 = rio.add_class_layer(res["path"], self.name("Classification", " (%s)" % tr(res["classifier"])),
                                     list(cl) + [(0, "Unclassified", "#000000")], tr(GROUP))
            self.tag(l2, "Classification", " (%s)" % res["classifier"])
            self.a_class.setLayer(l2)
            self.v_raster.setLayer(l2)
            self.lo_layer.setLayer(l2)
            pend = getattr(self, "_pending_change", None)
            if pend and res.get("path2"):
                n1, l3, first_is_1 = pend
                l2.setName(self.name("Classification", n1))
                self.tag(l2, "Classification", " (%s) — %s" % (res["classifier"], lyr.name()))
                a, b = (l2, l3) if first_is_1 else (l3, l2)
                self.ch1.setLayer(a)
                self.ch2.setLayer(b)
                self.ch_mode.setCurrentIndex(self.ch_mode.findData("post"))
                self.msg(tr("Both dates classified with the same model — open 4 Change and press Detect change."), "ok")
            self._pending_change = None
            self.msg(tr("Classification finished."), "ok")
        self._task(tr("Classifying"), procs.supervised,
                  (layer_path(lyr), bands, vec, ln, fld, k, params, self.out_dir(), self.s_maj.value(),
                   path2), on_done=done)

    def render_sup(self):
        res = self.session.get("supervised")
        if not res:
            return
        names = {c[0]: tr(c[1]) for c in self.classes()}
        fill(self.s_areas, [[names.get(v, v), n, a, p] for v, n, a, p in res["areas"]], "%.2f")
        t = "%s: %s · %s: %d" % (tr("Classifier"), tr(res["classifier"]), tr("training pixels"), res["n_train"])
        t += " · %s: %.1f%%" % (tr("training accuracy (optimistic)"), 100 * res["train_oa"])
        if "oob" in res:
            t += " · OOB: %.1f%%" % (100 * res["oob"])
        if "importance" in res:
            imp = sorted(res["importance"], key=lambda x: -x[1])[:5]
            t += "<br>%s: %s" % (tr("Most important bands"), ", ".join("%s %.2f" % (b.split("|")[0], v) for b, v in imp))
        self.s_info.setText(t)

    def area_chart(self):
        res = self.session.get("supervised") or self.session.get("unsupervised")
        if not res:
            return
        names = {c[0]: (tr(c[1]), c[2]) for c in self.classes()}
        labs = [names.get(v, (str(v),))[0] for v, _, _, _ in res["areas"]]
        cols = [names.get(v, (0, "#888888"))[1] for v, _, _, _ in res["areas"]]
        plots.plot_bars(tr("Class area (ha)"), labs, [a for _, _, a, _ in res["areas"]], cols, "ha", self)

    # ============================================================ 7 ACCURACY
    def build_accuracy(self, P):
        lay = P.section("accuracy", tr("Accuracy assessment"), "accuracy").lay
        f = QFormLayout()
        self.a_class = raster_combo()
        self.a_class.setObjectName("a_class")
        f.addRow(tr("Classified map"), self.a_class)
        self.a_design = QComboBox()
        self.a_design.setObjectName("a_design")
        self.a_design.addItem(tr("Cochran total, proportional to class area (recommended)"), "prop")
        self.a_design.addItem(tr("Cochran total, equal per class"), "equal")
        self.a_design.addItem(tr("Fixed number per class"), "fixed")
        f.addRow(tr("Sample design"), self.a_design)
        self.a_p, self.a_e = QDoubleSpinBox(), QDoubleSpinBox()
        for w, n, v, lo, hi, st in ((self.a_p, "a_p", 0.85, 0.5, 0.99, 0.01), (self.a_e, "a_e", 0.05, 0.01, 0.2, 0.005)):
            w.setObjectName(n)
            w.setDecimals(3)
            w.setRange(lo, hi)
            w.setSingleStep(st)
            w.setValue(v)
        self.a_p.setToolTip(tr("Expected overall accuracy p"))
        self.a_e.setToolTip(tr("Margin of error e (± of the accuracy estimate)"))
        f.addRow(tr("Expected accuracy / margin"), row(self.a_p, self.a_e))
        self.a_min, self.a_n = QSpinBox(), QSpinBox()
        for w, n, v in ((self.a_min, "a_min", 20), (self.a_n, "a_n", 50)):
            w.setObjectName(n)
            w.setRange(0, 2000)
            w.setValue(v)
        self.a_min.setToolTip(tr("Minimum points per class (Olofsson et al. 2014 suggest 50–100 for validation)"))
        self.a_n.setToolTip(tr("Points per class (Fixed design)"))
        f.addRow(tr("Min. per class / fixed n"), row(self.a_min, self.a_n))
        self.a_ninfo = QLabel()
        self.a_ninfo.setStyleSheet("color:#356;font-size:11px")
        f.addRow(self.a_ninfo)
        for w in (self.a_p, self.a_e):
            w.valueChanged.connect(self.update_cochran)
        self.a_design.currentIndexChanged.connect(self.update_cochran)
        self.update_cochran()
        f.addRow(row(btn(tr("Generate validation points"), self.gen_points), "stretch"))
        self.a_ref = QgsMapLayerComboBox()
        self.a_ref.setObjectName("a_ref")
        set_layer_filters(self.a_ref, "polygon", "point")
        f.addRow(tr("Reference layer"), self.a_ref)
        self.a_field = QgsFieldComboBox()
        self.a_field.setObjectName("a_field")
        self.a_field.setFilters(field_filter_numeric())
        self.a_ref.layerChanged.connect(self.a_field.setLayer)
        self.a_field.setLayer(self.a_ref.currentLayer())
        f.addRow(tr("Reference class field"), self.a_field)
        lay.addLayout(f)
        # ---- labelling the points (from the GEE LULC Toolkit labelling dock)
        sub(lay, tr("Label the points"))
        self.lb_info = QLabel(tr("Choose the reference layer, then label each point by looking at high-resolution "
                                 "imagery (basemap button at the top)."))
        self.lb_info.setWordWrap(True)
        self.lb_info.setStyleSheet("font-size:11px")
        lay.addWidget(self.lb_info)
        lay.addLayout(row(btn("◀", lambda: self.lb_step(-1)), btn("▶", lambda: self.lb_step(1)),
                          btn(tr("Next unlabelled"), self.lb_next_empty), btn(tr("Clear label"), lambda: self.lb_assign(None))))
        self.lb_buttons = QWidget()
        self.lb_flow = FlowLayout(self.lb_buttons)
        lay.addWidget(self.lb_buttons)
        self.lb_hint = QCheckBox(tr("Show the map class (hint) — off for blind labelling"))
        self.lb_hint.setObjectName("lb_hint")
        lay.addWidget(self.lb_hint)
        self.lb_i, self.lb_marker = -1, None
        self.lb_build_buttons()
        self.a_ref.layerChanged.connect(lambda *_: self.lb_reset())
        self.a_field.fieldChanged.connect(lambda *_: self.lb_refresh(zoom=False))
        lay.addLayout(row(btn(tr("Compute accuracy"), self.run_accuracy, primary=True), btn(tr("Export CSV"), self.export_cm)))
        self.a_metrics = QLabel()
        self.a_metrics.setWordWrap(True)
        lay.addWidget(self.a_metrics)
        self.a_cm = table([""], 120, hidden=True)
        lay.addWidget(self.a_cm)
        self.a_area_lab = QLabel()
        self.a_area_lab.setWordWrap(True)
        self.a_area_lab.setVisible(False)
        lay.addWidget(self.a_area_lab)
        self.a_area = table([tr("Class"), tr("Mapped (ha)"), tr("Estimated (ha)"), tr("± 95% CI (ha)"),
                             tr("Producer's acc.")], 110, hidden=True)
        self.a_area.setToolTip(tr("Area-adjusted estimates (Olofsson et al. 2014): valid when the validation points are "
                                  "a stratified random sample by map class, as made by 'Generate validation points'."))
        lay.addWidget(self.a_area)

    def gen_points(self):
        lyr = self.a_class.currentLayer()
        if lyr is None:
            return
        out = os.path.join(self.out_dir(), "validation_points.gpkg")
        i = 1
        while os.path.exists(out):
            out = os.path.join(self.out_dir(), "validation_points_%d.gpkg" % i)
            i += 1

        def done(ok, res, err):
            if not ok:
                self.msg(err, "err")
                return
            vl = QgsVectorLayer(res["path"] + "|layername=validation_points", self.name("Validation points"), "ogr")
            QgsProject.instance().addMapLayer(vl)
            self.tag(vl, "Validation points")
            self.a_ref.setLayer(vl)
            self.a_field.setField("ref_class")
            self.msg(tr("%d points created. Label each point below (class buttons), then compute.") % res["n"], "ok")
            self.lb_reset()
            self.lb_next_empty()
        n_total = procs.cochran_n(self.a_p.value(), self.a_e.value())
        self._task(tr("Sampling points"), procs.sample_points_design,
                  (layer_path(lyr), self.a_design.currentData(), self.a_n.value(), n_total, self.a_min.value(), out),
                  on_done=done)

    def update_cochran(self, *_):
        d = self.a_design.currentData()
        n = procs.cochran_n(self.a_p.value(), self.a_e.value())
        self.a_p.setEnabled(d != "fixed")
        self.a_e.setEnabled(d != "fixed")
        self.a_min.setEnabled(d == "prop")
        self.a_n.setEnabled(d == "fixed")
        self.a_ninfo.setText(tr("Cochran n = z²·p·(1−p)/e² + 10%% = %d points") % n if d != "fixed" else "")

    # ---------------- point labelling
    def _lb_layer(self):
        vl = self.a_ref.currentLayer()
        fld = self.a_field.currentField()
        if vl is None or not fld or vl.fields().indexOf(fld) < 0:
            return None, None
        return vl, fld

    def lb_reset(self):
        self.lb_i = -1
        self._lb_fids = []
        vl, _ = self._lb_layer()
        if vl is not None:
            self._lb_fids = sorted(f.id() for f in vl.getFeatures())
        self.lb_build_buttons()
        self.lb_refresh(zoom=False)

    def lb_build_buttons(self):
        while self.lb_flow.count():
            it = self.lb_flow.takeAt(0)
            if it is not None and it.widget() is not None:
                it.widget().deleteLater()
        for cid, n, col in self.classes():
            b = QPushButton("%d %s" % (cid, tr(n)))
            dark = QColor(col).lightness() < 140
            b.setStyleSheet("QPushButton{background:%s;color:%s;border:1px solid #888;border-radius:4px;padding:3px 7px}"
                            % (col, "white" if dark else "black"))
            b.clicked.connect(lambda _=False, c=cid: self.lb_assign(c))
            self.lb_flow.addWidget(b)

    def lb_step(self, d):
        if not getattr(self, "_lb_fids", None):
            self.lb_reset()
        if not self._lb_fids:
            return
        self.lb_i = (self.lb_i + d) % len(self._lb_fids)
        self.lb_refresh()

    def lb_next_empty(self):
        vl, fld = self._lb_layer()
        if vl is None:
            self.msg(tr("Choose the reference layer and field first."), "warn")
            return
        if not getattr(self, "_lb_fids", None):
            self.lb_reset()
        n = len(self._lb_fids)
        for k in range(1, n + 1):
            i = (self.lb_i + k) % n
            v = vl.getFeature(self._lb_fids[i])[fld]
            if v is None or str(v).strip() in ("", "0", "NULL"):
                self.lb_i = i
                self.lb_refresh()
                return
        self.msg(tr("All points are labelled — compute the accuracy."), "ok")

    def lb_assign(self, cid):
        vl, fld = self._lb_layer()
        if vl is None or self.lb_i < 0 or not self._lb_fids:
            return
        fid = self._lb_fids[self.lb_i]
        idx = vl.fields().indexOf(fld)
        if vl.isEditable():
            vl.changeAttributeValue(fid, idx, cid)
        else:
            vl.dataProvider().changeAttributeValues({fid: {idx: cid}})
            vl.reload()
        vl.triggerRepaint()
        if cid is not None:
            self.lb_next_empty()
        else:
            self.lb_refresh(zoom=False)

    def lb_refresh(self, zoom=True):
        vl, fld = self._lb_layer()
        if vl is None or self.lb_i < 0 or not getattr(self, "_lb_fids", None):
            if vl is not None and getattr(self, "_lb_fids", None):
                done_n = sum(1 for f in vl.getFeatures() if f[fld] not in (None, 0, "") and str(f[fld]) != "NULL")
                self.lb_info.setText(tr("%d of %d points labelled.") % (done_n, len(self._lb_fids)))
            return
        f = vl.getFeature(self._lb_fids[self.lb_i])
        names = {c[0]: tr(c[1]) for c in self.classes()}
        ref = f[fld]
        done_n = sum(1 for g in vl.getFeatures() if g[fld] not in (None, 0, "") and str(g[fld]) != "NULL")
        txt = "%s %d / %d · %s: <b>%s</b>" % (tr("Point"), self.lb_i + 1, len(self._lb_fids), tr("label"),
                                             names.get(ref, ref) if ref not in (None, 0, "") and str(ref) != "NULL"
                                             else "—")
        if self.lb_hint.isChecked() and vl.fields().indexOf("map_class") >= 0:
            txt += " · %s: %s" % (tr("map"), names.get(f["map_class"], f["map_class"]))
        txt += " · %s %d/%d" % (tr("done"), done_n, len(self._lb_fids))
        self.lb_info.setText(txt)
        if zoom and f.hasGeometry():
            from qgis.gui import QgsVertexMarker
            pt = QgsCoordinateTransform(vl.crs(), self.canvas.mapSettings().destinationCrs(),
                                        QgsProject.instance()).transform(f.geometry().centroid().asPoint())
            if self.lb_marker is None:
                self.lb_marker = QgsVertexMarker(self.canvas)
                self.lb_marker.setColor(QColor("#ff00ff"))
                self.lb_marker.setIconSize(22)
                self.lb_marker.setPenWidth(3)
                self.lb_marker.setIconType(E(QgsVertexMarker, "IconType.ICON_CIRCLE"))
            self.lb_marker.setCenter(pt)
            self.canvas.setCenter(pt)
            if self.canvas.scale() > 6000:
                self.canvas.zoomScale(3000)
            self.canvas.refresh()

    def run_accuracy(self):
        lyr, ref, fld = self.a_class.currentLayer(), self.a_ref.currentLayer(), self.a_field.currentField()
        if lyr is None or ref is None or not fld:
            self.msg(tr("Choose the classified map, reference layer and field."), "warn")
            return
        if ref.isEditable() and ref.isModified():
            ref.commitChanges()
        info = layer_info(lyr)
        if info is None:
            self.msg(tr("Choose an image file layer (not a web basemap)."), "warn")
            return
        vec, ln = rio.export_vector_for_raster(ref, info)

        def done(ok, res, err):
            if not ok or not res:
                self.msg(err or tr("cancelled"), "err")
                return
            d = res.get("diag", {})
            if res.get("problem"):
                why = {"empty": tr("The reference layer has no features."),
                       "no_value": tr("The field '%s' is empty for all %d reference features. Open the attribute "
                                      "table, type the true class id of each point (from high-resolution imagery "
                                      "or the field), save the edits, then compute again.") % (fld, d.get("features", 0)),
                       "outside": tr("None of the reference points fall inside the classified map. Check that both "
                                     "cover the same area."),
                       "unclassified": tr("The reference points only fall on unclassified pixels (value 0) of the map.")
                       }[res["problem"]]
                self.a_metrics.setText("<span style='color:#c0392b'>%s</span>" % why)
                self.msg(why, "warn")
                if res["problem"] == "no_value":
                    self.iface.showAttributeTable(ref)
                return
            self.session["accuracy"] = res
            self.render_accuracy()
            extra = ""
            if d.get("no_value"):
                extra = " — " + tr("%d point(s) without a reference value were skipped") % d["no_value"]
            self.msg(tr("Overall accuracy %.1f%%, kappa %.3f (n = %d)") % (100 * res["oa"], res["kappa"], res["n"]) + extra,
                     "ok")
        self._task(tr("Accuracy assessment"), procs.accuracy, (layer_path(lyr), vec, ln, fld), on_done=done)

    def render_accuracy(self):
        acc = self.session.get("accuracy")
        if not acc:
            return
        names = {c[0]: tr(c[1]) for c in self.classes()}
        labs = [str(names.get(v, v)) for v in acc["labels"]]
        hdr = [tr("Map \\ Ref")] + labs + [tr("Total"), tr("User's acc.")]
        self.a_cm.setColumnCount(len(hdr))
        self.a_cm.setHorizontalHeaderLabels(hdr)
        rows = []
        for i, r in enumerate(acc["cm"]):
            rows.append([labs[i]] + [str(x) for x in r] + [str(sum(r)), float(acc["ua"][i])])
        cs = [sum(r[j] for r in acc["cm"]) for j in range(len(labs))]
        rows.append([tr("Total")] + [str(x) for x in cs] + [str(sum(cs)), ""])
        rows.append([tr("Producer's acc.")] + [float(x) for x in acc["pa"]] + ["", ""])
        fill(self.a_cm, rows)
        for i in range(len(labs)):
            it = self.a_cm.item(i, i + 1)
            if it:
                it.setBackground(QColor("#cfe8cf"))
        self.a_metrics.setText("<b>%s:</b> %.2f%% &nbsp; <b>Kappa:</b> %.3f &nbsp; <b>n</b> = %d<br><small>%s</small>" % (
            tr("Overall accuracy"), 100 * acc["oa"], acc["kappa"], acc["n"],
            tr("Rows = classified map, columns = reference. User's = 1 − commission, Producer's = 1 − omission.")))
        adj = acc.get("adjusted")
        if adj:
            self.a_area_lab.setVisible(True)
            self.a_area_lab.setText("<b>%s</b> (Olofsson et al. 2014)<br>%s: <b>%.2f%%</b> ± %.2f%%" % (
                tr("Area-adjusted estimates"), tr("Area-weighted overall accuracy"), 100 * adj["oa"],
                100 * adj["oa_ci"]))
            fill(self.a_area, [[labs[i], adj["mapped_ha"][i], adj["area_ha"][i], adj["ci_ha"][i], adj["pa"][i]]
                               for i in range(len(labs))], "%.2f")

    def export_cm(self):
        acc = self.session.get("accuracy")
        if not acc:
            return
        fn, _ = QFileDialog.getSaveFileName(self, tr("Export CSV"), os.path.join(self.out_dir(), "accuracy.csv"),
                                            "CSV (*.csv)")
        if not fn:
            return
        names = {c[0]: c[1] for c in self.classes()}
        labs = [names.get(v, v) for v in acc["labels"]]
        with open(fn, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["map\\ref"] + labs + ["total", "users_accuracy"])
            for i, r in enumerate(acc["cm"]):
                w.writerow([labs[i]] + r + [sum(r), acc["ua"][i]])
            w.writerow(["producers_accuracy"] + acc["pa"])
            w.writerow(["f1"] + acc["f1"])
            w.writerow(["overall_accuracy", acc["oa"]])
            w.writerow(["kappa", acc["kappa"]])
            adj = acc.get("adjusted")
            if adj:
                w.writerow([])
                w.writerow(["area_adjusted (Olofsson et al. 2014)"])
                w.writerow(["class", "mapped_ha", "estimated_ha", "ci95_ha", "producers_accuracy_adj"])
                for i, lab in enumerate(labs):
                    w.writerow([lab, adj["mapped_ha"][i], adj["area_ha"][i], adj["ci_ha"][i], adj["pa"][i]])
                w.writerow(["overall_accuracy_area_weighted", adj["oa"], "ci95", adj["oa_ci"]])
        self.msg(tr("Saved %s") % fn, "ok")

    # ============================================================ clean-up & vectorize (classified maps)
    def build_vector(self, P):
        lay = P.section("vector", tr("Clean up & export polygons")).lay
        lay.addWidget(QLabel("<small>%s</small>" % tr(
            "Sieve removes patches smaller than the minimum mapping unit (they take the class of their "
            "largest neighbour); polygons get class id, class name and area in ha — ready for GIS or a report.")))
        f = QFormLayout()
        lay.addLayout(f)
        self.v_raster = raster_combo()
        self.v_raster.setObjectName("v_raster")
        f.addRow(tr("Classified map"), self.v_raster)
        self.v_mmu = QDoubleSpinBox()
        self.v_mmu.setObjectName("v_mmu")
        self.v_mmu.setRange(0, 10000)
        self.v_mmu.setDecimals(2)
        self.v_mmu.setValue(0.5)
        self.v_mmu.setSuffix(" ha")
        self.v_mmu.setToolTip(tr("0 = no sieve"))
        f.addRow(tr("Minimum mapping unit"), self.v_mmu)
        self.v_eight = QCheckBox(tr("Diagonal pixels are connected (8-connectedness)"))
        self.v_eight.setObjectName("v_eight")
        self.v_eight.setChecked(True)
        f.addRow(self.v_eight)
        self.v_poly = QCheckBox(tr("Also export polygons (GeoPackage)"))
        self.v_poly.setObjectName("v_poly")
        self.v_poly.setChecked(True)
        f.addRow(self.v_poly)
        lay.addLayout(row(btn(tr("Run"), self.run_vector, primary=True), "stretch"))
        self.v_info = QLabel()
        self.v_info.setWordWrap(True)
        lay.addWidget(self.v_info)

    def run_vector(self):
        lyr = self.v_raster.currentLayer()
        info = layer_info(lyr)
        if info is None:
            self.msg(tr("Choose an image file layer (not a web basemap)."), "warn")
            return
        cl = self.classes()
        names = {c[0]: c[1] for c in cl}

        def done(ok, res, err):
            if not ok or not res:
                self.msg(err or tr("cancelled"), "err")
                return
            legend = list(cl) + [(0, "Unclassified", "#000000")]
            sfx = " (%.2f ha)" % self.v_mmu.value()
            l2 = rio.add_class_layer(res["path"], self.name("Sieved map", sfx), legend, tr(GROUP))
            self.tag(l2, "Sieved map", sfx)
            txt = tr("Sieve: %d pixels (min. %d px) changed.") % (res["changed"], res["min_px"])
            if res.get("gpkg"):
                vl = QgsVectorLayer(res["gpkg"] + "|layername=classes", self.name("Class polygons"), "ogr")
                rio.set_vector_classes(vl, cl, "class_id")
                QgsProject.instance().addMapLayer(vl)
                self.tag(vl, "Class polygons")
                txt += " " + tr("%d polygons saved to %s") % (res["n_poly"], res["gpkg"])
            self.v_info.setText(txt)
            self.msg(txt, "ok")
        self._task(tr("Clean up & export polygons"), procs.sieve_vectorize,
                  (layer_path(lyr), self.v_mmu.value(), self.v_eight.isChecked(), self.v_poly.isChecked(),
                   self.out_dir(), names), on_done=done)

    # ============================================================ map layout (from the GEE LULC Toolkit)
    def build_layout(self, P):
        lay = P.section("layout", "🖨 " + tr("Map layout (print)")).lay
        lay.addWidget(QLabel("<small>%s</small>" % tr(
            "A4 map with title, legend, scale bar, north arrow, data sources and accuracy — opens in the QGIS "
            "layout designer for final touches and PDF/PNG export.")))
        f = QFormLayout()
        lay.addLayout(f)
        self.lo_layer = QgsMapLayerComboBox()
        self.lo_layer.setObjectName("lo_layer")
        self.lo_layer.setAllowEmptyLayer(True)
        f.addRow(tr("Main layer (legend)"), self.lo_layer)
        self.lo_title = QLineEdit()
        self.lo_title.setObjectName("lo_title")
        self.lo_title.setPlaceholderText(tr("auto"))
        f.addRow(tr("Title"), self.lo_title)
        self.lo_orient = QComboBox()
        self.lo_orient.setObjectName("lo_orient")
        self.lo_orient.addItem(tr("Landscape"), "L")
        self.lo_orient.addItem(tr("Portrait"), "P")
        f.addRow(tr("Page"), self.lo_orient)
        self.lo_acc = QCheckBox(tr("Add the accuracy line"))
        self.lo_acc.setObjectName("lo_acc")
        self.lo_acc.setChecked(True)
        f.addRow(self.lo_acc)
        lay.addLayout(row(btn(tr("Create map layout"), self.make_layout, primary=True), "stretch"))

    def make_layout(self):
        from qgis.core import (Qgis, QgsApplication, QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap,
                               QgsLayoutItemPicture, QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize,
                               QgsPrintLayout, QgsRectangle, QgsUnitTypes)
        from qgis.PyQt.QtGui import QFont
        try:
            MM = Qgis.LayoutUnit.Millimeters
        except AttributeError:
            MM = QgsUnitTypes.LayoutMillimeters
        try:
            KM = Qgis.DistanceUnit.Kilometers
        except AttributeError:
            KM = QgsUnitTypes.DistanceKilometers
        proj = QgsProject.instance()
        lyr = self.lo_layer.currentLayer()
        title = self.lo_title.text().strip() or (lyr.name() if lyr else tr("Map"))
        aoi = self.session.get("aoi_label", "")
        if aoi and aoi not in title:
            title += " — " + aoi
        mgr = proj.layoutManager()
        name = "LEE RS Lab — " + title
        old = mgr.layoutByName(name)
        if old is not None:
            mgr.removeLayout(old)
        L = QgsPrintLayout(proj)
        L.initializeDefaults()                       # one A4 landscape page
        L.setName(name)
        portrait = self.lo_orient.currentData() == "P"
        if portrait:
            from qgis.core import QgsLayoutItemPage
            L.pageCollection().page(0).setPageSize("A4", E(QgsLayoutItemPage, "Orientation.Portrait"))
        W, H = (210.0, 297.0) if portrait else (297.0, 210.0)
        mgr.addLayout(L)
        m = 8.0
        side = 0.0 if portrait else 72.0             # right column for legend + notes (landscape)
        foot = 62.0 if portrait else 22.0
        mw, mh = W - 2 * m - side, H - 26 - foot
        # title
        t = QgsLayoutItemLabel(L)
        t.setText(title)
        label_font(t, "Arial", 16, True)
        L.addLayoutItem(t)
        t.attemptMove(QgsLayoutPoint(m, 6, MM))
        t.attemptResize(QgsLayoutSize(W - 2 * m, 12, MM))
        # map
        mp = QgsLayoutItemMap(L)
        mp.setFrameEnabled(True)
        L.addLayoutItem(mp)
        mp.attemptMove(QgsLayoutPoint(m, 22, MM))
        mp.attemptResize(QgsLayoutSize(mw, mh, MM))
        crs = self.canvas.mapSettings().destinationCrs()
        mp.setCrs(crs)
        ext = None
        if self.aoi_geom is not None:
            g = QgsGeometry(self.aoi_geom)
            g.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs, proj))
            ext = g.boundingBox()
        elif lyr is not None:
            ext = QgsCoordinateTransform(lyr.crs(), crs, proj).transformBoundingBox(lyr.extent())
        if ext is None:
            ext = self.canvas.extent()
        ext = QgsRectangle(ext)
        ext.scale(1.05)
        mp.zoomToExtent(ext)
        # north arrow
        svg = ""
        for d in QgsApplication.svgPaths():
            for nm in ("arrows/NorthArrow_02.svg", "arrows/NorthArrow_04.svg", "arrows/NorthArrow_11.svg"):
                if os.path.exists(os.path.join(d, nm)):
                    svg = svg or os.path.join(d, nm)
        if svg:
            na = QgsLayoutItemPicture(L)
            na.setPicturePath(svg)
            L.addLayoutItem(na)
            na.attemptResize(QgsLayoutSize(12, 16, MM))
            na.attemptMove(QgsLayoutPoint(m + mw - 16, 26, MM))
        # scale bar
        sb = QgsLayoutItemScaleBar(L)
        sb.setStyle("Single Box")
        sb.setLinkedMap(mp)
        sb.setUnits(KM)
        sb.setUnitLabel("km")
        sb.setNumberOfSegments(4)
        sb.setNumberOfSegmentsLeft(0)
        sb.applyDefaultSize()
        L.addLayoutItem(sb)
        sb.attemptMove(QgsLayoutPoint(m, 22 + mh + 3, MM))
        # legend
        lg = QgsLayoutItemLegend(L)
        lg.setTitle(tr("Legend"))
        lg.setLinkedMap(mp)
        if lyr is not None:
            lg.setAutoUpdateModel(False)
            root = lg.model().rootGroup()
            root.clear()
            root.addLayer(lyr)
        L.addLayoutItem(lg)
        lg.attemptMove(QgsLayoutPoint(W - m - side + 4 if not portrait else m, 22 if not portrait else 22 + mh + 14, MM))
        # notes: sources, accuracy, CRS, date
        import datetime
        notes = []
        acc = self.session.get("accuracy")
        if self.lo_acc.isChecked() and acc:
            notes.append(tr("Overall accuracy %.1f%%, kappa %.3f (n = %d)") % (100 * acc["oa"], acc["kappa"], acc["n"]))
        notes.append("CRS: %s" % crs.authid())
        srcs = self.session.get("sources", [])
        if srcs:
            notes.append(tr("Data sources") + ": " + "; ".join(srcs[:4]))
        notes.append("LEE RS Lab · %s" % datetime.date.today().isoformat())
        nt = QgsLayoutItemLabel(L)
        nt.setText("\n".join(notes))
        label_font(nt, "Arial", 7)
        L.addLayoutItem(nt)
        if portrait:
            nt.attemptMove(QgsLayoutPoint(W / 2, 22 + mh + 14, MM))
            nt.attemptResize(QgsLayoutSize(W / 2 - m, 40, MM))
        else:
            nt.attemptMove(QgsLayoutPoint(W - m - side + 4, H - 60, MM))
            nt.attemptResize(QgsLayoutSize(side - 4, 52, MM))
        self.iface.openLayoutDesigner(L)
        self.msg(tr("Layout '%s' created.") % name, "ok")

    # ============================================================ 8 CHANGE
    def build_change(self, P):
        lay = P.section("change", tr("Change detection"), "change").lay
        self.ch_mode = QComboBox()
        self.ch_mode.setObjectName("ch_mode")
        self.ch_mode.addItem(tr("From → to: compare two classified maps"), "post")
        self.ch_mode.addItem(tr("Image differencing (index or band)"), "diff")
        lay.addWidget(self.ch_mode)
        f = QFormLayout()
        self.ch_form = f
        self.ch1, self.ch2 = raster_combo(), raster_combo()
        self.ch1.setObjectName("ch1")
        self.ch2.setObjectName("ch2")
        self.chb1, self.chb2 = self.band_combo("chb1"), self.band_combo("chb2")
        f.addRow(tr("Date 1 (before)"), self.ch1)
        f.addRow(tr("Band"), self.chb1)
        f.addRow(tr("Date 2 (after)"), self.ch2)
        f.addRow(tr("Band "), self.chb2)
        self.ch_k = QDoubleSpinBox()
        self.ch_k.setObjectName("ch_k")
        self.ch_k.setRange(0.1, 5)
        self.ch_k.setValue(1.5)
        f.addRow(tr("Threshold (mean ± k·std)"), self.ch_k)
        lay.addLayout(f)
        lay.addLayout(row(btn(tr("Detect change"), self.run_change, primary=True), "stretch"))
        self.ch_info = QLabel()
        self.ch_info.setWordWrap(True)
        lay.addWidget(self.ch_info)
        self.ch_table = table([""], 120, hidden=True)
        self.ch_table.setToolTip(tr("Rows = date 1 class, columns = date 2 class (ha). Diagonal = no change."))
        lay.addWidget(self.ch_table)
        # ---- from → to report (post-classification only)
        self.ch_post = QWidget()
        pl = QVBoxLayout(self.ch_post)
        pl.setContentsMargins(0, 0, 0, 0)
        sub(pl, tr("Gains and losses per class"))
        self.ch_gl = table([tr("Class"), tr("Date 1 (ha)"), tr("Date 2 (ha)"), tr("Gain"), tr("Loss"), tr("Net")],
                           110, hidden=True)
        pl.addWidget(self.ch_gl)
        sub(pl, tr("Largest changes (from → to)"))
        self.ch_tr = table([tr("From"), tr("To"), tr("Area (ha)"), tr("% of changed")], 110, hidden=True)
        pl.addWidget(self.ch_tr)
        pl.addLayout(row(btn("🌊 " + tr("Sankey diagram"), self.open_sankey, primary=True),
                         btn(tr("Area: date 1 vs date 2"), self.chart_change_areas),
                         btn(tr("Gains / losses chart"), self.chart_change), btn(tr("Export CSV"), self.export_change)))
        self.ch_sk_only = QCheckBox(tr("Sankey: changed areas only (hide 'no change')"))
        self.ch_sk_only.setObjectName("ch_sk_only")
        pl.addWidget(self.ch_sk_only)
        sub(pl, tr("Map one transition"))
        self.ch_from, self.ch_to = QComboBox(), QComboBox()
        pl.addLayout(row(self.ch_from, QLabel("→"), self.ch_to))
        pl.addLayout(row(btn(tr("Map this change"), self.map_transition, primary=True), "stretch"))
        self.ch_post.setVisible(False)
        lay.addWidget(self.ch_post)
        self.ch1.layerChanged.connect(lambda l: self.fill_bands(l, [self.chb1]))
        self.ch2.layerChanged.connect(lambda l: self.fill_bands(l, [self.chb2]))
        self.ch_mode.currentIndexChanged.connect(self.on_ch_mode)
        self.on_ch_mode()

    def on_ch_mode(self):
        diff = self.ch_mode.currentData() == "diff"
        for w in (self.chb1, self.chb2, self.ch_k):
            w.setVisible(diff)
            lab = self.ch_form.labelForField(w)
            if lab is not None:
                lab.setVisible(diff)
        for w, t in ((self.ch1, ("Date 1 (before)", "Classified map — date 1")),
                     (self.ch2, ("Date 2 (after)", "Classified map — date 2"))):
            lab = self.ch_form.labelForField(w)
            if lab is not None:
                lab.setText(tr(t[0] if diff else t[1]))
        self.ch_post.setVisible(not diff and bool(self.session.get("change", {}).get("cm")))

    def render_change(self):
        ch = self.session.get("change") or {}
        if not ch.get("cm"):
            return
        names = {c[0]: tr(c[1]) for c in self.classes()}
        labs, cm, pa = ch["labels"], np.array(ch["cm"], "float64"), ch["pixel_area"] / 1e4
        ha = cm * pa
        lab = [str(names.get(v, v)) for v in labs]
        self.ch_table.setColumnCount(len(labs) + 1)
        self.ch_table.setHorizontalHeaderLabels([tr("From \\ To (ha)")] + lab)
        fill(self.ch_table, [[lab[i]] + [float(x) for x in r] for i, r in enumerate(ha)], "%.1f")
        d1, d2, same = ha.sum(1), ha.sum(0), np.diag(ha)
        fill(self.ch_gl, [[lab[i], float(d1[i]), float(d2[i]), float(d2[i] - same[i]), float(d1[i] - same[i]),
                           float(d2[i] - d1[i])] for i in range(len(labs))], "%.1f")
        tot = float(ha.sum() - same.sum()) or 1.0
        trs = sorted(((ha[i, j], i, j) for i in range(len(labs)) for j in range(len(labs)) if i != j and ha[i, j] > 0),
                     reverse=True)
        fill(self.ch_tr, [[lab[i], lab[j], float(a), 100.0 * a / tot] for a, i, j in trs[:15]], "%.1f")
        for cb in (self.ch_from, self.ch_to):
            cur = cb.currentData()
            cb.clear()
            cb.addItem(tr("any class"), None)
            for v, n in zip(labs, lab):
                cb.addItem("%s  %s" % (v, n), v)
            if cur is not None and cb.findData(cur) >= 0:
                cb.setCurrentIndex(cb.findData(cur))
        if trs and self.ch_from.currentData() is None and self.ch_to.currentData() is None:
            self.ch_from.setCurrentIndex(self.ch_from.findData(labs[trs[0][1]]))
            self.ch_to.setCurrentIndex(self.ch_to.findData(labs[trs[0][2]]))
        self.ch_post.setVisible(self.ch_mode.currentData() == "post")

    def _change_dates(self):
        ch = self.session.get("change") or {}
        out = []
        for k, dflt in (("name1", "Date 1"), ("name2", "Date 2")):
            m = re.search(r"(19|20)\d\d", ch.get(k, "") or "")
            out.append(m.group(0) if m else tr(dflt))
        if out[0] == out[1]:
            out = [tr("Date 1"), tr("Date 2")]
        return out

    def _change_ok(self, need="cm"):
        """The last from → to result, or None when it is missing or was made from raw images (codes ≥ 1000)."""
        ch = self.session.get("change") or {}
        if not ch.get(need):
            self.msg(tr("Run Detect change first."), "warn")
            return None
        labs = [int(v) for v in ch.get("labels") or []]
        if labs and (max(labs) >= 1000 or len(labs) > 255):
            self.session.pop("change", None)
            self.msg(tr("This change result was made from two images, not two classified maps, so it has no "
                        "from → to classes. Classify both dates, then press Detect change again."), "warn")
            return None
        return ch

    def open_sankey(self):
        """Sankey diagram of the from → to flows (builder from the GEE LULC Toolkit; works offline)."""
        ch = self._change_ok("cm")
        if ch is None:
            return
        from . import sankey
        cls = {c[0]: (tr(c[1]), c[2]) for c in self.classes()}
        defs = [(v, cls.get(v, (str(v),))[0], cls.get(v, (0, rio.PALETTE[i % len(rio.PALETTE)]))[1])
                for i, v in enumerate(ch["labels"])]
        ha = np.array(ch["cm"], "float64") * ch["pixel_area"] / 1e4
        if self.ch_sk_only.isChecked():
            np.fill_diagonal(ha, 0.0)
        tot, chg = float(np.array(ch["cm"]).sum() * ch["pixel_area"] / 1e4), float(ha.sum() - np.trace(ha))
        d1, d2 = self._change_dates()
        aoi = self.session.get("aoi_label", "")
        path = os.path.join(self.out_dir(), "sankey_%s_%s.html" % (re.sub(r"\W+", "_", d1), re.sub(r"\W+", "_", d2)))
        sankey.write_sankey_html(ha.tolist(), defs, d1, d2, path=path,
                                 title="%s %s → %s%s" % (tr("Land-cover change"), d1, d2, (" — " + aoi) if aoi else ""),
                                 subtitle=tr("%s ha total · %s ha changed (%.1f%%)") % (
                                     "{:,.0f}".format(tot), "{:,.0f}".format(chg), 100.0 * chg / (tot or 1)),
                                 open_browser=False)
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.msg(tr("Saved %s") % path, "ok")

    def chart_change_areas(self):
        ch = self._change_ok("cm")
        if ch is None:
            return
        names = {c[0]: tr(c[1]) for c in self.classes()}
        ha = np.array(ch["cm"], "float64") * ch["pixel_area"] / 1e4
        d1, d2 = self._change_dates()
        plots.plot_grouped(tr("Class area (ha)"), [names.get(v, str(v)) for v in ch["labels"]],
                           ha.sum(1).tolist(), ha.sum(0).tolist(), d1, d2, "ha", self)

    def chart_change(self):
        ch = self._change_ok("cm")
        if ch is None:
            return
        names = {c[0]: (tr(c[1]), c[2]) for c in self.classes()}
        ha = np.array(ch["cm"], "float64") * ch["pixel_area"] / 1e4
        net = ha.sum(0) - ha.sum(1)
        labs = [names.get(v, (str(v),))[0] for v in ch["labels"]]
        cols = ["#1a9641" if x >= 0 else "#d7191c" for x in net]
        plots.plot_bars(tr("Net change per class (ha)"), labs, [float(x) for x in net], cols, "ha", self)

    def export_change(self):
        ch = self._change_ok("cm")
        if ch is None:
            return
        fn, _ = QFileDialog.getSaveFileName(self, tr("Export CSV"), os.path.join(self.out_dir(), "change_from_to.csv"),
                                            "CSV (*.csv)")
        if not fn:
            return
        names = {c[0]: c[1] for c in self.classes()}
        labs = [str(names.get(v, v)) for v in ch["labels"]]
        ha = np.array(ch["cm"], "float64") * ch["pixel_area"] / 1e4
        with open(fn, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["from\\to (ha)"] + labs + ["total_date1"])
            for i, r in enumerate(ha):
                w.writerow([labs[i]] + ["%.3f" % x for x in r] + ["%.3f" % r.sum()])
            w.writerow(["total_date2"] + ["%.3f" % x for x in ha.sum(0)] + ["%.3f" % ha.sum()])
            w.writerow([])
            w.writerow(["class", "date1_ha", "date2_ha", "gain_ha", "loss_ha", "net_ha"])
            for i, lab in enumerate(labs):
                d = ha[i, i]
                w.writerow([lab, ha[i].sum(), ha[:, i].sum(), ha[:, i].sum() - d, ha[i].sum() - d,
                            ha[:, i].sum() - ha[i].sum()])
        self.msg(tr("Saved %s") % fn, "ok")

    def map_transition(self):
        ch = self._change_ok("path")
        if ch is None:
            return
        a, b = self.ch_from.currentData(), self.ch_to.currentData()
        from osgeo import gdal
        info = rio.RasterInfo(ch["path"])
        code = np.nan_to_num(rio.read_band(ch["path"], 1)).astype("int64")
        fr, to = code // 1000, code % 1000
        m = (code > 0) & (fr != to)
        if a is not None:
            m &= fr == a
        if b is not None:
            m &= to == b
        names = {c[0]: c[1] for c in self.classes()}
        tag = "%s → %s" % (names.get(a, "any") if a is not None else "any", names.get(b, "any") if b is not None else "any")
        out = os.path.join(self.out_dir(), "transition_%s_%s.tif" % (a if a is not None else "any",
                                                                      b if b is not None else "any"))
        rio.write_array(out, m.astype("uint8"), info, gdal.GDT_Byte, 0)
        col = next((c[2] for c in self.classes() if c[0] == b), "#e31a1c")
        lyr = rio.add_class_layer(out, self.name("Change", " " + tag), [(1, "%s → %s", col,
                                  [names.get(a, "any class") if a is not None else "any class",
                                   names.get(b, "any class") if b is not None else "any class"])], tr(GROUP))
        self.tag(lyr, "Change", " " + tag)
        self.msg(tr("%s: %.1f ha") % (tag, m.sum() * info.pixel_area / 1e4), "ok")

    @staticmethod
    def _layer_date(lyr):
        """YYYY-MM-DD from RSLAB_START metadata or the layer/file name, for ordering dates."""
        info = layer_info(lyr)
        if info is not None and info.meta.get("RSLAB_START"):
            return info.meta["RSLAB_START"]
        m = re.search(r"(19|20)\d{2}-?\d{2}-?\d{2}", (lyr.name() if lyr else "") + " " + (layer_path(lyr) or ""))
        return m.group(0).replace("-", "") if m else None

    @staticmethod
    def _is_class_map(lyr):
        """A classified map: one band, whole numbers, few distinct values (checked on a thumbnail)."""
        info = layer_info(lyr)
        if info is None or info.count != 1:
            return False
        from osgeo import gdal
        ds = gdal.Open(layer_path(lyr))
        b = ds.GetRasterBand(1)
        w = min(400, ds.RasterXSize)
        h = max(1, int(ds.RasterYSize * w / max(1, ds.RasterXSize)))
        a = b.ReadAsArray(0, 0, ds.RasterXSize, ds.RasterYSize, w, h).astype("float64")
        nd = b.GetNoDataValue()
        a = a[np.isfinite(a)]
        if nd is not None:
            a = a[a != nd]
        if a.size == 0:
            return False
        return bool(np.all(a == np.round(a)) and np.unique(a).size <= 255 and a.max() < 1000)

    def run_change(self):
        l1, l2 = self.ch1.currentLayer(), self.ch2.currentLayer()
        if l1 is None or l2 is None:
            self.msg(tr("Choose two images."), "warn")
            return
        mode = self.ch_mode.currentData()
        if mode == "post":
            bad = [n for n, l in ((tr("Date 1"), l1), (tr("Date 2"), l2)) if not self._is_class_map(l)]
            if bad:
                why = tr("%s is an image, not a classified map. From → to needs two classified maps: classify each "
                         "date first (3 Classify › Supervised — classify › 'Also classify another date'), or choose "
                         "Image differencing for an index or band.") % " & ".join(bad)
                self.ch_info.setText("<span style='color:#c0392b'>%s</span>" % why)
                self.msg(why, "warn")
                self.session.pop("change", None)
                return
        names = {c[0]: tr(c[1]) for c in self.classes()}
        keys = {c[0]: c[1] for c in self.classes()}
        if mode == "diff":
            def done(ok, res, err):
                if not ok:
                    self.msg(err, "err")
                    return
                self.tag(rio.add_single_layer(res["diff"], self.name("Difference"), "RdBu", None, None, tr(GROUP)),
                         "Difference")
                cl = [(1, "Decrease", "#d7191c"), (2, "No change", "#f0f0f0"), (3, "Increase", "#1a9641")]
                self.tag(rio.add_class_layer(res["cls"], self.name("Change"), cl, tr(GROUP)), "Change")
                self.ch_post.setVisible(False)
                lab = {1: tr("Decrease"), 2: tr("No change"), 3: tr("Increase")}
                self.ch_table.setColumnCount(4)
                self.ch_table.setHorizontalHeaderLabels([tr("Class"), tr("Pixels"), tr("Area (ha)"), "%"])
                fill(self.ch_table, [[lab.get(v, v), n, a, p] for v, n, a, p in res["areas"]], "%.2f")
                s = tr("Thresholds: decrease < %.4f, increase > %.4f") % (res["lo"], res["hi"])
                self.ch_info.setText(s)
                self.session["change"] = {"summary": s + "; " + "; ".join("%s %.1f ha" % (lab.get(v, v), a)
                                                                          for v, _, a, _ in res["areas"])}
                self.msg(tr("Change map created."), "ok")
            self._task(tr("Change detection"), procs.change_difference,
                      (layer_path(l1), self.chb1.currentData() or 1, layer_path(l2), self.chb2.currentData() or 1,
                       self.ch_k.value(), self.out_dir()), on_done=done)
        else:
            def done(ok, res, err):
                if not ok:
                    self.msg(err, "err")
                    return
                cl = []
                rnd = random.Random(1)
                for code in res["codes"]:
                    a, b = divmod(code, 1000)
                    col = "#e8e8e8" if a == b else "#%06x" % rnd.randint(0x202020, 0xe0e0e0)
                    cl.append((code, "%s → %s", col, [keys.get(a, str(a)), keys.get(b, str(b))]))
                self.tag(rio.add_class_layer(res["path"], self.name("From–to change"), cl, tr(GROUP)), "From–to change")
                s = tr("Changed area: %.1f ha") % res["changed_ha"]
                self.ch_info.setText(s)
                self.session["change"] = {"summary": s, "cm": res["cm"], "labels": res["labels"],
                                          "pixel_area": res["pixel_area"], "path": res["path"],
                                          "name1": l1.name(), "name2": l2.name()}
                self.render_change()
                self.msg(s, "ok")
            self._task(tr("Change detection"), procs.change_postclass, (layer_path(l1), layer_path(l2), self.out_dir()),
                      on_done=done)

    # ============================================================ more datasets (generated LEE scripts)
    DATASET_CHOICES = [
        ("dem", "Elevation — Copernicus DEM 30 m (+ hillshade, slope)"),
        ("nasadem", "Elevation — NASADEM 30 m"),
        ("worldcover", "Land cover — ESA WorldCover 10 m (newest)"),
        ("lulc", "Land cover — Esri / Impact Observatory annual 10 m (year)"),
        ("water", "Surface water occurrence 1984–2021 — JRC 30 m"),
        ("modis", "MODIS NDVI 250 m — median of the dates above"),
    ]

    def _dataset_script(self, k):
        d0, d1 = self.d0.date().toString("yyyy-MM-dd"), self.d1.date().toString("yyyy-MM-dd")
        if k == "dem":
            return ('dem = lee.Image.load("dem")\nMap.addLayer(dem, {"palette": "Spectral"}, "Elevation (m)")\n'
                    'Map.addLayer(dem.hillshade(), {}, "Hillshade")\n'
                    'Map.addLayer(dem.slope(), {"min": 0, "max": 30, "palette": "YlOrRd"}, "Slope (°)")\n')
        if k == "nasadem":
            return 'dem = lee.Image.load("nasadem")\nMap.addLayer(dem, {"palette": "Spectral"}, "NASADEM (m)")\n'
        if k == "worldcover":
            return ('wc = lee.Image.load("worldcover")\nMap.addLayer(wc, {}, "ESA WorldCover")\n'
                    'print(wc.classAreas())\nChart.bars(wc.classAreas(), "WorldCover (ha)", "ha")\n')
        if k == "lulc":
            y = self.ds_year.value()
            return ('lc = lee.Image.load("lulc-annual", %d)\nMap.addLayer(lc, {}, "Land cover %d")\n'
                    'print(lc.classAreas())\nChart.bars(lc.classAreas(), "Land cover %d (ha)", "ha")\n' % (y, y, y))
        if k == "water":
            return ('occ = lee.Image.load("surface-water", bands="occurrence")\n'
                    'Map.addLayer(occ, {"min": 0, "max": 100, "palette": "Blues"}, "Water occurrence (%)")\n')
        return ('lee.config(scale=250)\nm = lee.ImageCollection("modis-ndvi").filterDate("%s", "%s").select("NDVI").median()\n'
                'Map.addLayer(m, {"min": 0, "max": 0.9, "palette": "RdYlGn"}, "MODIS NDVI median")\n' % (d0, d1))

    def add_dataset(self, as_script=False):
        code = self._dataset_script(self.ds.currentData())
        if as_script:
            self.code.setPlainText(code)
            self.tabs.setCurrentWidget(self.script_page)
            return
        self.run_script(code)

    # ============================================================ 9 SCRIPT (LEE — local Earth Engine)
    def build_script(self, P):
        lay = P.lay
        hdr = QLabel("<b>LEE — %s</b> · <small>%s</small>" % (
            tr("Local Earth Engine"), tr("Earth-Engine-style scripts that run on your computer with free data — no login.")))
        hdr.setWordWrap(True)
        hdr.setStyleSheet("background:#eef5fb;border-left:4px solid #1f6fb2;padding:4px")
        lay.addWidget(hdr)
        from qgis.PyQt.QtGui import QFont
        from .lee_examples import EXAMPLES
        self.examples = QComboBox()
        self.examples.setObjectName("examples")
        self.examples.addItem(tr("— examples —"), None)
        for t, code in EXAMPLES:
            self.examples.addItem(tr(t), code)
        self.examples.currentIndexChanged.connect(
            lambda i: self.examples.currentData() and self.code.setPlainText(self.examples.currentData()))
        lay.addWidget(self.examples)
        lay.addLayout(row(btn(tr("Open…"), self.open_script), btn(tr("Save…"), self.save_script),
                          btn(tr("API reference"), self.api_help)))
        self.code = QPlainTextEdit()
        self.code.setObjectName("code")
        f = QFont("Menlo")
        f.setStyleHint(E(QFont, "StyleHint.Monospace"))
        f.setPointSize(11)
        self.code.setFont(f)
        self.code.setMinimumHeight(220)
        self.code.setLineWrapMode(E(QPlainTextEdit, "LineWrapMode.NoWrap"))
        self.code.setPlainText(EXAMPLES[0][1])
        lay.addWidget(self.code)
        self.btn_run = btn(tr("Run"), lambda: self.run_script(), primary=True)
        self.btn_stop = btn(tr("Stop"), self.stop_script)
        self.btn_stop.setEnabled(False)
        lay.addLayout(row(self.btn_run, self.btn_stop))
        self.sc_prog = QProgressBar()
        lay.addWidget(self.sc_prog)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(f)
        self.out.setMinimumHeight(110)
        self.out.setPlaceholderText(tr("Output"))
        lay.addWidget(self.out)
        self.lee_use = QWidget()
        lu = QVBoxLayout(self.lee_use)
        lu.setContentsMargins(0, 4, 0, 0)
        sub(lu, tr("Use a result in the other tabs"))
        self.lee_layers = QComboBox()
        self.lee_layers.setObjectName("lee_layers")
        lu.addWidget(self.lee_layers)
        lu.addLayout(row(btn("→ " + tr("2 Enhance"), lambda: self.send_lee("enhance")),
                         btn("→ " + tr("3 Classify"), lambda: self.send_lee("classify")),
                         btn("→ " + tr("Accuracy / clean-up"), lambda: self.send_lee("classmap")),
                         btn("→ " + tr("Change: date 1"), lambda: self.send_lee("ch1")),
                         btn("→ " + tr("Change: date 2"), lambda: self.send_lee("ch2"))))
        self.lee_use.setVisible(False)
        lay.addWidget(self.lee_use)
        self._script_task = None

    def open_script(self):
        fn, _ = QFileDialog.getOpenFileName(self, tr("Open script"), self.out_dir(), "Python (*.py)")
        if fn:
            with open(fn, encoding="utf-8") as fh:
                self.code.setPlainText(fh.read())

    def save_script(self):
        fn, _ = QFileDialog.getSaveFileName(self, tr("Save script"), os.path.join(self.out_dir(), "lee_script.py"),
                                            "Python (*.py)")
        if fn:
            with open(fn, "w", encoding="utf-8") as fh:
                fh.write(self.code.toPlainText())

    def api_help(self):
        from . import lee
        txt = [lee.__doc__.strip(), "", "Datasets:", lee.datasets(), "", "Image methods:"]
        for n in sorted(dir(lee.Image)):
            if not n.startswith("_"):
                doc = (getattr(lee.Image, n).__doc__ or "").strip().split("\n")[0]
                txt.append("  img.%s  %s" % (n, doc))
        txt += ["", "ImageCollection methods:"]
        for n in sorted(dir(lee.ImageCollection)):
            if not n.startswith("_"):
                doc = (getattr(lee.ImageCollection, n).__doc__ or "").strip().split("\n")[0]
                txt.append("  col.%s  %s" % (n, doc))
        txt += ["", "Map.addLayer(img, vis, name) · Chart.series([(date, value)], title) · Chart.bars({k: v}, title)",
                "lee.config(scale=30) · lee.aoi() · print(...)"]
        self.out.setPlainText("\n".join(txt))

    def stop_script(self):
        if self._script_task is not None:
            try:
                self._script_task.cancel()
            except RuntimeError:
                pass

    def run_script(self, code=None, switch=True):
        from . import lee
        code = code if code is not None else self.code.toPlainText()
        if self.aoi_geom is None:
            self.msg(tr("Set an AOI first."), "warn")
            self.go("aoi")
            return
        geom = QgsGeometry(self.aoi_geom) if self.aoi_geom is not None else None
        out_dir, workers = self.out_dir(), self.par.value()
        lines = []
        self.out.setPlainText(tr("Running…"))
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)

        def job(task):
            return lee.run(code, geom, out_dir, task, lines.append, workers)

        def done(ok, res, err):
            self._script_task = None
            P = self._live()                     # the panel on screen (a rebuilt one if the language changed)
            P._script_task = None
            for pn in {id(self): self, id(P): P}.values():
                try:
                    pn.btn_run.setEnabled(True)
                    pn.btn_stop.setEnabled(False)
                except RuntimeError:
                    pass
            P.out.setPlainText("\n".join(lines))
            if not ok or not res:
                P.out.appendPlainText("\n" + tr("Error:") + " " + (err or tr("cancelled")))
                P.msg(tr("Script failed: %s") % (err or tr("cancelled"))[:200], "err")
                return
            P._show_script_results(res)
            P.out.appendPlainText("\n" + tr("Finished in %.0f s") % res["seconds"])
            P.session.setdefault("sources", [])
            for s_ in ("LEE (local) — data from Microsoft Planetary Computer / Earth Search STAC catalogues",):
                if s_ not in P.session["sources"]:
                    P.session["sources"].append(s_)
        t = self._task(tr("Running LEE script"), job, on_done=done)
        self._bind_progress(t, "sc_prog")
        self._script_task = t

    def send_lee(self, target):
        """Send a LEE output layer to the image boxes of another tab and open that section."""
        lyr = QgsProject.instance().mapLayer(self.lee_layers.currentData() or "")
        if lyr is None:
            return
        if target == "enhance":
            for cb in (self.c_raster, self.i_raster, self.f_raster):
                cb.setLayer(lyr)
            self.go("composite")
        elif target == "classify":
            for cb in (self.u_raster, self.s_raster):
                cb.setLayer(lyr)
            self.go("unsup")
        elif target == "classmap":
            self.a_class.setLayer(lyr)
            self.v_raster.setLayer(lyr)
            self.go("accuracy")
        else:
            (self.ch1 if target == "ch1" else self.ch2).setLayer(lyr)
            self.go("change")
        self.msg(tr("%s is ready to use.") % lyr.name(), "ok")

    def _show_script_results(self, res):
        group = tr(GROUP) + " — " + tr("Script")
        added = []
        for L in res["layers"]:
            vis = L["vis"]
            if L["kind"] == "rgb":
                lyr = rio.add_rgb_layer(L["path"], L["name"], (1, 2, 3), "clip", group)
                if lyr is not None and "min" in vis and "max" in vis:
                    rio.set_rgb_minmax(lyr, vis["min"], vis["max"])
            elif L["kind"] == "class":
                lyr = rio.add_class_layer(L["path"], L["name"], L["legend"], group)
            elif vis.get("palette"):
                lyr = rio.add_single_layer(L["path"], L["name"], vis["palette"], vis.get("min"), vis.get("max"), group)
            else:
                lyr = rio.add_gray_layer(L["path"], L["name"], group)
            if lyr is not None and getattr(self, "_wc_wait", False) and "worldcover" in L["name"].lower():
                self.wc_layer.setLayer(lyr)
                self._wc_wait = False
                self.msg(tr("WorldCover ready — now press 'Make training points'."), "ok")
                continue
            if lyr is not None:
                added.append(lyr)
                boxes = (self.a_class, self.v_raster, self.ch1, self.ch2) if L["kind"] == "class" else \
                    (self.c_raster, self.i_raster, self.f_raster, self.u_raster, self.s_raster)
                for cb in boxes:
                    if cb.currentLayer() is None:
                        cb.setLayer(lyr)
                        if cb is self.ch1:       # date 1 taken → next class map goes to date 2
                            break
        if added:
            self.lee_layers.clear()
            for lyr in reversed(added):
                self.lee_layers.addItem(lyr.name(), lyr.id())
            self.lee_use.setVisible(True)
        for c in res["charts"]:
            if c["kind"] == "series" and c["data"]:
                plots.plot_series(c["title"], [d for d, _ in c["data"]], [v for _, v in c["data"]], c["ylabel"], self)
            elif c["kind"] == "bars" and c["data"]:
                items = sorted(c["data"].items(), key=lambda kv: -kv[1])
                plots.plot_bars(tr(c["title"]), [tr(str(k)) for k, _ in items], [v for _, v in items], None,
                                c["ylabel"], self)

    # ============================================================ language, state, help, report
    def on_lang(self):
        lang = self.lang.currentData()
        if lang != language():
            set_language(lang)
            self.plugin.rebuild_panel()

    def get_state(self):
        st = {}
        for w in self.findChildren(QWidget):
            n = w.objectName()
            if not n or n.startswith("qt_"):
                continue
            try:
                if isinstance(w, QgsMapLayerComboBox):
                    st[n] = w.currentLayer().id() if w.currentLayer() else None
                elif isinstance(w, QgsFieldComboBox):
                    st[n] = w.currentField()
                elif isinstance(w, QgsFileWidget):
                    st[n] = w.filePath()
                elif isinstance(w, QComboBox):
                    st[n] = w.currentIndex()
                elif isinstance(w, QLineEdit):
                    st[n] = w.text()
                elif isinstance(w, QPlainTextEdit) and n == "code":
                    st[n] = w.toPlainText()
                elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                    st[n] = w.value()
                elif isinstance(w, QCheckBox):
                    st[n] = w.isChecked()
                elif isinstance(w, QDateEdit):
                    st[n] = w.date().toString("yyyy-MM-dd")
                elif isinstance(w, QTabWidget):
                    st[n] = w.currentIndex()
                elif isinstance(w, QAbstractButton) and w.isCheckable():
                    st[n] = w.isChecked()
            except Exception:  # noqa: BLE001
                pass
        st["_bands"] = [self.bands.item(i).checkState() == CHECKED for i in range(self.bands.count())]
        st["_scenes"] = [[self.scenes.item(r, c).text() if self.scenes.item(r, c) else ""
                          for c in range(self.scenes.columnCount())] for r in range(self.scenes.rowCount())]
        st["_stac"] = self.stac_scenes
        st["_local"] = [d["path"] for d in self.local_items]
        st["_local_use"] = [self.local_table.item(r, 0).checkState() == CHECKED
                            for r in range(self.local_table.rowCount()) if self.local_table.item(r, 0)]
        return st

    def set_state(self, st):
        order = ["src", "sensor", "cb_country", "aoi_src"]  # these rebuild other widgets, restore first
        self._pending_admin = [st.get("cb_prov", -1), st.get("cb_dist", -1), st.get("cb_comm", -1)]
        widgets = {w.objectName(): w for w in self.findChildren(QWidget) if w.objectName()}
        keys = [k for k in order if k in st] + [k for k in st if k not in order]
        keys.sort(key=lambda k: 1 if (k.startswith("sec_") and st.get(k)) else 0)   # open sections last
        for n in keys:
            w = widgets.get(n)
            v = st[n]
            if w is None or v is None or n == "_lang":
                continue
            try:
                if isinstance(w, QgsMapLayerComboBox):
                    lyr = QgsProject.instance().mapLayer(v)
                    if lyr:
                        w.setLayer(lyr)
                elif isinstance(w, QgsFieldComboBox):
                    w.setField(v)
                elif isinstance(w, QgsFileWidget):
                    w.setFilePath(v)
                elif isinstance(w, QComboBox):
                    if 0 <= v < w.count():
                        w.setCurrentIndex(v)
                elif isinstance(w, QLineEdit):
                    w.setText(v)
                elif isinstance(w, QPlainTextEdit) and n == "code":
                    w.setPlainText(v)
                elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                    w.setValue(v)
                elif isinstance(w, QCheckBox):
                    w.setChecked(v)
                elif isinstance(w, QDateEdit):
                    w.setDate(QDate.fromString(v, "yyyy-MM-dd"))
                elif isinstance(w, QTabWidget):
                    w.setCurrentIndex(v)
                elif isinstance(w, QAbstractButton) and w.isCheckable():
                    w.setChecked(bool(v))
            except Exception:  # noqa: BLE001
                pass
        for i, on in enumerate(st.get("_bands", [])):
            if i < self.bands.count():
                self.bands.item(i).setCheckState(CHECKED if on else UNCHECKED)
        if st.get("_scenes"):
            fill(self.scenes, st["_scenes"])
        self.stac_scenes = st.get("_stac")
        if st.get("_local"):
            self._local_plan(st["_local"])
            for r, on in enumerate(st.get("_local_use", [])):
                if self.local_table.item(r, 0):
                    self.local_table.item(r, 0).setCheckState(CHECKED if on else UNCHECKED)
        adm = self.session.get("aoi_admin")
        if adm:
            self.session["aoi_label"] = adm["km"] if (language() == "km" and adm.get("km")) else adm["en"]
        if self.session.get("aoi_geom") is not None:
            self.apply_aoi_crs()
            self.aoi_lab.setText("<b>%s</b> — %.1f km²" % (self.session.get("aoi_label", ""),
                                                           self.session.get("aoi_km2", 0)))

    def rerender(self):
        self.render_pca()
        self.render_unsup()
        self.render_sup()
        self.render_accuracy()
        self.render_change()
        if self.session.get("indices"):
            fill(self.idx_res, self.session["indices"])
        if self.admin is not None:
            idx = getattr(self, "_pending_admin", None) or \
                [cb.currentIndex() for cb in (self.cb_prov, self.cb_dist, self.cb_comm)]
            self._pending_admin = None
            self.fill_prov()       # re-label the units in the new language, keep the selection
            for cb, i, nxt in ((self.cb_prov, idx[0], self.on_prov), (self.cb_dist, idx[1], self.on_dist),
                               (self.cb_comm, idx[2], None)):
                if 0 <= i < cb.count():
                    cb.blockSignals(True)
                    cb.setCurrentIndex(i)
                    cb.blockSignals(False)
                    if nxt:
                        nxt()
            if getattr(self.admin, "describe", None):
                self.admin_lab.setText("<small>%s<br>%s</small>" % (self.admin.name, self.admin.describe()))

    def show_help(self):
        path = os.path.join(HERE, "help", "index_%s.html" % language())
        if not os.path.exists(path):
            path = os.path.join(HERE, "help", "index_en.html")
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def export_report(self):
        fn, _ = QFileDialog.getSaveFileName(self, tr("Save PDF report"),
                                            os.path.join(self.out_dir(), "RS_lab_report_%s.pdf" % language()), "PDF (*.pdf)")
        if not fn:
            return
        from . import basemaps as bmod
        cat = {b["id"]: b for b in bmod.catalogue()}
        for lyr in QgsProject.instance().mapLayers().values():   # basemap attribution in the report
            bid = lyr.customProperty("rslab_basemap")
            if bid in cat:
                txt = "%s — %s" % (cat[bid]["name"], cat[bid]["attribution"])
                self.session.setdefault("sources", [])
                if txt not in self.session["sources"]:
                    self.session["sources"].append(txt)
        try:
            cl = [(v, tr(n), c) for v, n, c in self.classes()]
            report.export_pdf(fn, self.session, self.canvas, cl)
            self.msg(tr("Saved %s") % fn, "ok")
            from qgis.PyQt.QtCore import QUrl
            from qgis.PyQt.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(fn))
        except Exception as e:  # noqa: BLE001
            self.msg(str(e), "err")

    def cleanup(self):
        if getattr(self, "lb_marker", None) is not None:
            try:
                self.canvas.scene().removeItem(self.lb_marker)
            except RuntimeError:
                pass
            self.lb_marker = None
        for t in (self.rect_tool, self.profile_tool):
            if t is not None and self.canvas.mapTool() is t:
                self.canvas.unsetMapTool(t)

