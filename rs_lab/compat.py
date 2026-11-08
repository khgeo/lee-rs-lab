"""Qt5/Qt6 and QGIS 3/4 compatibility helpers."""
from qgis.PyQt import QtCore, QtWidgets
import logging

LOG = logging.getLogger(__name__)

try:
    from qgis.PyQt.QtWidgets import QAction  # noqa: F401  Qt5 (re-exported)
except ImportError:  # Qt6
    from qgis.PyQt.QtGui import QAction  # noqa: F401  (re-exported)


def E(owner, dotted):
    """Resolve an enum value by scoped path, falling back to the short name.
    E(Qt, 'AlignmentFlag.AlignLeft') works on both PyQt5 and PyQt6."""
    obj = owner
    try:
        for part in dotted.split("."):
            obj = getattr(obj, part)
        return obj
    except AttributeError:
        return getattr(owner, dotted.split(".")[-1])


def _has_path(owner, dotted):
    obj = owner
    for part in dotted.split("."):
        if not hasattr(obj, part):
            return False
        obj = getattr(obj, part)
    return True


def EA(*pairs):
    """First enum value that resolves among (owner, 'Enum.Member') pairs, e.g. the QGIS 4 name in Qgis first,
    then the QGIS 3 name. Each pair falls back to the short member name as E() does."""
    for owner, dotted in pairs:
        last = dotted.split(".")[-1]
        if _has_path(owner, dotted) or hasattr(owner, last):
            return E(owner, dotted)
    raise AttributeError(str(pairs))


def run_dialog(dlg):
    f = getattr(dlg, "exec", None) or getattr(dlg, "exec_")
    return f()


Qt = QtCore.Qt
CHECKED = E(Qt, "CheckState.Checked")
UNCHECKED = E(Qt, "CheckState.Unchecked")
USER_ROLE = E(Qt, "ItemDataRole.UserRole")
CHECKABLE = E(Qt, "ItemFlag.ItemIsUserCheckable")
EDITABLE = E(Qt, "ItemFlag.ItemIsEditable")
RIGHT_DOCK = E(Qt, "DockWidgetArea.RightDockWidgetArea")
SCROLL_OFF = E(Qt, "ScrollBarPolicy.ScrollBarAlwaysOff")
SELECT_ROWS = E(QtWidgets.QAbstractItemView, "SelectionBehavior.SelectRows")
MULTI_SEL = E(QtWidgets.QAbstractItemView, "SelectionMode.ExtendedSelection")
NO_EDIT = E(QtWidgets.QAbstractItemView, "EditTrigger.NoEditTriggers")
STRETCH = E(QtWidgets.QHeaderView, "ResizeMode.Stretch")
RESIZE_CONTENTS = E(QtWidgets.QHeaderView, "ResizeMode.ResizeToContents")
YES = E(QtWidgets.QMessageBox, "StandardButton.Yes")
NO = E(QtWidgets.QMessageBox, "StandardButton.No")


def can_cancel():
    from qgis.core import QgsTask
    return E(QgsTask, "Flag.CanCancel")


def layer_filter(kind):
    """kind: 'raster', 'vector', 'polygon', 'point'."""
    names = {"raster": "RasterLayer", "vector": "VectorLayer", "polygon": "PolygonLayer",
             "point": "PointLayer"}
    n = names[kind]
    try:
        from qgis.core import Qgis
        return getattr(Qgis.LayerFilter, n)
    except Exception:
        from qgis.core import QgsMapLayerProxyModel
        return getattr(QgsMapLayerProxyModel, n)


def field_filter_numeric():
    try:                                           # QGIS ≥ 3.34
        from qgis.core import Qgis
        return Qgis.FieldFilter.Numeric
    except Exception:  # noqa: BLE001
        from qgis.core import QgsFieldProxyModel
        return E(QgsFieldProxyModel, "Filter.Numeric")


def set_layer_filters(combo, *kinds):
    """combo.setFilters() for one or more layer kinds without the QGIS ≥ 3.34 DeprecationWarning:
    OR-ing two Qgis.LayerFilter values gives a plain int, which matches the deprecated
    QgsMapLayerProxyModel.Filters overload — wrap it back into Qgis.LayerFilters first."""
    import warnings
    v = layer_filter(kinds[0])
    for k in kinds[1:]:
        v = v | layer_filter(k)
    if len(kinds) > 1:
        try:
            from qgis.core import Qgis
            if hasattr(Qgis, "LayerFilters"):          # PyQt5 QFlags type
                v = Qgis.LayerFilters(v)
            elif hasattr(Qgis, "LayerFilter"):         # PyQt6 enum.Flag
                v = Qgis.LayerFilter(v)
        except Exception:  # noqa: BLE001
            LOG.debug("ignored error", exc_info=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        combo.setFilters(v)


def label_font(item, family="Arial", size=10, bold=False):
    """Font of a QgsLayoutItemLabel: setTextFormat (QGIS ≥ 3.40) or the older setFont."""
    from qgis.PyQt.QtGui import QFont
    f = QFont(family, int(size))
    f.setBold(bold)
    try:
        from qgis.core import QgsTextFormat
        tf = QgsTextFormat.fromQFont(f)
        tf.setSize(size)
        item.setTextFormat(tf)
    except Exception:  # noqa: BLE001
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            item.setFont(f)


def web_url(url):
    """Only http(s) URLs are ever opened (no file: or custom schemes)."""
    if not str(url).lower().startswith(("https://", "http://")):
        raise ValueError("Refusing to open a non-web URL: %r" % (url,))
    return url
