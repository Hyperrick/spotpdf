"""Self-contained HTML presentation of bounded diagnostic data."""

from __future__ import annotations

import html
import json

STYLE = """
:root{color-scheme:light;font:16px system-ui,sans-serif;color:#172337;background:#edf1f5}
body{max-width:1160px;margin:36px auto;padding:0 24px}h1{font-size:32px;margin-bottom:8px}
h2{font-size:21px}p{line-height:1.55}.muted{color:#55647a}.badge{background:#e7edf5;
padding:4px 9px;border-radius:6px;font-size:13px}.primary{background:#ffe2dc;color:#952c18}
article,section{background:white;padding:24px;margin:20px 0;border-radius:14px;
box-shadow:0 2px 8px #152a4010}input{padding:12px;border:1px solid #b5c2d2;border-radius:8px;
font:inherit;width:min(90%,600px)}pre{white-space:pre-wrap;overflow-wrap:anywhere;
background:#f4f6f9;padding:14px;border-radius:6px;font-size:13px}details{margin-top:16px}
a{color:#125bb2}summary{cursor:pointer}.page{position:relative;max-width:850px;margin:auto}
.page>svg{width:100%;display:block}.mark{position:absolute;border:2px solid #d83921;
box-sizing:border-box;background:#f7471920;min-width:3px;min-height:3px}.mark span{
position:absolute;background:#b82a16;color:white;font:bold 12px system-ui;padding:2px 5px;
bottom:100%;left:-2px}.crop{width:100%;height:240px;max-width:100%;overflow:hidden;margin-top:12px;
border:1px solid #cbd4df;border-radius:8px;background-repeat:no-repeat}.warning{
border-left:4px solid #bd6805}li{margin:8px 0;overflow-wrap:anywhere}h3{overflow-wrap:anywhere}
[hidden]{display:none!important}@media(max-width:600px){body{padding:0 12px}article,section{
padding:16px}h1{font-size:26px}}@media print{input{display:none}article{break-inside:avoid}}
"""
SCRIPT = """
const query=document.querySelector('input');
query.addEventListener('input',()=>{const q=query.value.toLowerCase();
document.querySelectorAll('article').forEach(el=>el.hidden=!el.textContent.toLowerCase().includes(q));
document.querySelectorAll('.mark').forEach(el=>{
const target=document.getElementById(el.getAttribute('href').slice(1));
el.hidden=target.hidden;});});
"""


def escape(value):
    return html.escape(str(value), quote=True)


def document(request, findings, gaps, previews, outcome):
    parts = [
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'\">",
        "<title>spotpdf diagnostic report</title><style>",
        STYLE,
        "</style><body>",
        '<header><p class="muted">SPOTPDF / DIAGNOSTICS</p><h1>Locate the problem.</h1>',
        f"<p>{escape(request['input_name'])} · {escape(request['command'])}</p>",
        f'<p class="badge">{escape(outcome)}</p>',
        '<p class="muted">Original-page previews are orientation aids, not color proofs. '
        "Object bounds may include clipped or occluded content.</p></header>",
        '<input aria-label="Filter findings" placeholder="Filter by spot, object or reason…">',
    ]
    parameters = {
        key: request[key]
        for key in ("spot", "all_spots", "destination", "cmyk", "to_cmyk", "dry_run")
        if request.get(key) is not None
    }
    parts.append(
        "<details><summary>Requested operation</summary><pre>"
        + escape(json.dumps(parameters, indent=2))
        + "</pre></details>"
    )
    if gaps:
        parts.append('<section class="warning"><h2>Incomplete diagnostic coverage</h2><ul>')
        parts.extend(f"<li>{escape(gap)}</li>" for gap in dict.fromkeys(gaps))
        parts.append("</ul></section>")
    if not findings and not request.get("failed"):
        parts.append(
            "<section><h2>No failure reported</h2><p>The requested operation "
            "completed successfully.</p></section>"
        )
    for index, finding in enumerate(findings, 1):
        parts.append(
            f'<article id="finding-{index}"><span class="badge '
            f'{"primary" if finding.primary else ""}">'
            f"{'Operation failure' if finding.primary else 'Additional finding'}</span>"
            f"<h2>{index}. {escape(finding.code)}</h2><h3>{escape(finding.message)}</h3>"
            f"<p>Spot: {escape(', '.join(finding.spots) or 'Not applicable')}</p>"
            f"<p>Object: {escape(finding.object_id or 'Direct / see locations')}</p>"
        )
        visual = False
        for preview in previews:
            for box in preview["boxes"]:
                if box["finding"] != index:
                    continue
                visual = True
                x0, y0, x1, y1 = box["box"]
                parts.append(
                    f'<p><a href="#page-{preview["page"]}">Page {preview["page"]}</a>'
                    f" · {escape(box.get('accuracy', 'object bounds'))} · original-page excerpt</p>"
                    f'<svg class="crop" role="img" aria-label="Original-page excerpt" '
                    f'viewBox="{x0 - 8} {y0 - 8} {x1 - x0 + 16} {y1 - y0 + 16}" '
                    f'preserveAspectRatio="xMinYMid meet">'
                    f'<use href="#raster-{preview["page"]}"/></svg>'
                )
        if not visual:
            pages = sorted({o["page"] for o in finding.occurrences if o.get("page")})
            if pages:
                parts.append(
                    '<p class="muted">Page only; no reliable object bounds. Pages: '
                    + ", ".join(str(page) for page in pages)
                    + "</p>"
                )
            else:
                parts.append(
                    '<p class="muted">Structural location only; no reliable visible '
                    "object bounds available.</p>"
                )
        parts.append(
            "<details><summary>Technical locations</summary><pre>"
            + escape(json.dumps(finding.wire(), indent=2, ensure_ascii=True))
            + "</pre></details></article>"
        )
    for preview in previews:
        parts.append(
            f'<section id="page-{preview["page"]}"><h2>Page {preview["page"]}</h2>'
            f'<div class="page"><svg viewBox="0 0 {preview["width"]} {preview["height"]}" '
            f'role="img" aria-label="Original page {preview["page"]}">'
            f'<image id="raster-{preview["page"]}" width="{preview["width"]}" '
            f'height="{preview["height"]}" href="data:image/png;base64,{preview["png"]}"/>'
            "</svg>"
        )
        for item in preview["boxes"]:
            x0, y0, x1, y1 = item["box"]
            w, h = preview["width"] / 100, preview["height"] / 100
            parts.append(
                f'<a class="mark" href="#finding-{item["finding"]}" '
                f'aria-label="Finding {item["finding"]}" '
                f'style="left:{x0 / w}%;top:{y0 / h}%;'
                f'width:{(x1 - x0) / w}%;height:{(y1 - y0) / h}%">'
                f"<span>{item['finding']}</span></a>"
            )
        parts.append("</div></section>")
    parts.extend(["<script>", SCRIPT, "</script></body></html>"])
    return "".join(parts).encode("utf-8")
