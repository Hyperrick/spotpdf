"""Command routing for spotpdf."""

from __future__ import annotations

import sys

import pikepdf

from .alternate import set_alternate_cmyk
from .cli_limits import processing_limits_from_args
from .cli_output import (
    CHECK_PRESENT_EXIT,
    RUNTIME_ERROR_EXIT,
    _display_name,
    _print_batch_result,
    _print_report,
    _stats_text,
    emit_alternate,
    emit_check,
    emit_convert,
    emit_list,
    emit_remove_all,
    emit_remove_spot,
    emit_rename,
    emit_runtime_error,
)
from .cli_parser import build_parser
from .convert import convert_spot_to_cmyk
from .document import check_spot, inspect_pdf, remove_all_spots, remove_spot
from .model import SpotPdfError
from .rename import rename_spot


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(raw_argv)
    output_format = args.format
    try:
        limits = processing_limits_from_args(args)
        if args.command == "list":
            report = inspect_pdf(args.input, limits=limits)
            emit_list(report, args.input, output_format)
            return 0
        if args.command == "check":
            present = check_spot(args.input, args.spot, limits=limits)
            emit_check(args.input, args.spot, present, output_format)
            return CHECK_PRESENT_EXIT if present else 0
        if args.command == "remove":
            if args.all_spots:
                result = remove_all_spots(
                    args.input,
                    args.output,
                    force=args.force,
                    limits=limits,
                )
                emit_remove_all(args.input, args.output, result, output_format)
                return 0
            stats = remove_spot(
                args.input,
                args.output,
                args.spot,
                force=args.force,
                limits=limits,
            )
            emit_remove_spot(args.input, args.output, args.spot, stats, output_format)
            return 0
        if args.command == "rename":
            result = rename_spot(
                args.input,
                args.output,
                args.spot,
                args.destination,
                force=args.force,
                limits=limits,
            )
            emit_rename(args.input, args.output, result, output_format)
            return 0
        if args.command == "set-alternate":
            result = set_alternate_cmyk(
                args.input,
                args.output,
                args.spot,
                args.cmyk,
                force=args.force,
                limits=limits,
            )
            emit_alternate(args.input, args.output, result, output_format)
            return 0
        if args.command == "convert":
            result = convert_spot_to_cmyk(
                args.input,
                args.output,
                args.spot,
                args.to_cmyk,
                force=args.force,
                limits=limits,
            )
            emit_convert(args.input, args.output, result, output_format)
            return 0
    except (SpotPdfError, pikepdf.PdfError, OSError, TypeError, ValueError) as error:
        emit_runtime_error(args.command, error, output_format)
        return RUNTIME_ERROR_EXIT
    except RecursionError as error:
        emit_runtime_error(args.command, error, output_format)
        return RUNTIME_ERROR_EXIT
    return RUNTIME_ERROR_EXIT


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "_display_name",
    "_print_batch_result",
    "_print_report",
    "_stats_text",
    "build_parser",
    "main",
]
