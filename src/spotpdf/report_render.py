"""PDFium geometry and original-page previews, used only inside a report worker."""

from __future__ import annotations

import base64
import ctypes
import io
import math
from contextlib import closing

import pypdfium2 as pdfium
import pypdfium2.raw as raw


def png(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def marked_bounds(page, trace):
    matches = {}
    for obj in page.get_objects(max_depth=65):
        if obj.type == raw.FPDF_PAGEOBJ_TEXT and raw.FPDFTextObj_GetTextRenderMode(obj) in (3, 7):
            continue
        for index in range(raw.FPDFPageObj_CountMarks(obj)):
            mark = raw.FPDFPageObj_GetMark(obj, index)
            value = ctypes.c_int()
            if not raw.FPDFPageObjMark_GetParamIntValue(mark, trace.tag_key.encode(), value):
                continue
            if value.value not in trace.entries:
                continue
            left, bottom, right, top = obj.get_bounds()
            points = [(left, bottom), (left, top), (right, bottom), (right, top)]
            parent = obj.container
            while parent is not None:
                matrix = parent.get_matrix()
                points = [matrix.on_point(x, y) for x, y in points]
                parent = parent.container
            box = [
                min(p[0] for p in points),
                min(p[1] for p in points),
                max(p[0] for p in points),
                max(p[1] for p in points),
            ]
            if all(math.isfinite(v) for v in box):
                matches.setdefault(value.value, []).append(box)
    return matches


def matches_finding(finding, entry):
    for occurrence in finding.occurrences:
        if occurrence.get("location") in {
            entry["location"],
            entry.get("legacy_location"),
        } and occurrence.get("sequence_index") == entry.get("sequence_index"):
            return True
    refs = set(entry["references"])
    if any(
        f"{o.get('location')}#instruction{o.get('sequence_index')}" in refs
        for o in finding.occurrences
        if "sequence_index" in o
    ):
        return True
    keys = {finding.object_id, finding.location}
    for occurrence in finding.occurrences:
        keys.update((occurrence.get("object_id"), occurrence.get("location")))
    return bool(refs.intersection(keys - {None, "document"}))


def render(original, annotated, trace, findings, pages, gaps, max_bytes):
    previews = []
    total = 0
    with pdfium.PdfDocument(str(original)) as source, pdfium.PdfDocument(str(annotated)) as copy:
        for number in pages:
            try:
                with closing(source[number - 1]) as page, closing(copy[number - 1]) as marked:
                    scale = 1600 / max(page.get_size())
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil().copy()
                        converter = bitmap.get_posconv(page)
                        comparison = marked.render(scale=scale)
                        try:
                            comparable = comparison.to_pil()
                            same_render = (
                                comparable.size == image.size
                                and comparable.tobytes() == image.tobytes()
                            )
                        finally:
                            comparison.close()
                        if same_render:
                            geometry = marked_bounds(marked, trace)
                        else:
                            geometry = {}
                            gaps.append(
                                f"Page {number}: diagnostic copy render differs; "
                                "operation geometry omitted"
                            )
                        boxes = []
                        for finding in findings:
                            for occurrence in finding.occurrences:
                                if (
                                    occurrence.get("page") == number
                                    and "bbox" in occurrence
                                    and occurrence.get("accuracy") == "surrounding area"
                                ):
                                    rectangle = occurrence["bbox"]
                                    if all(math.isfinite(v) for v in rectangle):
                                        mark = -len(trace.entries) - 1
                                        geometry[mark] = [rectangle]
                                        trace.entries[mark] = {
                                            "page": number,
                                            "location": occurrence["location"],
                                            "references": [finding.object_id, finding.location],
                                            "accuracy": "surrounding area",
                                        }
                        for mark, rectangles in geometry.items():
                            entry = trace.entries[mark]
                            for finding_index, finding in enumerate(findings, 1):
                                if not matches_finding(finding, entry):
                                    continue
                                # Multiple renderer fragments belong to the same source operation.
                                rect = [
                                    min(r[0] for r in rectangles),
                                    min(r[1] for r in rectangles),
                                    max(r[2] for r in rectangles),
                                    max(r[3] for r in rectangles),
                                ]
                                corners = [
                                    converter.to_bitmap(x, y)
                                    for x, y in [(rect[0], rect[1]), (rect[2], rect[3])]
                                ]
                                x0, x1 = sorted(c[0] for c in corners)
                                y0, y1 = sorted(c[1] for c in corners)
                                x0, y0 = max(0, x0), max(0, y0)
                                x1, y1 = min(image.width, x1), min(image.height, y1)
                                if x1 <= x0 or y1 <= y0:
                                    continue
                                occurrence = {
                                    k: v
                                    for k, v in entry.items()
                                    if k not in {"references", "legacy_location"}
                                }
                                if "form_chain" in occurrence:
                                    occurrence["form_chain"] = [
                                        {k: v for k, v in call.items() if k != "key"}
                                        for call in occurrence["form_chain"]
                                    ]
                                occurrence.update(
                                    bbox=rect,
                                    accuracy=entry.get("accuracy", "object bounds"),
                                    preview_page=number,
                                )
                                if occurrence not in finding.occurrences:
                                    finding.occurrences.append(occurrence)
                                boxes.append(
                                    {
                                        "finding": finding_index,
                                        "box": [x0, y0, x1, y1],
                                        "accuracy": occurrence["accuracy"],
                                    }
                                )
                        encoded = png(image)
                        total += len(encoded)
                        if total > max_bytes // 2:
                            gaps.append("Preview byte budget reached; remaining images omitted")
                            break
                        previews.append(
                            {
                                "page": number,
                                "width": image.width,
                                "height": image.height,
                                "png": encoded,
                                "boxes": boxes,
                            }
                        )
                    finally:
                        bitmap.close()
            except Exception as error:
                gaps.append(f"Page {number}: rendering unavailable: {error}")
    return previews
