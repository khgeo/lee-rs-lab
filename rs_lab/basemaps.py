"""One-click XYZ basemaps (catalogue in data/sources.json → "basemaps"), added at the BOTTOM of the
layer tree so they sit under the imagery, plus the CRS helper that picks the AOI's UTM zone."""
import json
import os

from qgis.core import (QgsCoordinateReferenceSystem, QgsProject, QgsRasterLayer)
from .compat import E
import logging

LOG = logging.getLogger(__name__)

HERE = os.path.dirname(__file__)


def catalogue():
    with open(os.path.join(HERE, "data", "sources.json"), encoding="utf-8") as f:
        return json.load(f).get("basemaps", [])


def _uri(url, zmax):
    enc = url.replace("&", "%26").replace("=", "%3D")
    return "type=xyz&url=%s&zmin=0&zmax=%d" % (enc, zmax)


def add_basemap(bid):
    """Add (or re-show) a basemap. Returns the layer or None."""
    bm = next((b for b in catalogue() if b["id"] == bid), None)
    if bm is None:
        return None
    proj = QgsProject.instance()
    root = proj.layerTreeRoot()
    uri = _uri(bm["url"], bm.get("zmax", 19))
    for lyr in proj.mapLayers().values():          # already there → just make it visible
        if lyr.customProperty("rslab_basemap") == bid:
            node = root.findLayer(lyr.id())
            if node is not None:
                node.setItemVisibilityChecked(True)
            return lyr
    lyr = QgsRasterLayer(uri, bm["name"], "wms")
    if not lyr.isValid():
        return None
    lyr.setCustomProperty("rslab_basemap", bid)
    try:
        lyr.setAttribution(bm.get("attribution", ""))
    except Exception:  # noqa: BLE001  (older QGIS)
        md = lyr.metadata()
        md.setRights([bm.get("attribution", "")])
        lyr.setMetadata(md)
    proj.addMapLayer(lyr, False)
    root.insertLayer(len(root.children()), lyr)     # bottom of the layer list
    return lyr


def build_menu(parent, tr=lambda t: t):
    """Basemap drop-down used by the toolbar arrow and the panel header."""
    from qgis.PyQt.QtWidgets import QMenu
    menu = QMenu(parent)
    for b in catalogue():
        menu.addAction(b["name"]).triggered.connect(lambda _=False, bid=b["id"]: _add_and_show(bid, tr))
    menu.addSeparator()
    menu.addAction(tr("Remove all basemaps")).triggered.connect(lambda _=False: remove_basemaps())
    return menu


def _add_and_show(bid, tr):
    from qgis.utils import iface
    lyr = add_basemap(bid)
    if lyr is None:
        try:
            from qgis.core import Qgis
            iface.messageBar().pushMessage("RS Lab", tr("Could not add the basemap (no internet?)."), E(Qgis, "MessageLevel.Warning"), 6)
        except Exception:  # noqa: BLE001
            LOG.debug("ignored error", exc_info=True)
    elif len(QgsProject.instance().mapLayers()) == 1:      # first layer → show the world
        iface.mapCanvas().setExtent(iface.mapCanvas().fullExtent())
        iface.mapCanvas().refresh()


def remove_basemaps():
    proj = QgsProject.instance()
    ids = [lyr.id() for lyr in proj.mapLayers().values() if lyr.customProperty("rslab_basemap")]
    if ids:
        proj.removeMapLayers(ids)
    return len(ids)


# ------------------------------------------------------------------ CRS for an AOI
def utm_for(geom_4326):
    """(epsg, label) of the UTM zone at the AOI centroid, e.g. (32735, 'WGS 84 / UTM 35S')."""
    c = geom_4326.centroid().asPoint()
    zone = min(60, max(1, int((c.x() + 180) / 6) + 1))
    south = c.y() < 0
    return (32700 if south else 32600) + zone, "WGS 84 / UTM %d%s" % (zone, "S" if south else "N")


def display_crs_for(geom_4326):
    """CRS for the map display: the UTM zone, or Web Mercator for AOIs wider than ~3 UTM zones."""
    bb = geom_4326.boundingBox()
    if bb.width() > 18 or bb.height() > 30:
        return QgsCoordinateReferenceSystem("EPSG:3857"), "EPSG:3857 — WGS 84 / Pseudo-Mercator"
    epsg, lab = utm_for(geom_4326)
    return QgsCoordinateReferenceSystem("EPSG:%d" % epsg), "EPSG:%d — %s" % (epsg, lab)
