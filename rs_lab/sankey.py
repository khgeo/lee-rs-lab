# -*- coding: utf-8 -*-
"""Self-contained Sankey diagram of LULC change (from -> to).

Why not D3 / the Observable example directly? A QGIS plugin may be run in
the field with no internet, and pulling d3 + d3-sankey from a CDN would
break there. This computes the (bipartite, two-column) Sankey layout in
pure Python and writes ONE standalone HTML file with inline SVG - no
external scripts, no network - so it opens anywhere, including offline. A
small amount of inline CSS/JS adds hover highlighting and tooltips. The
look follows the classic d3-sankey style: class-coloured bands whose
width is proportional to transition area.

No QGIS imports here, so the builder is unit-testable on its own.
"""

import html as _html
import os
import tempfile
import webbrowser
import logging

LOG = logging.getLogger(__name__)


def _band_path(x0, y0, x1, y1, w):
    """Filled horizontal band of thickness w, (x0,y0)->(x1,y1) at the top."""
    xm = (x0 + x1) / 2.0
    return (
        "M{x0:.2f},{y0:.2f} "
        "C{xm:.2f},{y0:.2f} {xm:.2f},{y1:.2f} {x1:.2f},{y1:.2f} "
        "L{x1:.2f},{y1w:.2f} "
        "C{xm:.2f},{y1w:.2f} {xm:.2f},{y0w:.2f} {x0:.2f},{y0w:.2f} Z"
    ).format(x0=x0, y0=y0, xm=xm, x1=x1, y1=y1,
             y1w=y1 + w, y0w=y0 + w)


# Shared CSS/JS so the standalone diagram and the dashboard report render
# the Sankey identically.
SANKEY_CSS = """
  .sk svg{ width:100%; height:auto; display:block; }
  .sk .lab{ font-size:12px; fill:var(--fg); }
  .sk .hdr{ font-size:14px; font-weight:700; fill:var(--fg); }
  .sk .lnk{ transition:fill-opacity .12s; cursor:pointer; }
  .sk .nd{ stroke:rgba(0,0,0,.25); stroke-width:.5; }
  .sk-tip{ position:fixed; pointer-events:none; background:#111c;
    color:#fff; padding:5px 9px; border-radius:6px; font-size:12px;
    opacity:0; transition:opacity .1s; white-space:nowrap; z-index:50; }
"""

SANKEY_JS = """
(function(){
  var tip=document.getElementById('sk-tip');
  if(!tip){ tip=document.createElement('div'); tip.id='sk-tip';
    tip.className='sk-tip'; document.body.appendChild(tip); }
  document.querySelectorAll('.sk .lnk').forEach(function(p){
    p.addEventListener('mousemove',function(e){
      tip.textContent=p.getAttribute('data-tip');
      tip.style.left=(e.clientX+12)+'px';
      tip.style.top=(e.clientY+12)+'px'; tip.style.opacity=1;
      p.style.fillOpacity=Math.min(0.9,
        parseFloat(p.getAttribute('fill-opacity'))+0.3);
    });
    p.addEventListener('mouseleave',function(){
      tip.style.opacity=0;
      p.style.fillOpacity=p.getAttribute('fill-opacity');
    });
  });
})();
"""


def build_sankey_svg(matrix, class_defs, year_a, year_b):
    """Return just the <svg>...</svg> markup (wrap in .sk + SANKEY_CSS/JS).

    matrix[i][j] = area (ha) from class i (year_a) to class j (year_b).
    class_defs is [(code, name, hex), ...] in matrix order.
    """
    names = [n for _c, n, _h in class_defs]
    colors = ["#" + h.lstrip("#") for _c, _n, h in class_defs]
    n = len(names)

    from_tot = [sum(matrix[i][j] for j in range(n)) for i in range(n)]
    to_tot = [sum(matrix[i][j] for i in range(n)) for j in range(n)]
    total = sum(from_tot) or 1.0

    # geometry
    W, H = 920, 560
    pad_top, pad_bot = 70, 40
    pad_left, pad_right = 20, 20
    node_w = 22
    label_w = 150
    gap = 10
    avail = H - pad_top - pad_bot

    left_idx = [i for i in range(n) if from_tot[i] > 0]
    right_idx = [j for j in range(n) if to_tot[j] > 0]
    max_nodes = max(len(left_idx), len(right_idx), 1)
    scale = (avail - (max_nodes - 1) * gap) / total
    if scale <= 0:
        scale = avail / total  # degenerate: ignore gaps

    x_left = pad_left + label_w
    x_right = W - pad_right - label_w - node_w

    # node y-positions (stacked, in class order)
    def stack(idx, totals):
        ys = {}
        y = pad_top
        for i in idx:
            ys[i] = y
            y += totals[i] * scale + gap
        return ys
    left_y = stack(left_idx, from_tot)
    right_y = stack(right_idx, to_tot)

    # cumulative offsets within each node as we draw links in class order
    left_off = {i: 0.0 for i in left_idx}
    right_off = {j: 0.0 for j in right_idx}

    svg = []
    # links first (under nodes)
    for i in left_idx:
        for j in right_idx:
            a = matrix[i][j]
            if a <= 0:
                continue
            w = a * scale
            y0 = left_y[i] + left_off[i]
            y1 = right_y[j] + right_off[j]
            left_off[i] += w
            right_off[j] += w
            pct = 100.0 * a / total
            changed = " (unchanged)" if i == j else ""
            tip = "{} → {}: {:,.0f} ha ({:.1f}%){}".format(
                names[i], names[j], a, pct, changed)
            op = 0.22 if i == j else 0.5
            svg.append(
                '<path class="lnk" d="{d}" fill="{c}" fill-opacity="{op}" '
                'data-tip="{t}"><title>{t}</title></path>'.format(
                    d=_band_path(x_left + node_w, y0, x_right, y1, max(w, 0.4)),
                    c=colors[i], op=op, t=_html.escape(tip)))

    # nodes + labels
    def node_rects(idx, totals, x, side):
        for i in idx:
            h = max(totals[i] * scale, 1.0)
            y = (left_y if side == "L" else right_y)[i]
            svg.append(
                '<rect class="nd" x="{x}" y="{y:.2f}" width="{w}" '
                'height="{h:.2f}" fill="{c}" rx="2">'
                '<title>{nm}: {a:,.0f} ha</title></rect>'.format(
                    x=x, y=y, w=node_w, h=h, c=colors[i],
                    nm=_html.escape(names[i]), a=totals[i]))
            lab = "{} · {:,.0f} ha".format(names[i], totals[i])
            if side == "L":
                svg.append(
                    '<text class="lab" x="{tx}" y="{ty:.2f}" '
                    'text-anchor="end">{t}</text>'.format(
                        tx=x - 6, ty=y + h / 2 + 4, t=_html.escape(lab)))
            else:
                svg.append(
                    '<text class="lab" x="{tx}" y="{ty:.2f}" '
                    'text-anchor="start">{t}</text>'.format(
                        tx=x + node_w + 6, ty=y + h / 2 + 4,
                        t=_html.escape(lab)))
    node_rects(left_idx, from_tot, x_left, "L")
    node_rects(right_idx, to_tot, x_right, "R")

    # column headers
    svg.append('<text class="hdr" x="{x}" y="46" text-anchor="middle">'
               '{y}</text>'.format(x=x_left + node_w / 2, y=year_a))
    svg.append('<text class="hdr" x="{x}" y="46" text-anchor="middle">'
               '{y}</text>'.format(x=x_right + node_w / 2, y=year_b))

    ttl = "LULC change: {} → {}".format(year_a, year_b)
    return ('<svg viewBox="0 0 {W} {H}" role="img" aria-label="{ttl}">\n'
            '{body}\n</svg>').format(
                W=W, H=H, ttl=_html.escape(ttl), body="\n".join(svg))


def build_sankey_html(matrix, class_defs, year_a, year_b,
                      title=None, subtitle=None):
    """Return a complete standalone HTML document as a string."""
    n = len(class_defs)
    total = sum(sum(row) for row in matrix) or 1.0
    changed = total - sum(matrix[i][i] for i in range(n))
    ttl = title or "LULC change: {} → {}".format(year_a, year_b)
    sub = subtitle or "{:,.0f} ha total · {:,.0f} ha changed " \
        "({:.1f}%)".format(total, changed, 100.0 * changed / total)
    svg = build_sankey_svg(matrix, class_defs, year_a, year_b)
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ttl}</title>
<style>
  :root{{ --bg:#ffffff; --fg:#1b2430; --muted:#5b6672; }}
  @media (prefers-color-scheme: dark){{
    :root:not([data-theme="light"]){{ --bg:#141a20; --fg:#e8edf2;
      --muted:#9aa6b2; }} }}
  html,body{{ margin:0; background:var(--bg); color:var(--fg);
    font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  .wrap{{ max-width:960px; margin:0 auto; padding:16px; }}
  h1{{ font-size:20px; margin:4px 0 2px; }}
  .sub{{ color:var(--muted); font-size:13px; margin-bottom:10px; }}
  .foot{{ color:var(--muted); font-size:11px; margin-top:8px; }}
  {css}
</style></head><body><div class="wrap">
<h1>{ttl}</h1><div class="sub">{sub}</div>
<div class="sk">{svg}</div>
<div class="foot">Band width is proportional to area. Faint straight bands
are land that kept its class; coloured diagonals are change. Hover a band
for the exact area.</div>
</div><div class="sk-tip" id="sk-tip"></div>
<script>{js}</script>
</body></html>""".format(ttl=_html.escape(ttl), sub=_html.escape(sub),
                         css=SANKEY_CSS, js=SANKEY_JS, svg=svg)


def write_sankey_html(matrix, class_defs, year_a, year_b, path=None,
                      title=None, subtitle=None, open_browser=True):
    """Write the Sankey HTML to `path` (temp file if None). Returns the path."""
    doc = build_sankey_html(matrix, class_defs, year_a, year_b,
                            title=title, subtitle=subtitle)
    if path is None:
        fd, path = tempfile.mkstemp(
            prefix="lulc_sankey_{}_{}_".format(year_a, year_b), suffix=".html")
        os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    if open_browser:
        try:
            webbrowser.open("file://" + os.path.abspath(path))
        except (OSError, RuntimeError, webbrowser.Error):  # headless, etc.
            LOG.debug("ignored error", exc_info=True)
    return path
