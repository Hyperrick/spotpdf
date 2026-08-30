#!/usr/bin/env python3
"""Measure the synthetic 64/128-spot single-pass inventory contract."""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import tempfile
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pikepdf

from spotpdf.document import inspect_pdf
from spotpdf.inspection import enrich_inspection_report
from spotpdf.inventory import discover_spot_declarations
from spotpdf.objects import object_key
from spotpdf.publication import open_strict

PAGE_COUNT = 8
SPOT_COUNTS = (64, 128)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Benchmark inventory over synthetic 64/128-spot PDFs without committing PDFs.")
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=7,
        help="timing and tracemalloc samples per size (default: 7)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="also write the JSON result to this path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")

    with tempfile.TemporaryDirectory(prefix="spotpdf-inventory-benchmark-") as directory:
        root = Path(directory)
        sources = {count: create_benchmark_pdf(root, count) for count in SPOT_COUNTS}
        measurements = {
            str(count): measure_inventory(sources[count], count, args.runs) for count in SPOT_COUNTS
        }

    peak_64 = measurements["64"]["tracemalloc_peak_bytes"]["max"]
    peak_128 = measurements["128"]["tracemalloc_peak_bytes"]["max"]
    memory_limit = int(2.25 * peak_64 + 256 * 1024)
    memory_ok = peak_128 <= memory_limit
    result = {
        "schema_version": 1,
        "runtime": {
            "python": platform.python_version(),
            "pikepdf": pikepdf.__version__,
            "platform": platform.platform(),
        },
        "fixture": {
            "pages": PAGE_COUNT,
            "forms": PAGE_COUNT,
            "spot_counts": list(SPOT_COUNTS),
        },
        "measurements": measurements,
        "checks": {
            "single_pass_structure": True,
            "memory_growth_ok": memory_ok,
            "peak_128_limit_bytes": memory_limit,
        },
    }
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(serialized, end="")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    return 0 if memory_ok else 1


def measure_inventory(path: Path, spot_count: int, runs: int) -> dict[str, object]:
    metrics = structural_metrics(path, spot_count)
    inspect_pdf(path)

    timings: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        inspect_pdf(path)
        timings.append(time.perf_counter() - start)

    peaks: list[int] = []
    for _ in range(runs):
        gc.collect()
        tracemalloc.start()
        inspect_pdf(path)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)

    return {
        "metrics": metrics,
        "timing_seconds": _samples(timings),
        "tracemalloc_peak_bytes": _samples(peaks),
    }


def structural_metrics(path: Path, spot_count: int) -> dict[str, int]:
    with open_strict(path) as pdf:
        original_parse = pikepdf.parse_content_stream
        parsed_objects: list[tuple[object, ...]] = []

        def counted_parse(value, *args, **kwargs):
            source = value.obj if isinstance(value, pikepdf.Page) else value
            parsed_objects.append(object_key(source))
            return original_parse(value, *args, **kwargs)

        with patch.object(pikepdf, "parse_content_stream", side_effect=counted_parse):
            report = discover_spot_declarations(pdf)
            metrics = enrich_inspection_report(pdf, report)

    parse_counts = Counter(parsed_objects)

    expected_instructions = 4 * spot_count + 2 * PAGE_COUNT
    expected = {
        "resource_contexts_scanned": PAGE_COUNT * 2,
        "page_streams_parsed": PAGE_COUNT,
        "form_streams_parsed": PAGE_COUNT,
        "streams_parsed": PAGE_COUNT * 2,
        "actual_parse_calls": PAGE_COUNT * 2,
        "unique_parse_objects": PAGE_COUNT * 2,
        "max_parses_per_object": 1,
        "instructions_visited": expected_instructions,
    }
    actual = {
        "resource_contexts_scanned": metrics.resource_contexts_scanned,
        "page_streams_parsed": metrics.page_streams_parsed,
        "form_streams_parsed": metrics.form_streams_parsed,
        "streams_parsed": metrics.streams_parsed,
        "actual_parse_calls": len(parsed_objects),
        "unique_parse_objects": len(parse_counts),
        "max_parses_per_object": max(parse_counts.values(), default=0),
        "instructions_visited": metrics.instructions_visited,
    }
    if actual != expected:
        raise RuntimeError(f"single-pass work mismatch: expected {expected}, got {actual}")
    if len(report.spots) != spot_count:
        raise RuntimeError(f"expected {spot_count} spots, got {len(report.spots)}")
    for index in range(spot_count):
        summary = report.spots[f"BenchmarkSpot{index:03d}"]
        expected_page = {index % PAGE_COUNT + 1}
        if (
            summary.pages != expected_page
            or summary.paint_operations != 1
            or summary.contexts != {"painted"}
        ):
            raise RuntimeError(f"incorrect attribution for {summary.name!r}")
    return actual


def create_benchmark_pdf(root: Path, spot_count: int) -> Path:
    path = root / f"inventory-{spot_count}.pdf"
    with pikepdf.Pdf.new() as pdf:
        for page_index in range(PAGE_COUNT):
            indices = list(range(page_index, spot_count, PAGE_COUNT))
            split = len(indices) // 2
            direct_spaces, direct_content = _spot_stream(indices[:split], "D")
            form_spaces, form_content = _spot_stream(indices[split:], "F")
            form = _form(pdf, form_content, form_spaces)

            page = pdf.add_blank_page(page_size=(200, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=direct_spaces,
                XObject=pikepdf.Dictionary(Paint=form),
            )
            page.Contents = pdf.make_stream(direct_content + b"0 g /Paint Do\n")
        pdf.save(path)
    return path


def _spot_stream(indices: list[int], prefix: str) -> tuple[pikepdf.Dictionary, bytes]:
    spaces = pikepdf.Dictionary()
    content = bytearray()
    for index in indices:
        alias = f"{prefix}{index:03d}"
        spaces[pikepdf.Name(f"/{alias}")] = _separation(f"BenchmarkSpot{index:03d}")
        content.extend(f"/{alias} cs 1 scn {index} 0 1 1 re f\n".encode())
    return spaces, bytes(content)


def _form(
    pdf: pikepdf.Pdf,
    content: bytes,
    spaces: pikepdf.Dictionary,
) -> pikepdf.Stream:
    form = pdf.make_stream(content)
    form.Type = pikepdf.Name.XObject
    form.Subtype = pikepdf.Name.Form
    form.BBox = pikepdf.Array([0, 0, 200, 100])
    form.Resources = pikepdf.Dictionary(ColorSpace=spaces)
    return form


def _separation(name: str) -> pikepdf.Array:
    return pikepdf.Array(
        [
            pikepdf.Name.Separation,
            pikepdf.Name(f"/{name}"),
            pikepdf.Name.DeviceCMYK,
            pikepdf.Dictionary(
                FunctionType=2,
                Domain=pikepdf.Array([0, 1]),
                C0=pikepdf.Array([0, 0, 0, 0]),
                C1=pikepdf.Array([1, 0, 1, 0]),
                N=1,
            ),
        ]
    )


def _samples(values: list[float] | list[int]) -> dict[str, float | int | list[float] | list[int]]:
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "samples": values,
    }


if __name__ == "__main__":
    raise SystemExit(main())
