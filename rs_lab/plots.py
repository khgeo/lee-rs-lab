"""Small matplotlib chart dialogs + the spectral profile (pixel signature) map tool."""
import re as _re
import warnings as _warnings

import numpy as np
from osgeo import gdal
from qgis.core import QgsCoordinateTransform, QgsProject
from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout

from .i18n import tr
import logging

LOG = logging.getLogger(__name__)

try:
    from matplotlib.figure import Figure
    try:
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as Canvas
    except ImportError:
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as Canvas
    HAVE_MPL = True
except Exception:  # noqa: BLE001
    HAVE_MPL = False


def _khmer_fonts():
    """Font fallback so Khmer class names / titles render (not as boxes): the default DejaVu Sans has no
    Khmer glyphs. Matplotlib ≥ 3.6 falls back glyph by glyph along font.sans-serif."""
    if not HAVE_MPL:
        return
    try:
        import os
        import matplotlib
        from matplotlib import font_manager as fm
        want = ["Khmer Sangam MN", "Khmer MN", "Khmer UI", "Khmer OS System", "Khmer OS", "Noto Sans Khmer",
                "Leelawadee UI", "Hanuman", "Battambang"]
        dirs = ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
                os.path.expanduser("~/Library/Fonts"), "C:/Windows/Fonts", "/usr/share/fonts",
                os.path.expanduser("~/.local/share/fonts")]
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for root, _, files in os.walk(d):
                for f in files:
                    fl = f.lower()
                    if ("khmer" in fl or "leelawad" in fl or "hanuman" in fl) and fl.endswith((".ttf", ".otf", ".ttc")):
                        try:
                            fm.fontManager.addfont(os.path.join(root, f))
                        except Exception:  # noqa: BLE001
                            LOG.debug("ignored error", exc_info=True)
        have = {f.name for f in fm.fontManager.ttflist}
        found = [n for n in want if n in have]
        if found:
            cur = list(matplotlib.rcParams.get("font.sans-serif", []))
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["font.sans-serif"] = ["DejaVu Sans"] + found + [c for c in cur if c not in found]
    except Exception:  # noqa: BLE001
        LOG.debug("ignored error", exc_info=True)


# ---------------------------------------------------------------- Khmer text
# Matplotlib cannot shape Khmer (stacked consonants, vowels around the base), so even with a Khmer font
# the words come out as boxes or broken clusters. Qt shapes Khmer correctly with the system font, so every
# Khmer text in a figure (title, tick labels, legend, axis labels) is drawn by Qt into a small transparent
# image and pinned onto the original text, which stays in place (invisible) for layout.

_KH = _re.compile("[\u1780-\u17FF\u19E0-\u19FF]")
_warnings.filterwarnings("ignore", message=r"Glyph \d+ .*missing from font")
_warnings.filterwarnings("ignore", message=r"Matplotlib currently does not support Khmer")
_QT_FAMILY = []


def _qt_khmer_family():
    if not _QT_FAMILY:
        fam = ""
        try:
            from qgis.PyQt.QtGui import QFontDatabase
            try:
                have = set(QFontDatabase.families())          # Qt 6 (static)
            except TypeError:
                have = set(QFontDatabase().families())        # Qt 5
            for n in ("Khmer Sangam MN", "Khmer MN", "Khmer OS System", "Khmer OS Battambang", "Khmer UI",
                      "Noto Sans Khmer", "Leelawadee UI", "Hanuman", "Battambang"):
                if n in have:
                    fam = n
                    break
        except Exception:  # noqa: BLE001
            LOG.debug("ignored error", exc_info=True)
        _QT_FAMILY.append(fam)
    return _QT_FAMILY[0]


def _qt_text_rgba(text, pt, color, bold=False, rotation=0.0, scale=4):
    """RGBA uint8 array of `text` drawn by Qt (pt = font size in points; 1 image pixel = 1/scale point)."""
    from qgis.PyQt.QtCore import QRectF, Qt
    from qgis.PyQt.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QTransform
    from .compat import E
    fam = _qt_khmer_family()
    f = QFont(fam) if fam else QFont()
    f.setPixelSize(max(1, int(round(pt * scale))))
    f.setBold(bool(bold))
    fm = QFontMetricsF(f)
    lines = text.split("\n")
    adv = getattr(fm, "horizontalAdvance", None) or fm.width
    w = int(max(adv(ln) for ln in lines) + 2 * scale) + 1
    h = int(fm.lineSpacing() * len(lines) + scale) + 1
    fmt = E(QImage, "Format.Format_RGBA8888")
    img = QImage(w, h, fmt)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(E(QPainter, "RenderHint.Antialiasing"))
    p.setRenderHint(E(QPainter, "RenderHint.TextAntialiasing"))
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, w, h), E(Qt, "AlignmentFlag.AlignCenter"), text)
    p.end()
    if rotation % 360:
        img = img.transformed(QTransform().rotate(-rotation), E(Qt, "TransformationMode.SmoothTransformation"))
    img = img.convertToFormat(fmt)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    ptr = img.constBits()
    n = img.sizeInBytes() if hasattr(img, "sizeInBytes") else img.byteCount()
    try:
        ptr.setsize(n)
    except AttributeError:
        LOG.debug("ignored error", exc_info=True)
    arr = np.frombuffer(ptr, np.uint8, count=n).reshape(h, bpl)[:, :w * 4].reshape(h, w, 4)
    return arr.copy()


def _khmerize(fig, scale=4):
    """Replace every visible Khmer text of `fig` by a Qt-rendered image at the same place and alignment."""
    try:
        from matplotlib.colors import to_hex
        from matplotlib.offsetbox import AnnotationBbox, OffsetImage
        from matplotlib.text import Text
    except Exception:  # noqa: BLE001
        return
    fx_of = {"left": 0.0, "center": 0.5, "right": 1.0}
    fy_of = {"top": 1.0, "center": 0.5, "center_baseline": 0.5, "bottom": 0.0, "baseline": 0.1}
    for t in fig.findobj(Text):
        s = t.get_text()
        if not s or getattr(t, "_rslab_kh", False) or not t.get_visible() or not _KH.search(s):
            continue
        try:
            wt = t.get_fontweight()
            bold = wt in ("bold", "heavy", "black", "semibold", "demibold") or (isinstance(wt, (int, float)) and wt >= 600)
            arr = _qt_text_rgba(s, t.get_fontsize(), to_hex(t.get_color()), bold, t.get_rotation(), scale)
        except Exception:  # noqa: BLE001
            LOG.debug("ignored error", exc_info=True)
            continue
        fx, fy = fx_of.get(t.get_horizontalalignment(), 0.5), fy_of.get(t.get_verticalalignment(), 0.5)
        ab = AnnotationBbox(OffsetImage(arr, zoom=1.0 / scale), (fx, fy), xycoords=t, box_alignment=(fx, fy),
                            frameon=False, pad=0, annotation_clip=False)
        ab.set_in_layout(False)
        ab.set_zorder(max(t.get_zorder(), 5) + 1)
        ab._rslab_kh = True
        fig.add_artist(ab)
        t.set_alpha(0)          # keeps its box for layout and as the anchor; the image is what you see
        t._rslab_kh = True


_khmer_fonts()

_open = []


class ChartDialog(QDialog):
    def __init__(self, title, parent=None, w=7, h=4.2):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        if not HAVE_MPL:
            lay.addWidget(QLabel(tr("matplotlib is not available in this QGIS installation.")))
            self.fig = None
            return
        self.fig = Figure(figsize=(w, h), dpi=100)
        self.canvas = Canvas(self.fig)
        lay.addWidget(self.canvas)
        row = QHBoxLayout()
        save = QPushButton(tr("Save PNG…"))
        save.clicked.connect(self.save)
        row.addStretch(1)
        row.addWidget(save)
        lay.addLayout(row)
        self.resize(int(w * 110), int(h * 120))
        self.setModal(False)
        _open.append(self)

    def save(self):
        from qgis.PyQt.QtWidgets import QFileDialog
        fn, _ = QFileDialog.getSaveFileName(self, tr("Save chart"), "chart.png", "PNG (*.png)")
        if fn and self.fig is not None:
            self.fig.savefig(fn, dpi=200, bbox_inches="tight")

    def done_(self):
        if self.fig is not None:
            try:
                if not getattr(self, "no_tight", False):
                    self.fig.tight_layout()
            except Exception:  # noqa: BLE001
                LOG.debug("ignored error", exc_info=True)
            _khmerize(self.fig)
            self.canvas.draw()
        self.show()
        self.raise_()


def _short(d):
    return d.split("|")[0].strip()


_RADAR = None


def _is_radar(label):
    """Sentinel-1 / SAR bands in dB (VV, VH, VH_dry, 'VV | vv', '... dB')."""
    import re
    global _RADAR
    if _RADAR is None:
        _RADAR = re.compile(r"(^|[^A-Za-z])(VV|VH|HH|HV)([^A-Za-z]|$)|\bdB\b")
    return bool(_RADAR.search(str(label)))


def _sig_panel(ax, x, labels, series, idx, ylabel):
    for lab, vals, col, sd in series:
        v = np.asarray(vals, float)[idx]
        ax.plot(x, v, marker="o", label=lab, color=col)
        if sd is not None:
            s = np.asarray(sd, float)[idx]
            ax.fill_between(x, v - s, v + s, alpha=0.12, color=col)
    ax.set_xticks(x)
    ax.set_xticklabels([_short(labels[i]) for i in idx], rotation=45, ha="right")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)


def plot_signatures(title, band_labels, series, parent=None):
    """series: list of (label, values, color or None, stds or None).
    When optical bands / indices are mixed with radar bands in dB (e.g. the multi-season stack), the radar
    bands are drawn in a second panel with their own dB axis — on one axis the dB values (−25…−5) squash
    indices (−1…1) into a flat line."""
    dlg = ChartDialog(title, parent)
    if dlg.fig is None:
        dlg.show()
        return dlg
    n = len(band_labels)
    rad = [i for i in range(n) if _is_radar(band_labels[i])]
    opt = [i for i in range(n) if i not in rad]
    if rad and opt:
        gs = dlg.fig.add_gridspec(1, 2, width_ratios=[max(len(opt), 2), max(len(rad), 1.5)], wspace=0.28)
        ax = dlg.fig.add_subplot(gs[0, 0])
        ax2 = dlg.fig.add_subplot(gs[0, 1])
        _sig_panel(ax, np.arange(len(opt)), band_labels, series, opt, "Value (reflectance / index)")
        _sig_panel(ax2, np.arange(len(rad)), band_labels, series, rad, "Backscatter (dB)")
        ax2.set_title("Radar", fontsize=9, loc="left")
        ax.set_title(title, fontsize=11, loc="left")
        ax.set_xlabel("Band")
        h, l = ax.get_legend_handles_labels()
        ax2.legend(h, l, fontsize=7, loc="center left", bbox_to_anchor=(1.02, 0.5), borderaxespad=0)
        dlg.fig.subplots_adjust(left=0.08, right=0.85, bottom=0.27, top=0.9, wspace=0.28)
        dlg.no_tight = True                  # tight_layout cannot handle the outside legend
    else:
        ax = dlg.fig.add_subplot(111)
        ax.set_title(title, fontsize=11, loc="left")
        _sig_panel(ax, np.arange(n), band_labels, series, list(range(n)),
                   "Backscatter (dB)" if rad else "Value (reflectance / DN)")
        ax.set_xlabel("Band")
        ax.legend(fontsize=8)
    dlg.done_()
    return dlg


def plot_histograms(title, hists, parent=None):
    dlg = ChartDialog(title, parent)
    if dlg.fig is None:
        dlg.show()
        return dlg
    ax = dlg.fig.add_subplot(111)
    ax.set_title(title, fontsize=11, loc="left")
    cols = ["#d7191c", "#1a9641", "#2b83ba", "#7b3294", "#fdae61", "#404040"]
    for i, (lab, h, e) in enumerate(hists):
        c = (np.asarray(e[:-1]) + np.asarray(e[1:])) / 2
        ax.plot(c, h, label=_short(lab), color=cols[i % len(cols)])
    ax.set_xlabel("Pixel value")
    ax.set_ylabel("Frequency")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    dlg.done_()
    return dlg


def plot_bars(title, labels, values, colors=None, ylabel="", parent=None):
    dlg = ChartDialog(title, parent)
    if dlg.fig is None:
        dlg.show()
        return dlg
    ax = dlg.fig.add_subplot(111)
    ax.set_title(title, fontsize=11, loc="left")
    ax.bar(range(len(values)), values, color=colors)
    ax.set_xticks(range(len(values)))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.3)
    dlg.done_()
    return dlg


def plot_scatter(title, xs, ys, xl, yl, labels=None, colors=None, parent=None):
    """Feature-space plot (e.g. Red vs NIR) of training pixels."""
    dlg = ChartDialog(title, parent, 5.5, 5)
    if dlg.fig is None:
        dlg.show()
        return dlg
    ax = dlg.fig.add_subplot(111)
    ax.set_title(title, fontsize=11, loc="left")
    if labels is None:
        ax.scatter(xs, ys, s=2, alpha=0.3)
    else:
        for i, lab in enumerate(labels):
            ax.scatter(xs[i], ys[i], s=3, alpha=0.4, label=lab, color=colors[i] if colors else None)
        ax.legend(fontsize=8, markerscale=4)
    ax.set_xlabel(_short(xl))
    ax.set_ylabel(_short(yl))
    ax.grid(alpha=0.3)
    dlg.done_()
    return dlg


class SpectralProfileTool(QgsMapToolEmitPoint):
    """Click on the map: plot the spectrum of the pixel from the chosen raster. Clicks accumulate."""

    def __init__(self, canvas, get_layer):
        super().__init__(canvas)
        self.canvas, self.get_layer = canvas, get_layer
        self.series = []
        self.canvasClicked.connect(self.on_click)
        self.dlg = None

    def on_click(self, pt, button):
        lyr = self.get_layer()
        if lyr is None or lyr.providerType() != "gdal":
            return
        ct = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), lyr.crs(), QgsProject.instance())
        p = ct.transform(pt)
        ds = gdal.Open(lyr.source().split("|")[0])
        gt = ds.GetGeoTransform()
        col = int((p.x() - gt[0]) / gt[1])
        row = int((p.y() - gt[3]) / gt[5])
        if not (0 <= col < ds.RasterXSize and 0 <= row < ds.RasterYSize):
            return
        vals, labs = [], []
        for i in range(1, ds.RasterCount + 1):
            b = ds.GetRasterBand(i)
            v = float(b.ReadAsArray(col, row, 1, 1)[0, 0])
            nd = b.GetNoDataValue()
            vals.append(np.nan if nd is not None and v == nd else v)
            labs.append(b.GetDescription() or "Band %d" % i)
        self.series.append(("P%d (%.0f, %.0f)" % (len(self.series) + 1, p.x(), p.y()), vals, None, None))
        self.series = self.series[-8:]
        if self.dlg is not None:
            self.dlg.close()
        self.dlg = plot_signatures(tr("Spectral profile — click more pixels to compare"), labs, self.series,
                                   self.canvas.window())

    def clear(self):
        self.series = []


def plot_series(title, dates, values, ylabel="", parent=None):
    """Time-series line chart (dates as YYYY-MM-DD strings)."""
    dlg = ChartDialog(title, parent, 8, 4)
    if dlg.fig is None:
        dlg.show()
        return dlg
    import datetime as _dt
    ax = dlg.fig.add_subplot(111)
    ax.set_title(title, fontsize=11, loc="left")
    try:
        x = [_dt.date.fromisoformat(d[:10]) for d in dates]
    except ValueError:
        x = list(range(len(dates)))
    ax.plot(x, values, marker="o", ms=3, lw=1.2, color="#1f6fb2")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)
    dlg.fig.autofmt_xdate()
    dlg.done_()
    return dlg


def plot_grouped(title, labels, a, b, la, lb, ylabel="", parent=None):
    """Grouped bars: two series (e.g. class area at date 1 and date 2)."""
    dlg = ChartDialog(title, parent)
    if dlg.fig is None:
        dlg.show()
        return dlg
    ax = dlg.fig.add_subplot(111)
    ax.set_title(title, fontsize=11, loc="left")
    x = np.arange(len(labels))
    w = 0.38
    ax.bar(x - w / 2, a, w, label=la, color="#3f7fd9")
    ax.bar(x + w / 2, b, w, label=lb, color="#e0803c")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel(ylabel)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    dlg.done_()
    return dlg
