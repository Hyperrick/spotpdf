"""Stable machine output and backward-compatible text rendering for the CLI."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Final, Literal, TextIO

import pikepdf

from .limits import ProcessingBudgetExceeded
from .model import (
    AlternateResult,
    BatchRemovalResult,
    ConversionResult,
    InspectionReport,
    InvalidPdfError,
    NestingLimitExceededError,
    RemovalStats,
    RenameResult,
    SpotPdfError,
    UnsupportedSpotUseError,
    __version__,
)

OutputFormat = Literal["text", "json"]

JSON_FORMAT: Final = "json"
TEXT_FORMAT: Final = "text"
SCHEMA_VERSION: Final = "spotpdf.cli/v1"
RUNTIME_ERROR_EXIT: Final = 1
CHECK_PRESENT_EXIT: Final = 2
USAGE_ERROR_EXIT: Final = 64


def emit_list(report: InspectionReport, input_path: Path, output_format: OutputFormat) -> None:
    """Render one inventory in the requested format."""

    if output_format == TEXT_FORMAT:
        _print_report(report)
        return
    colorants = []
    for name in sorted(report.colorants, key=lambda item: (item.casefold(), item)):
        summary = report.colorants[name]
        colorants.append(
            {
                "name": name,
                "roles": sorted(role.value for role in summary.roles),
                "kinds": sorted(kind.value for kind in summary.kinds),
                "pages": sorted(summary.pages),
                "paint_operations": summary.paint_operations,
                "contexts": sorted(summary.contexts),
            }
        )
    _emit_success(
        "list",
        0,
        {
            "input": str(input_path),
            "colorant_count": len(colorants),
            "colorants": colorants,
        },
    )


def emit_check(input_path: Path, spot: str, present: bool, output_format: OutputFormat) -> None:
    """Render the exact-name predicate result."""

    exit_code = CHECK_PRESENT_EXIT if present else 0
    if output_format == TEXT_FORMAT:
        print(f"{spot}: {'present' if present else 'absent'}")
        return
    _emit_success(
        "check",
        exit_code,
        {"input": str(input_path), "spot": spot, "present": present},
    )


def emit_remove_spot(
    input_path: Path,
    output_path: Path | None,
    spot: str,
    stats: RemovalStats,
    output_format: OutputFormat,
) -> None:
    """Render one exact-name removal result."""

    if output_format == TEXT_FORMAT:
        if output_path is None:
            print(
                f"Dry run verified removal of {spot!r}: {_stats_text(stats)}; no output published"
            )
        else:
            print(f"Removed {spot!r}: {_stats_text(stats)}; output: {output_path}")
        return
    result = {
        "selection": {"mode": "spot", "spot": spot},
        "stats": _removal_stats(stats),
    }
    result.update(_mutation_paths(input_path, output_path))
    _emit_success(
        "remove",
        0,
        result,
    )


def emit_remove_all(
    input_path: Path,
    output_path: Path | None,
    result: BatchRemovalResult,
    output_format: OutputFormat,
) -> None:
    """Render one all-removable-spots result."""

    if output_format == TEXT_FORMAT:
        _print_batch_result(result, output_path)
        return
    payload = {
        "selection": {"mode": "all"},
        "spots_removed": list(result.spots),
        "stats": _removal_stats(result.stats),
    }
    payload.update(_mutation_paths(input_path, output_path))
    _emit_success("remove", 0, payload)


def emit_rename(
    input_path: Path,
    output_path: Path | None,
    result: RenameResult,
    output_format: OutputFormat,
) -> None:
    """Render one exact rename result."""

    if output_format == TEXT_FORMAT:
        prefix = "Dry run verified rename of" if output_path is None else "Renamed"
        print(
            f"{prefix} {result.source!r} to {result.destination!r} in "
            f"{result.definitions_renamed} color-space definition(s) and "
            f"{result.references_renamed} inventoried exact-name reference(s); "
            "alternate colors, "
            f"tint transforms, and paint operands preserved; {_publication_text(output_path)}"
        )
        return
    payload = {
        "source": result.source,
        "destination": result.destination,
        "definitions_renamed": result.definitions_renamed,
        "references_renamed": result.references_renamed,
    }
    payload.update(_mutation_paths(input_path, output_path))
    _emit_success(
        "rename",
        0,
        payload,
    )


def emit_alternate(
    input_path: Path,
    output_path: Path | None,
    result: AlternateResult,
    output_format: OutputFormat,
) -> None:
    """Render one alternate-preview result."""

    if output_format == TEXT_FORMAT:
        cmyk = ",".join(f"{value:g}" for value in result.cmyk_percentages)
        prefix = "Dry run verified changing" if output_path is None else "Changed"
        print(
            f"{prefix} only the alternate preview for {result.spot!r} to "
            f"DeviceCMYK {cmyk} in {result.definitions_changed} Separation "
            "definition(s); spot name, plate identity, content streams, and paint "
            "operands preserved; no process conversion performed; "
            f"{_publication_text(output_path)}"
        )
        return
    payload = {
        "spot": result.spot,
        "cmyk_percentages": list(result.cmyk_percentages),
        "definitions_changed": result.definitions_changed,
    }
    payload.update(_mutation_paths(input_path, output_path))
    _emit_success(
        "set-alternate",
        0,
        payload,
    )


def emit_convert(
    input_path: Path,
    output_path: Path | None,
    result: ConversionResult,
    output_format: OutputFormat,
) -> None:
    """Render one explicit process-conversion result."""

    if output_format == TEXT_FORMAT:
        cmyk = ",".join(f"{value:g}" for value in result.cmyk_percentages)
        pages = ",".join(str(page) for page in result.pages_affected) or "none"
        prefix = "Dry run verified conversion of" if output_path is None else "Converted"
        print(
            f"{prefix} {result.spot!r} paint to explicit DeviceCMYK {cmyk}; "
            f"rewrote {_count(result.color_operators_rewritten, 'color operator')} in "
            f"{_count(result.page_content_sequences_changed, 'page content sequence')} "
            f"and {_count(result.forms_changed, 'Form')}; removed "
            f"{_count(result.definitions_removed, 'Separation definition')} through "
            f"{_count(result.resources_removed, 'resource alias')}; "
            f"pages affected: {pages}; "
            f"{_publication_text(output_path)}"
        )
        return
    payload = {
        "spot": result.spot,
        "cmyk_percentages": list(result.cmyk_percentages),
        "definitions_removed": result.definitions_removed,
        "resources_removed": result.resources_removed,
        "page_content_sequences_changed": result.page_content_sequences_changed,
        "forms_changed": result.forms_changed,
        "color_operators_rewritten": result.color_operators_rewritten,
        "pages_affected": list(result.pages_affected),
    }
    payload.update(_mutation_paths(input_path, output_path))
    _emit_success(
        "convert",
        0,
        payload,
    )


def emit_runtime_error(command: str, error: BaseException, output_format: OutputFormat) -> None:
    """Render one classified processing failure to stderr."""

    code, message, details = _classify_error(error)
    if getattr(error, "findings", None):
        details["findings"] = [finding.wire() for finding in error.findings]
    _emit_error(command, RUNTIME_ERROR_EXIT, code, message, details, output_format)


def emit_usage_error(command: str | None, message: str, output_format: OutputFormat) -> None:
    """Render one command-line syntax or configuration failure to stderr."""

    _emit_error(command, USAGE_ERROR_EXIT, "usage_error", message, {}, output_format)


def _emit_success(command: str, exit_code: int, result: dict[str, Any]) -> None:
    _write_json(
        {
            "schema_version": SCHEMA_VERSION,
            "spotpdf_version": __version__,
            "command": command,
            "ok": True,
            "exit_code": exit_code,
            "result": result,
        },
        sys.stdout,
    )


def _emit_error(
    command: str | None,
    exit_code: int,
    code: str,
    message: str,
    details: dict[str, Any],
    output_format: OutputFormat,
) -> None:
    if output_format == TEXT_FORMAT:
        print(f"spotpdf: error: {message}", file=sys.stderr)
        return
    _write_json(
        {
            "schema_version": SCHEMA_VERSION,
            "spotpdf_version": __version__,
            "command": command,
            "ok": False,
            "exit_code": exit_code,
            "error": {"code": code, "message": message, "details": details},
        },
        sys.stderr,
    )


def _write_json(payload: dict[str, Any], stream: TextIO) -> None:
    record = (
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    binary_stream = getattr(stream, "buffer", None)
    if binary_stream is None:
        stream.write(record)
        return
    try:
        stream.flush()
        binary_stream.write(record.encode("ascii"))
        binary_stream.flush()
    except OSError:
        _redirect_failed_stream(stream)
        raise


def _redirect_failed_stream(stream: TextIO) -> None:
    """Prevent a second status-stream failure during interpreter shutdown."""

    try:
        stream_fd = stream.fileno()
        null_fd = os.open(os.devnull, os.O_WRONLY)
    except (AttributeError, OSError):
        return
    try:
        os.dup2(null_fd, stream_fd)
    except OSError:
        pass
    finally:
        os.close(null_fd)


def _classify_error(error: BaseException) -> tuple[str, str, dict[str, Any]]:
    if isinstance(error, ProcessingBudgetExceeded):
        return (
            "budget_exceeded",
            str(error),
            {
                "metric": error.metric,
                "field": error.field,
                "observed": error.observed,
                "limit": error.limit,
                "option": error.option,
            },
        )
    if isinstance(error, UnsupportedSpotUseError):
        return "unsupported_spot_use", str(error), {}
    if isinstance(error, NestingLimitExceededError):
        return "nesting_limit_exceeded", str(error), {}
    if isinstance(error, InvalidPdfError):
        return "validation_error", str(error), {}
    if isinstance(error, pikepdf.PdfError):
        return "pdf_error", str(error), {}
    if isinstance(error, OSError):
        details = {"errno": error.errno} if error.errno is not None else {}
        return "io_error", str(error), details
    if isinstance(error, (TypeError, ValueError)):
        return "invalid_input", str(error), {}
    if isinstance(error, RecursionError):
        return (
            "nesting_limit_exceeded",
            "PDF nesting exceeds safe processing limits",
            {},
        )
    if isinstance(error, SpotPdfError):
        return "processing_error", str(error), {}
    return "processing_error", str(error), {}


def _removal_stats(stats: RemovalStats) -> dict[str, Any]:
    return {
        "changed": stats.changed,
        "pages_changed": sorted(stats.pages_changed),
        "forms_changed": stats.forms_changed,
        "text_blocks": stats.text_blocks,
        "text_show_operations": stats.text_show_operations,
        "fills_removed": stats.fills_removed,
        "strokes_removed": stats.strokes_removed,
        "resources_removed": stats.resources_removed,
    }


def _print_report(report: InspectionReport) -> None:
    if not report.colorants:
        print("No reachable named colorants found.")
        return
    print("NAME\tROLE\tKIND\tPAGES\tPAINT OPS\tSTATUS")
    for name in sorted(report.colorants, key=str.casefold):
        summary = report.colorants[name]
        roles = ",".join(sorted(role.value for role in summary.roles))
        kinds = ",".join(sorted(kind.value for kind in summary.kinds))
        pages = ",".join(str(page) for page in sorted(summary.pages)) or "-"
        status = "; ".join(sorted(summary.contexts)) or "declared"
        print(
            f"{_display_name(name)}\t{roles}\t{kinds}\t{pages}\t"
            f"{summary.paint_operations}\t{status}"
        )


def _print_batch_result(result: BatchRemovalResult, output: Path | None) -> None:
    if not result.spots:
        if output is None:
            print("Dry run verified: no removable named spot colors found; no output published")
        else:
            print(
                f"No removable named spot colors found; copied input byte-for-byte; "
                f"output: {output}"
            )
        return
    names = ", ".join(repr(name) for name in result.spots)
    prefix = "Dry run verified removal of" if output is None else "Removed"
    print(
        f"{prefix} {len(result.spots)} named spot color(s): {names}; "
        f"{_stats_text(result.stats)}; NChannel process components, canonical "
        "/Cyan, /Magenta, /Yellow, /Black, and reserved /All and /None "
        "preserved; "
        f"{_publication_text(output)}"
    )


def _mutation_paths(input_path: Path, output_path: Path | None) -> dict[str, Any]:
    """Return the additive dry-run shape without changing published result contracts."""

    if output_path is None:
        return {"input": str(input_path), "dry_run": True}
    return {"input": str(input_path), "output": str(output_path)}


def _publication_text(output_path: Path | None) -> str:
    if output_path is None:
        return "no output published"
    return f"output: {output_path}"


def _stats_text(stats: RemovalStats) -> str:
    pages = ",".join(str(page) for page in sorted(stats.pages_changed)) or "none"
    return (
        f"{_count(stats.text_blocks, 'text block')}, "
        f"{_count(stats.text_show_operations, 'text show')}, "
        f"{_count(stats.fills_removed, 'fill')}, "
        f"{_count(stats.strokes_removed, 'stroke')}; "
        f"pages changed: {pages}"
    )


def _count(value: int, noun: str) -> str:
    suffix = "" if value == 1 else "s"
    return f"{value} {noun}{suffix}"


def _display_name(name: str) -> str:
    """Escape controls so PDF-provided names cannot inject TSV rows or columns."""

    return "".join(
        f"\\x{ord(character):02x}"
        if ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
        else character
        for character in name
    )


__all__ = [
    "CHECK_PRESENT_EXIT",
    "JSON_FORMAT",
    "OutputFormat",
    "RUNTIME_ERROR_EXIT",
    "SCHEMA_VERSION",
    "TEXT_FORMAT",
    "USAGE_ERROR_EXIT",
    "_display_name",
    "_print_batch_result",
    "_print_report",
    "_stats_text",
    "emit_alternate",
    "emit_check",
    "emit_convert",
    "emit_list",
    "emit_remove_all",
    "emit_remove_spot",
    "emit_rename",
    "emit_runtime_error",
    "emit_usage_error",
]
