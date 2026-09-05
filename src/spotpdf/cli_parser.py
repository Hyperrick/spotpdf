"""Argument parsing and usage-error policy for the spotpdf CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final, cast

from .alternate import parse_cmyk_percentages
from .cli_limits import add_processing_limit_arguments
from .cli_output import (
    JSON_FORMAT,
    TEXT_FORMAT,
    USAGE_ERROR_EXIT,
    OutputFormat,
    emit_usage_error,
)
from .model import __version__

_FORMATS: Final = (TEXT_FORMAT, JSON_FORMAT)


@dataclass
class _ParseContext:
    """State shared by the root parser and every command parser."""

    output_format: OutputFormat = TEXT_FORMAT
    command_name: str | None = None


class SpotPdfArgumentParser(argparse.ArgumentParser):
    """Use an automation-safe exit code and optional structured errors."""

    def __init__(
        self,
        *args: object,
        parse_context: _ParseContext | None = None,
        command_name: str | None = None,
        **kwargs: object,
    ) -> None:
        self.parse_context = parse_context or _ParseContext()
        self.command_name = command_name
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        command_name = self.command_name or self.parse_context.command_name
        if self.parse_context.output_format == JSON_FORMAT:
            emit_usage_error(command_name, message, JSON_FORMAT)
        else:
            self.print_usage(sys.stderr)
            self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        self.exit(USAGE_ERROR_EXIT)

    def parse_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        """Preserve the parsed command when the root parser rejects leftovers."""

        raw_args = sys.argv[1:] if args is None else list(args)
        self.parse_context.output_format = _detected_output_format(raw_args)
        self.parse_context.command_name = None
        parsed, extras = self.parse_known_args(raw_args, namespace)
        if extras:
            command_name = getattr(parsed, "command", None)
            if isinstance(command_name, str):
                self.parse_context.command_name = command_name
            message = f"unrecognized arguments: {' '.join(extras)}"
            if self.exit_on_error:
                self.error(message)
            raise argparse.ArgumentError(None, message)
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parse_context = _ParseContext()
    parser = SpotPdfArgumentParser(
        prog="spotpdf",
        description="Inspect and safely mutate named spot colors in vector PDF content.",
        parse_context=parse_context,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_output_format(parser, default=TEXT_FORMAT)
    parser_factory = partial(SpotPdfArgumentParser, parse_context=parse_context)
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=parser_factory,
    )

    list_parser = commands.add_parser(
        "list",
        command_name="list",
        help="list reachable named colorants and their semantic roles",
    )
    list_parser.add_argument("input", type=Path, help="input PDF")

    check_parser = commands.add_parser(
        "check",
        command_name="check",
        help="check for one exact spot or Separation name",
        epilog="A present name exits with status 2 even when JSON reports ok=true.",
    )
    check_parser.add_argument("input", type=Path, help="input PDF")
    check_parser.add_argument("--spot", required=True, help="exact, case-sensitive spot name")

    remove_parser = commands.add_parser(
        "remove",
        command_name="remove",
        help="remove supported paint for one or all named spots",
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
    _add_mutation_destination(remove_parser)
    _add_force(remove_parser)

    rename_parser = commands.add_parser(
        "rename",
        command_name="rename",
        help="atomically rename one exact Separation spot plate",
    )
    rename_parser.add_argument("input", type=Path, help="input PDF")
    rename_parser.add_argument("--spot", required=True, help="exact source spot name")
    rename_parser.add_argument("--to", required=True, dest="destination", help="exact target name")
    _add_mutation_destination(rename_parser)
    _add_force(rename_parser)

    alternate_parser = commands.add_parser(
        "set-alternate",
        command_name="set-alternate",
        help="change only one Separation spot's alternate CMYK preview",
    )
    alternate_parser.add_argument("input", type=Path, help="input PDF")
    alternate_parser.add_argument("--spot", required=True, help="exact spot name")
    alternate_parser.add_argument(
        "--cmyk",
        required=True,
        type=parse_cmyk_percentages,
        metavar="C,M,Y,K",
        help="four finite CMYK percentages in the inclusive range 0..100",
    )
    _add_mutation_destination(alternate_parser)
    _add_force(alternate_parser)

    convert_parser = commands.add_parser(
        "convert",
        command_name="convert",
        help="replace supported Separation paint with explicit DeviceCMYK values",
        description=(
            "Remove one exact, case-sensitive Separation plate by replacing supported paint "
            "with an operator-supplied DeviceCMYK recipe. The recipe is not inferred from "
            "the alternate color or an ICC profile."
        ),
    )
    convert_parser.add_argument("input", type=Path, help="input PDF")
    convert_parser.add_argument(
        "--spot",
        required=True,
        help="exact, case-sensitive spot name whose plate will be removed",
    )
    convert_parser.add_argument(
        "--to-cmyk",
        required=True,
        type=parse_cmyk_percentages,
        metavar="C,M,Y,K",
        help="operator-supplied C,M,Y,K percentages in the inclusive range 0..100",
    )
    _add_mutation_destination(convert_parser)
    _add_force(convert_parser)
    for command_parser in (
        list_parser,
        check_parser,
        remove_parser,
        rename_parser,
        alternate_parser,
        convert_parser,
    ):
        _add_output_format(command_parser, default=argparse.SUPPRESS)
        add_processing_limit_arguments(command_parser)
    return parser


def _add_mutation_destination(parser: argparse.ArgumentParser) -> None:
    """Require either a published output or a fully verified dry run."""

    from .report_cli import add_report_arguments

    add_report_arguments(parser)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("-o", "--output", type=Path, help="publish the verified output PDF")
    destination.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "perform the full rewrite and post-save verification in temporary storage, "
            "then discard it"
        ),
    )


def _add_force(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output after validation; has no effect with --dry-run",
    )


def _add_output_format(parser: argparse.ArgumentParser, *, default: object) -> None:
    parser.add_argument(
        "--format",
        choices=_FORMATS,
        default=default,
        help="output format; JSON emits one schema-versioned object (default: text)",
    )


def _detected_output_format(argv: Sequence[str]) -> OutputFormat:
    """Select the last valid exact format option before end-of-options."""

    selected: OutputFormat = TEXT_FORMAT
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        if token == "--format" and index + 1 < len(argv):
            candidate = argv[index + 1]
            if candidate in _FORMATS:
                selected = cast(OutputFormat, candidate)
                index += 2
                continue
        if token.startswith("--format="):
            candidate = token.partition("=")[2]
            if candidate in _FORMATS:
                selected = cast(OutputFormat, candidate)
        index += 1
    return selected


__all__ = ["SpotPdfArgumentParser", "build_parser"]
