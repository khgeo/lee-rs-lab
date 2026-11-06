"""LEE RS Lab — plugin entry: dock widget, toolbar action, session, language rebuild, reset."""
import os

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import QDockWidget, QToolBar

from . import deps
from .compat import EA, RIGHT_DOCK, QAction
from .i18n import init_language, tr
import logging

LOG = logging.getLogger(__name__)

HERE = os.path.dirname(__file__)
NAME = "LEE RS Lab"


def _polygon_type():
    try:
        from qgis.core import Qgis
        return Qgis.GeometryType.Polygon
    except Exception:  # noqa: BLE001
        from qgis.core import QgsWkbTypes
        return EA((Qgis, "GeometryType.Polygon"), (QgsWkbTypes, "GeometryType.PolygonGeometry"))


def _alive(obj):
    """True if obj is a live Qt object (its C++ side has not been deleted)."""
    if obj is None:
        return False
    try:
        from qgis.PyQt import sip
        return not sip.isdeleted(obj)
    except Exception:  # noqa: BLE001
        try:
            obj.objectName()
            return True
        except RuntimeError:
            return False


class RSLab:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.panel = None
        self.action = None
        self.session = {}
        self.admin_backend = None
        self.aoi_band = None
        self.toolbar = None
        self.bm_button = self.bm_menu = self.bm_action = None
        self._retired = []
        deps.migrate_settings()
        init_language()

    # ------------------------------------------------------------ QGIS hooks
    def initGui(self):
        self.action = QAction(QIcon(os.path.join(HERE, "icon.png")), NAME, self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self.on_icon)
        self.action.setToolTip("LEE RS Lab — Local Earth Engine remote sensing lab with free data, no login")
        # its own toolbar (not mixed into the Plugins toolbar beside the Python console)
        # Always a fresh toolbar: one left from a previous load may already be queued for deletion.
        for old in self.iface.mainWindow().findChildren(QToolBar, "RSLabToolbar"):
            if _alive(old):
                self.iface.mainWindow().removeToolBar(old)
                old.setObjectName("RSLabToolbar_old")
                old.deleteLater()
        self.toolbar = self.iface.addToolBar(NAME)
        self.toolbar.setObjectName("RSLabToolbar")
        self.toolbar.setToolTip(NAME)
        self.toolbar.addAction(self.action)
        self.iface.mapCanvas().destinationCrsChanged.connect(self._reshow_aoi)
        self.iface.addPluginToRasterMenu(NAME, self.action)
        QgsProject.instance().cleared.connect(self.reset)

    def unload(self):
        try:
            QgsProject.instance().cleared.disconnect(self.reset)
        except Exception:  # noqa: BLE001
            LOG.debug("ignored error", exc_info=True)
        if _alive(self.panel):
            try:
                self.panel.cleanup()
            except RuntimeError:
                LOG.debug("ignored error", exc_info=True)
        self.panel = None
        try:
            self.clear_aoi()
        except RuntimeError:
            self.aoi_band = None
        dock, self.dock = self.dock, None
        if _alive(dock):
            try:
                self.iface.removeDockWidget(dock)
                dock.deleteLater()
            except RuntimeError:
                LOG.debug("ignored error", exc_info=True)
        try:
            self.iface.mapCanvas().destinationCrsChanged.disconnect(self._reshow_aoi)
        except Exception:  # noqa: BLE001
            LOG.debug("ignored error", exc_info=True)
        tb, self.toolbar = self.toolbar, None
        if _alive(tb):              # QGIS may already have destroyed it (e.g. on exit / reload)
            try:
                self.iface.mainWindow().removeToolBar(tb)
                tb.setObjectName("RSLabToolbar_old")
                tb.deleteLater()
            except RuntimeError:
                LOG.debug("ignored error", exc_info=True)
        self.bm_action = self.bm_button = self.bm_menu = None
        if _alive(self.action):
            try:
                self.iface.removePluginRasterMenu(NAME, self.action)
            except RuntimeError:
                LOG.debug("ignored error", exc_info=True)

    def _ensure_dock(self):
        if self.dock is None or not _alive(self.dock):
            self.dock = QDockWidget(NAME, self.iface.mainWindow())
            self.dock.setObjectName("RSLabDock")
            self.panel = self._new_panel()
            self.dock.setWidget(self.panel)
            self.iface.addDockWidget(RIGHT_DOCK, self.dock)
            # only mirror the state on the icon — never hide the dock from here: a tabified dock reports
            # "not visible" whenever another tab (e.g. Layer Styling) is in front, and hiding it then
            # made the panel disappear for good.
            self.dock.visibilityChanged.connect(lambda *_: self._sync_icon())
        return self.dock

    def _on_screen(self):
        """True if the panel is open AND in front (not just a hidden tab behind Layer Styling)."""
        d = self.dock
        if d is None or not _alive(d) or d.isHidden():
            return False
        try:
            return not d.visibleRegion().isEmpty()
        except Exception:  # noqa: BLE001
            return d.isVisible()

    def on_icon(self, *_):
        """Toolbar icon: open / bring to front; click again (when it is in front) to close."""
        if self._on_screen():
            self.dock.hide()
        else:
            d = self._ensure_dock()
            d.show()
            d.raise_()                      # also selects its tab when it is grouped with other panels
            d.activateWindow()
        self._sync_icon()

    def toggle(self, on):                   # kept for compatibility (e.g. other code calling it)
        if on and not self._on_screen():
            self.on_icon()
        elif not on and self._on_screen():
            self.on_icon()

    def _sync_icon(self):
        if not _alive(self.action):
            return
        open_ = self.dock is not None and _alive(self.dock) and not self.dock.isHidden()
        self.action.blockSignals(True)
        self.action.setChecked(open_)
        self.action.blockSignals(False)

    def _new_panel(self):
        from .panel import Panel
        return Panel(self)

    # ------------------------------------------------------------ language rebuild (same dock)
    def rebuild_panel(self):
        if self.dock is None:
            return
        old = self.panel
        state = old.get_state() if old else {}
        old_names = self._layer_names_snapshot()
        if old:
            old.cleanup()
        self.panel = self._new_panel()
        self.dock.setWidget(self.panel)
        self.dock.setWindowTitle(NAME)
        self.panel.set_state(state)
        self.panel.rerender()
        self._rename_layers(old_names)
        if old:
            self._carry_tasks(old, self.panel)
            self.retire_panel(old)

    def _layer_names_snapshot(self):
        root = QgsProject.instance().layerTreeRoot()
        return [g for g in root.findGroups()]

    def _rename_layers(self, groups):
        from .panel import GROUP
        for g in groups:
            if g.name() in (GROUP, "RS Lab", "LEE RS Lab", "មន្ទីរពិសោធន៍ចាប់សញ្ញាពីចម្ងាយ", "មន្ទីរពិសោធន៍យកព័ត៌មានពីចម្ងាយ"):
                g.setName(tr(GROUP))
        import re
        from . import raster_io as rio
        view = self.iface.layerTreeView()
        for lyr in QgsProject.instance().mapLayers().values():
            key = lyr.customProperty("rslab_key")
            if key:
                # translate a "(…)" part of the suffix too, e.g. " (Maximum Likelihood)"
                sfx = re.sub(r"\(([^()]+)\)", lambda m: "(%s)" % tr(m.group(1)),
                             lyr.customProperty("rslab_suffix") or "")
                lyr.setName(tr(key) + sfx)
            # legend class labels (Water, Cluster 1, Decrease, A → B …)
            if rio.relabel_layer(lyr) and view is not None:
                view.refreshLayerSymbology(lyr.id())

    # ------------------------------------------------------------ reset on new project
    def reset(self):
        classes = self.session.get("classes")
        self.session = {}
        if classes:
            self.session["classes"] = classes
        self.clear_aoi()
        if self.dock is not None:
            old = self.panel
            if old:
                old.cleanup()
            self.panel = self._new_panel()
            self.dock.setWidget(self.panel)
            if old:
                self.retire_panel(old)

    # ------------------------------------------------------------ old panels with running tasks
    def retire_panel(self, old):
        """Delete a replaced panel now, or — if one of its tasks is still running — hide it and keep it
        alive until the task finishes (otherwise its callbacks would touch deleted widgets)."""
        if not _alive(old):
            return
        if getattr(old, "busy", lambda: False)():
            old._retired = True
            old.hide()
            old.setParent(None)
            self._retired.append(old)
        else:
            old.deleteLater()

    @staticmethod
    def _carry_tasks(old, new):
        """A LEE script still running: the new panel's Stop button stops it, Run stays disabled."""
        t = getattr(old, "_script_task", None)
        if t is not None:
            new._script_task = t
            try:
                new.btn_run.setEnabled(False)
                new.btn_stop.setEnabled(True)
            except (AttributeError, RuntimeError):
                LOG.debug("ignored error", exc_info=True)

    def release_panel(self, old):
        if old in self._retired:
            self._retired.remove(old)
        if _alive(old):
            old.deleteLater()

    # ------------------------------------------------------------ AOI outline on the canvas
    def show_aoi(self, geom_4326):
        canvas = self.iface.mapCanvas()
        if self.aoi_band is None:
            self.aoi_band = QgsRubberBand(canvas, _polygon_type())
            self.aoi_band.setColor(QColor(255, 120, 0, 30))
            self.aoi_band.setStrokeColor(QColor(255, 90, 0))
            self.aoi_band.setWidth(2)
        g = QgsGeometry(geom_4326)
        g.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                           canvas.mapSettings().destinationCrs(), QgsProject.instance()))
        self.aoi_band.setToGeometry(g, None)

    def _reshow_aoi(self):
        """Keep the orange AOI outline in place when the map CRS changes."""
        g = self.session.get("aoi_geom")
        if g is not None and self.aoi_band is not None:
            self.show_aoi(g)

    def clear_aoi(self):
        if self.aoi_band is not None:
            self.aoi_band.reset(_polygon_type())
            self.iface.mapCanvas().scene().removeItem(self.aoi_band)
            self.aoi_band = None
