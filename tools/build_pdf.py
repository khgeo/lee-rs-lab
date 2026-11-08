#!/usr/bin/env python3
"""Build docs/downloads/lee-rs-lab-manual-km.pdf from the Markdown manual.

    pip install "mkdocs>=1.6,<2" pymdown-extensions playwright pypdf && playwright install chromium
    python tools/build_pdf.py

Page 1 (the designed cover) is taken from tools/cover.pdf; the rest is rendered from docs/index.md and
docs/lessons/*.md with the manual's own Khmer fonts (Siemreap text, Battambang bold) in Chromium, which
shapes Khmer correctly. Each lesson starts on a new page; the footer reads "LEE RS Lab · n / N".
"""
import html
import os
import re
import sys
import tempfile

import markdown
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
OUT = os.path.join(DOCS, "downloads", "lee-rs-lab-manual-km.pdf")
COVER = os.path.join(ROOT, "tools", "cover.pdf")
FONTS = os.path.join(DOCS, "assets", "fonts")

EXT = ["admonition", "attr_list", "md_in_html", "tables", "pymdownx.details", "pymdownx.superfences"]

CSS = """
@font-face{font-family:'Siemreap';src:url('file://%(f)s/Siemreap.ttf')}
@font-face{font-family:'Battambang';font-weight:400;src:url('file://%(f)s/Battambang-Regular.ttf')}
@font-face{font-family:'Battambang';font-weight:700;src:url('file://%(f)s/Battambang-Bold.ttf')}
@page{size:A4;margin:16mm 16mm 18mm 16mm;
  @bottom-right{content:"LEE RS Lab · " counter(page) " / " counter(pages);
    font-family:'Battambang',sans-serif;font-size:7.5pt;color:#7a8a96}}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{font-family:'Siemreap','Battambang',sans-serif;font-size:10pt;line-height:1.75;color:#1d2a33;margin:0}
b,strong,h1,h2,h3,h4,th{font-family:'Battambang',sans-serif;font-weight:700}
h1{color:#0f7c87;font-size:17pt;line-height:1.5;border-bottom:2px solid #0f8a96;padding-bottom:4px;
   margin:0 0 10px;break-after:avoid}
h2{color:#14535c;font-size:12.5pt;margin:16px 0 6px;break-after:avoid}
h3{color:#14535c;font-size:11pt;margin:12px 0 4px;break-after:avoid}
p{margin:5px 0}
ul,ol{margin:4px 0 6px;padding-left:22px}
li{margin:1px 0}
a{color:#0f6f7a;text-decoration:none}
code{font-family:'DejaVu Sans Mono',monospace;font-size:8.5pt;background:#eef3f5;padding:0 3px;border-radius:3px}
pre{background:#eef3f5;padding:8px 10px;border-radius:4px;white-space:pre-wrap;font-size:8.3pt;line-height:1.45;
    break-inside:avoid}
pre code{background:none;padding:0}
img{display:block;margin:8px auto 2px;max-width:100%;max-height:118mm;border-radius:3px;
    box-shadow:0 0 0 .5px #b9c6ce;break-inside:avoid}
p:has(> em:only-child){text-align:center;color:#5d6d78;font-size:8.3pt;margin:0 0 10px}
p:has(> em:only-child) em{font-style:normal}
table{border-collapse:collapse;margin:8px 0;font-size:8.8pt;width:auto;break-inside:avoid}
th,td{border:1px solid #c7d6df;padding:3px 7px;vertical-align:top}
th{background:#e4f1f3}
blockquote{margin:8px 0;padding:6px 12px;border-left:4px solid #0f8a96;background:#f1f8f9;color:#2b3d47}
.admonition,details{margin:10px 0;padding:6px 12px 8px;border-left:4px solid #0f8a96;background:#f1f8f9;
    border-radius:3px;break-inside:avoid}
.admonition.tip{border-color:#00a86b;background:#eefaf4}
.admonition.note{border-color:#3478c6;background:#eef4fb}
.admonition.warning{border-color:#e08a00;background:#fff6e8}
.admonition-title{font-family:'Battambang',sans-serif;font-weight:700;margin:2px 0 4px}
hr{display:none}
.lesson{break-before:page}
.toc h1{margin-top:0}
.toc ol{font-size:10.5pt;line-height:1.95}
.byline{color:#4a5b66}
"""


def _md(text, base_dir):
    h = markdown.markdown(text, extensions=EXT)

    def fix(m):
        src = m.group(1)
        if re.match(r"^[a-z]+://", src):
            return m.group(0)
        return 'src="file://%s"' % os.path.normpath(os.path.join(base_dir, src))
    return re.sub(r'src="([^"]+)"', fix, h)


def _clean_lesson(text):
    # drop the "← lesson n · lesson n+1 →" footer and its rule
    text = re.sub(r"\n---\s*\n+\[←.*$", "\n", text, flags=re.S)
    text = re.sub(r"\n\[←[^\n]*$", "\n", text)
    # mkdocs-only image attributes are fine for attr_list; lazy loading is irrelevant in print
    return text.replace("{ loading=lazy }", "")


def _clean_index(text):
    text = re.sub(r"^!\[[^\]]*\]\(assets/img/cover\.jpg\)\{[^}]*\}\s*$", "", text, flags=re.M)
    # the web contents list is replaced by the one at the top of the PDF
    text = re.sub(r"\n## មាតិកា\n.*$", "\n", text, flags=re.S)
    return text


def build(out=OUT):
    with open(os.path.join(ROOT, "mkdocs.yml"), encoding="utf-8") as f:
        cfg = yaml.load(f, Loader=yaml.BaseLoader)
    nav = cfg["nav"]
    lessons = [(list(d.keys())[0], list(d.values())[0]) for d in nav if "lessons/" in list(d.values())[0]]
    parts = ["<section class='toc'><h1>មាតិកា</h1><ol>"]
    parts += ["<li>%s</li>" % html.escape(t) for t, _ in lessons]
    parts.append("</ol></section>")
    with open(os.path.join(DOCS, "index.md"), encoding="utf-8") as f:
        idx = _clean_index(f.read())
    parts.append("<section class='intro'>%s</section>" % _md(idx, DOCS))
    for _, rel in lessons:
        p = os.path.join(DOCS, rel)
        with open(p, encoding="utf-8") as f:
            parts.append("<section class='lesson'>%s</section>" % _md(_clean_lesson(f.read()), os.path.dirname(p)))
    page = ("<!doctype html><html lang='km'><head><meta charset='utf-8'><style>%s</style></head><body>%s"
            "</body></html>") % (CSS.replace("%(f)s", FONTS), "\n".join(parts))

    tmp = tempfile.mkdtemp(prefix="leepdf_")
    html_path = os.path.join(tmp, "manual.html")
    body_pdf = os.path.join(tmp, "body.pdf")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.goto("file://" + html_path, wait_until="load")
        pg.evaluate("document.fonts.ready")
        pg.pdf(path=body_pdf, format="A4", print_background=True, prefer_css_page_size=True)
        b.close()

    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    if os.path.exists(COVER):
        w.append(PdfReader(COVER))
    w.append(PdfReader(body_pdf))
    w.add_metadata({"/Title": "សៀវភៅណែនាំ LEE RS Lab", "/Author": "YAM Sarath (យាំ សារដ្ឋ)",
                    "/Subject": "LEE RS Lab — ការយកព័ត៌មានពីចម្ងាយក្នុង QGIS ដោយមិនចាំបាច់មានគណនី"})
    w.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        w.write(f)
    return out, html_path


if __name__ == "__main__":
    path, src = build(sys.argv[1] if len(sys.argv) > 1 else OUT)
    print("Wrote", path, "(HTML:", src + ")")
