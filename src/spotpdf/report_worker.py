"""Killable report subprocess; communicate only JSON and private files."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from .diagnostics import Finding
from .limits import ProcessingLimits
from .publication import open_strict
from .report_collect import collect, consolidate
from .report_html import document


def generate(request, directory):
    findings = [Finding(**f) for f in request["findings"]]
    gaps = list(request.get("gaps", []))
    previews = []
    # Persist a usable technical fallback before any native work.
    write_result(request, directory, findings, gaps, previews)
    if not request.get("skip_input"):
        try:
            path = Path(request["input"])
            before = path.stat()
            signature = [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns]
            if signature != request.get("source_signature"):
                raise ValueError("Input changed since the operation; localization omitted")
            maximum = request["limits"]["max_input_bytes"]
            if maximum is not None and before.st_size > maximum:
                raise ValueError("Input exceeds the processing byte limit")
            snapshot = directory / "original.pdf"
            shutil.copyfile(path, snapshot)
            after = path.stat()
            if signature != [after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns]:
                raise ValueError("Input changed while creating diagnostic snapshot")
            request = dict(request, input=str(snapshot))
            with open_strict(request["input"], limits=ProcessingLimits(**request["limits"])) as pdf:
                inventory = collect(pdf, request, findings, gaps) if request["failed"] else None
                if inventory is not None:
                    if len(findings) > request["max_findings"]:
                        gaps.append("Finding limit reached")
                    findings = consolidate(findings, inventory, request["max_findings"])
                from .report_locations import resolve_locations

                resolve_locations(pdf, findings)
                write_result(request, directory, findings, gaps, previews)
                if findings:
                    from .report_render import render
                    from .report_trace import Trace

                    hinted = set()
                    for finding in findings:
                        locations = [finding.location or ""] + [
                            o.get("location", "") for o in finding.occurrences
                        ]
                        for location in locations:
                            match = re.match(r"page (\d+)(?:\b)", location)
                            if match:
                                hinted.add(int(match[1]))
                    # Include all pages to discover other invocations of shared resources.
                    candidates = sorted(hinted) + [
                        p for p in range(1, len(pdf.pages) + 1) if p not in hinted
                    ]
                    pages = candidates[: request["max_pages"]]
                    if len(candidates) > len(pages):
                        gaps.append("Page preview limit reached; remaining uses were not localized")
                    trace = Trace(
                        max_operations=min(request["limits"].get("max_operators") or 100000, 100000)
                    )
                    trace.instrument(pdf, pages)
                    gaps.extend(trace.gaps)
                    annotated = directory / "annotated.pdf"
                    pdf.save(annotated)
                    previews = render(
                        request["input"],
                        annotated,
                        trace,
                        findings,
                        pages,
                        gaps,
                        request["max_bytes"],
                    )
        except Exception as error:
            gaps.append(f"Diagnostic analysis unavailable: {error}")
    write_result(request, directory, findings, gaps, previews)


def write_result(request, directory, findings, gaps, previews):
    outcome = (
        "Operation failed"
        if request["failed"]
        else "Dry run verified; no output PDF published"
        if request["dry_run"]
        else "Operation completed; output PDF published"
    )
    content = document(request, findings, gaps, previews, outcome)
    if len(content) > request["max_bytes"]:
        gaps.append("HTML byte limit reached; previews omitted")
        content = document(request, findings, gaps, [], outcome)
    while len(content) > request["max_bytes"] and len(findings) > 1:
        findings = findings[:-1]
        if "HTML byte limit reached; findings truncated" not in gaps:
            gaps.append("HTML byte limit reached; findings truncated")
        content = document(request, findings, gaps, [], outcome)
    if len(content) > request["max_bytes"]:
        raise ValueError("Report byte limit too small for the technical report")
    destination = directory / "report.html"
    staging = directory / "report.tmp"
    staging.write_bytes(content)
    staging.replace(destination)
    metadata = {
        "status": "partial" if gaps else "complete",
        "gaps": list(dict.fromkeys(gaps)),
        "findings": [f.wire() for f in findings],
    }
    staging = directory / "metadata.tmp"
    staging.write_text(json.dumps(metadata), encoding="utf-8")
    staging.replace(directory / "metadata.json")


def main():
    directory = Path(sys.argv[1])
    request = json.loads((directory / "request.json").read_text())
    generate(request, directory)


if __name__ == "__main__":
    main()
