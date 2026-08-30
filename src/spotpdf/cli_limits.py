"""Shared argparse surface for per-command PDF processing limits."""

from __future__ import annotations

import argparse

from .limits import DEFAULT_PROCESSING_LIMITS, ProcessingLimits


def positive_integer(value: str) -> int:
    """Parse one strictly positive base-10 CLI integer."""

    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def add_processing_limit_arguments(parser: argparse.ArgumentParser) -> None:
    """Expose the same application ceilings on one subcommand."""

    defaults = DEFAULT_PROCESSING_LIMITS
    group = parser.add_argument_group(
        "processing budgets",
        "Application ceilings for one command; raise only for trusted large PDFs. "
        "They do not replace OS/container limits.",
    )
    group.add_argument(
        "--max-input-bytes",
        type=positive_integer,
        default=defaults.max_input_bytes,
        metavar="BYTES",
        help=f"maximum input file size in bytes (default: {defaults.max_input_bytes})",
    )
    group.add_argument(
        "--max-pages",
        type=positive_integer,
        default=defaults.max_pages,
        metavar="INTEGER",
        help=f"maximum PDF page count (default: {defaults.max_pages})",
    )
    group.add_argument(
        "--max-reachable-objects",
        type=positive_integer,
        default=defaults.max_reachable_objects,
        metavar="INTEGER",
        help=(
            "maximum reachable PDF graph entries processed "
            f"(default: {defaults.max_reachable_objects})"
        ),
    )
    group.add_argument(
        "--max-decoded-content-bytes",
        type=positive_integer,
        default=defaults.max_decoded_content_bytes,
        metavar="BYTES",
        help=(
            "maximum decoded page/Form content bytes processed "
            f"(default: {defaults.max_decoded_content_bytes})"
        ),
    )
    group.add_argument(
        "--max-operators",
        type=positive_integer,
        default=defaults.max_operators,
        metavar="INTEGER",
        help=f"maximum content operators processed (default: {defaults.max_operators})",
    )


def processing_limits_from_args(args: argparse.Namespace) -> ProcessingLimits:
    """Build fresh immutable limits for one parsed command invocation."""

    return ProcessingLimits(
        max_input_bytes=args.max_input_bytes,
        max_pages=args.max_pages,
        max_reachable_objects=args.max_reachable_objects,
        max_decoded_content_bytes=args.max_decoded_content_bytes,
        max_operators=args.max_operators,
    )


__all__ = [
    "add_processing_limit_arguments",
    "positive_integer",
    "processing_limits_from_args",
]
