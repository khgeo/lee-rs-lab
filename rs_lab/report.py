"""PDF lab report (QTextDocument HTML + QPrinter, A4 portrait, Qt5/Qt6 safe)."""
import base64
import datetime
import html
import math

from qgis.core import QgsDistanceArea, QgsMapRendererParallelJob, QgsMapSettings, QgsProject
from qgis.PyQt.QtCore import QBuffer, QByteArray, QPointF, QRectF, QSize, QSizeF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter, QPen, QPolygonF, QTextDocument
from qgis.PyQt.QtPrintSupport import QPrinter

from .compat import E
from .i18n import language, tr
import logging

LOG = logging.getLogger(__name__)


def _img_b64(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(E(QBuffer, "OpenModeFlag.WriteOnly"))
    img.save(buf, "PNG")
    return base64.b64encode(bytes(ba)).decode()


def render_canvas(canvas, w=1600, h=1100):
    ms = QgsMapSettings(canvas.mapSettings())
    ms.setOutputSize(QSize(w, h))
    ms.setExtent(canvas.extent())
    job = QgsMapRendererParallelJob(ms)
    job.start()
    job.waitForFinished()
    img = job.renderedImage()
    p = QPainter(img)
    p.setRenderHint(E(QPainter, "RenderHint.Antialiasing"))
    # north arrow
    cx, cy = w - 70, 90
    poly = QPolygonF([QPointF(cx, cy - 50), QPointF(cx + 22, cy + 25), QPointF(cx, cy + 12), QPointF(cx - 22, cy + 25)])
    p.setPen(QPen(QColor("#222"), 2))
    p.setBrush(QColor("#222"))
    p.drawPolygon(poly)
    f = QFont("Arial", 22)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(cx - 20, cy + 28, 40, 30), E(Qt, "AlignmentFlag.AlignCenter"), "N")
    # scale bar
    try:
        da = QgsDistanceArea()
        da.setSourceCrs(ms.destinationCrs(), QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")
        e = ms.extent()
        from qgis.core import QgsPointXY
        y = e.yMinimum() + e.height() * 0.05
        m_per_px = da.measureLine(QgsPointXY(e.xMinimum(), y), QgsPointXY(e.xMaximum(), y)) / w
        target = m_per_px * w * 0.25
        nice = 10 ** math.floor(math.log10(target))
        for k in (5, 2, 1):
            if k * nice <= target:
                nice *= k
                break
        px = nice / m_per_px
        x0, y0 = 40, h - 50
        p.setBrush(QColor("white"))
        p.setPen(QPen(QColor("white"), 0))
        p.drawRect(QRectF(x0 - 12, y0 - 40, px + 90, 62))
        p.setPen(QPen(QColor("#222"), 3))
        p.drawLine(QPointF(x0, y0), QPointF(x0 + px, y0))
        p.drawLine(QPointF(x0, y0 - 10), QPointF(x0, y0 + 6))
        p.drawLine(QPointF(x0 + px, y0 - 10), QPointF(x0 + px, y0 + 6))
        p.setFont(QFont("Arial", 18))
        lab = "%g km" % (nice / 1000) if nice >= 1000 else "%g m" % nice
        p.drawText(QPointF(x0 + px + 10, y0 + 6), lab)
    except Exception:  # noqa: BLE001
        LOG.debug("ignored error", exc_info=True)
    p.end()
    return img


def _table(headers, rows, fmt="%.3f"):
    def cell(v):
        if isinstance(v, float):
            return "—" if v != v else fmt % v
        return html.escape(str(v))
    h = "".join("<th>%s</th>" % html.escape(str(x)) for x in headers)
    b = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % cell(v) for v in r) for r in rows)
    return "<table cellspacing='0' cellpadding='4' border='1' width='100%%'><tr>%s</tr>%s</table>" % (h, b)


def build_html(session, canvas, classes):
    km = language() == "km"
    font = "'Khmer OS System','Khmer OS Battambang','Leelawadee UI',Arial" if km else "Arial"
    out = ["<html><body style=\"font-family:%s; font-size:10pt;\">" % font]
    out.append("<h1 style='color:#1f5f8b'>%s</h1>" % tr("LEE RS Lab — Remote Sensing Report"))
    out.append("<p>%s: %s<br>%s: %s</p>" % (tr("Date"), datetime.date.today().isoformat(),
                                            tr("Project"), html.escape(QgsProject.instance().title() or
                                                                       QgsProject.instance().baseName() or "—")))
    # key findings
    find = []
    acc = session.get("accuracy")
    sup = session.get("supervised") or session.get("unsupervised")
    if sup:
        top = sorted(sup["areas"], key=lambda r: -r[2])[:3]
        names = {c[0]: c[1] for c in classes}
        find.append(tr("Largest classes: %s") % ", ".join("%s (%.1f%%)" % (names.get(v, v), pct)
                                                          for v, _, _, pct in top))
    if acc:
        find.append(tr("Overall accuracy %.1f%%, kappa %.3f (n = %d)") % (100 * acc["oa"], acc["kappa"], acc["n"]))
        if acc["oa"] < 0.85:
            find.append("<span style='color:#c0392b'>%s</span>" %
                        tr("Accuracy is below 85%: add or refine training samples, check confused classes."))
    if session.get("supervised") and "train_oa" in session["supervised"] and not acc:
        find.append("<span style='color:#c0392b'>%s</span>" %
                    tr("No independent accuracy assessment yet — training accuracy is optimistic."))
    if find:
        out.append("<h2>%s</h2><ul>%s</ul>" % (tr("Key findings"), "".join("<li>%s</li>" % f for f in find)))
    if canvas is not None:
        # 16:9 and 500 pt wide so the heading and the map fit on page 1 (a taller map jumped to page 2,
        # leaving page 1 almost empty).
        img = render_canvas(canvas, 1600, 900)
        out.append("<h2>%s</h2><p><img width='500' src='data:image/png;base64,%s'></p>" % (tr("Map"), _img_b64(img)))
    dl = session.get("download")
    if dl:
        out.append("<h2>%s</h2>" % tr("Imagery"))
        rows = [[tr(k), str(v)] for k, v in dl.items() if k not in ("bands_desc",)]
        out.append(_table([tr("Item"), tr("Value")], rows))
    for key, title in (("supervised", tr("Supervised classification")), ("unsupervised", tr("Unsupervised classification"))):
        r = session.get(key)
        if not r:
            continue
        out.append("<h2>%s</h2>" % title)
        out.append("<p>%s: %s<br>%s: %s</p>" % (tr("Method"), html.escape(str(r.get("classifier", r.get("method", "")))),
                                                tr("Bands"), html.escape(", ".join(b.split("|")[0] for b in r["bands"]))))
        names = {c[0]: c[1] for c in classes}
        out.append(_table([tr("Class"), tr("Pixels"), tr("Area (ha)"), "%"],
                          [[names.get(v, v), n, a, p] for v, n, a, p in r["areas"]], "%.2f"))
        if "oob" in r:
            out.append("<p>%s: %.3f</p>" % (tr("Random Forest out-of-bag accuracy"), r["oob"]))
    if acc:
        names = {c[0]: c[1] for c in classes}
        labs = [names.get(v, v) for v in acc["labels"]]
        out.append("<h2>%s</h2>" % tr("Accuracy assessment"))
        rows = []
        for i, row in enumerate(acc["cm"]):
            rows.append([labs[i]] + row + [sum(row), acc["ua"][i]])
        colsum = [sum(r[j] for r in acc["cm"]) for j in range(len(labs))]
        rows.append([tr("Total")] + colsum + [sum(colsum), ""])
        rows.append([tr("Producer's acc.")] + acc["pa"] + ["", ""])
        out.append("<p>%s</p>" % tr("Rows = classified map, columns = reference."))
        out.append(_table([""] + labs + [tr("Total"), tr("User's acc.")], rows))
        out.append("<p><b>%s</b> %.2f%% &nbsp; <b>Kappa</b> %.3f</p>" % (tr("Overall accuracy"), 100 * acc["oa"], acc["kappa"]))
        adj = acc.get("adjusted")
        if adj:
            out.append("<h3>%s (Olofsson et al. 2014)</h3>" % tr("Area-adjusted estimates"))
            out.append("<p>%s: %.2f%% ± %.2f%%</p>" % (tr("Area-weighted overall accuracy"), 100 * adj["oa"],
                                                       100 * adj["oa_ci"]))
            out.append(_table([tr("Class"), tr("Mapped (ha)"), tr("Estimated (ha)"), tr("± 95% CI (ha)"),
                               tr("Producer's acc.")],
                              [[labs[i], adj["mapped_ha"][i], adj["area_ha"][i], adj["ci_ha"][i], adj["pa"][i]]
                               for i in range(len(labs))], "%.2f"))
    ch = session.get("change")
    if ch:
        out.append("<h2>%s</h2><p>%s</p>" % (tr("Change detection"), html.escape(ch.get("summary", ""))))
        if ch.get("cm"):
            import numpy as np
            names = {c[0]: c[1] for c in classes}
            labs = [str(names.get(v, v)) for v in ch["labels"]]
            ha = np.array(ch["cm"], "float64") * ch["pixel_area"] / 1e4
            out.append("<p>%s</p>" % tr("Rows = date 1 class, columns = date 2 class (ha). Diagonal = no change."))
            out.append(_table([tr("From \\ To (ha)")] + labs,
                              [[labs[i]] + [float(x) for x in r] for i, r in enumerate(ha)], "%.1f"))
            d = np.diag(ha)
            out.append("<h3>%s</h3>" % tr("Gains and losses per class"))
            out.append(_table([tr("Class"), tr("Date 1 (ha)"), tr("Date 2 (ha)"), tr("Gain"), tr("Loss"), tr("Net")],
                              [[labs[i], float(ha[i].sum()), float(ha[:, i].sum()), float(ha[:, i].sum() - d[i]),
                                float(ha[i].sum() - d[i]), float(ha[:, i].sum() - ha[i].sum())]
                               for i in range(len(labs))], "%.1f"))
    out.append("<h2>%s</h2><p>%s</p>" % (tr("Data sources"), "<br>".join(html.escape(s) for s in session.get("sources", [])) or "—"))
    out.append("<p style='color:#666; font-size:8pt'>%s</p>" % tr(
        "Disclaimer: produced with LEE RS Lab for teaching, learning and research. Results depend on the images, "
        "training data and methods chosen and must be checked before any official use."))
    out.append("</body></html>")
    return "".join(out)


def export_pdf(path, session, canvas, classes):
    printer = QPrinter(E(QPrinter, "PrinterMode.HighResolution"))
    printer.setOutputFormat(E(QPrinter, "OutputFormat.PdfFormat"))
    printer.setOutputFileName(path)
    printer.setPageSize(QPageSize(E(QPageSize, "PageSizeId.A4")))
    printer.setPageOrientation(E(QPageLayout, "Orientation.Portrait"))
    doc = QTextDocument()
    doc.setHtml(build_html(session, canvas, classes))
    rect = printer.pageLayout().paintRectPoints()
    doc.setPageSize(QSizeF(rect.width(), rect.height()))
    (getattr(doc, "print_", None) or getattr(doc, "print"))(printer)
    return path
