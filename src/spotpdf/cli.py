"""Command-line interface for spotpdf."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pikepdf

from .document import check_spot, inspect_pdf, remove_all_spots, remove_spot
from .model import BatchRemovalResult, RemovalStats, SpotPdfError, __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotpdf",
        description="Inspect and remove named spot-color paint from vector PDF content.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser(
        "list",
        help="list reachable named colorants and their semantic roles",
    )
    list_parser.add_argument("input", type=Path, help="input PDF")

    check_parser = commands.add_parser(
        "check",
        help="check for one exact spot or Separation name",
    )
    check_parser.add_argument("input", type=Path, help="input PDF")
    check_parser.add_argument("--spot", required=True, help="exact, case-sensitive spot name")

    remove_parser = commands.add_parser(
        "remove", help="remove supported paint for one or all named spots"
    )
    remove_parser.add_argument("input", type=Path, help="input PDF")
    selection = remove_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--spot", help="exact, case-sensitive spot name")
    selection.add_argument(
        "--all",
        action="store_true",
        dest="all_spots",
        help=(
            "remove named spots while preserving NChannel process components, "
            "canonical Cyan/Magenta/Yellow/Black, and reserved /All and /None"
        ),
    )
    remove_parser.add_argument("-o", "--output", required=True, type=Path, help="output PDF")
    remove_parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output after validation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            _print_report(inspect_pdf(args.input))
            return 0
        if args.command == "check":
            present = check_spot(args.input, args.spot)
            print(f"{args.spot}: {'present' if present else 'absent'}")
            return 2 if present else 0
        if args.command == "remove":
            if args.all_spots:
                result = remove_all_spots(
                    args.input,
                    args.output,
                    force=args.force,
                )
                _print_batch_result(result, args.output)
                return 0
            stats = remove_spot(
                args.input,
                args.output,
                args.spot,
                force=args.force,
            )
            print(f"Removed {args.spot!r}: {_stats_text(stats)}; output: {args.output}")
            return 0
    except (SpotPdfError, pikepdf.PdfError, OSError, TypeError, ValueError) as error:
        print(f"spotpdf: error: {error}", file=sys.stderr)
        return 1
    except RecursionError:
        print(
            "spotpdf: error: PDF nesting exceeds safe processing limits",
            file=sys.stderr,
        )
        return 1
    return 1


def _print_report(report) -> None:
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


def _print_batch_result(result: BatchRemovalResult, output: Path) -> None:
    if not result.spots:
        print(f"No removable named spot colors found; copied input byte-for-byte; output: {output}")
        return
    names = ", ".join(repr(name) for name in result.spots)
    print(
        f"Removed {len(result.spots)} named spot color(s): {names}; "
        f"{_stats_text(result.stats)}; NChannel process components, canonical "
        "/Cyan, /Magenta, /Yellow, /Black, and reserved /All and /None "
        "preserved; "
        f"output: {output}"
    )


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


if __name__ == "__main__":
    raise SystemExit(main())
