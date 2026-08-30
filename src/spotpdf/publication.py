"""Strict PDF opening, saving, and atomic output publication."""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

import pikepdf

from .budget_preflight import audit_pdf, enforce_input_size
from .limits import (
    DEFAULT_PROCESSING_LIMITS,
    ProcessingBudgetExceeded,
    ProcessingLimits,
    require_processing_limits,
)
from .model import InvalidPdfError, SpotPdfError


@dataclass(frozen=True)
class AtomicPdfOutput:
    """Validated input, final output, and private sibling temporary path."""

    input_path: Path
    output_path: Path
    temp_path: Path
    force: bool


@contextmanager
def atomic_pdf_output(
    input_path: Path,
    output_path: Path,
    *,
    force: bool,
    limits: ProcessingLimits = DEFAULT_PROCESSING_LIMITS,
) -> Iterator[AtomicPdfOutput]:
    """Yield a temporary destination and publish it only after successful exit."""

    limits = require_processing_limits(limits)
    transaction = _prepare_output(input_path, output_path, force=force, limits=limits)
    published = False
    try:
        yield transaction
        if transaction.temp_path.stat().st_size == 0:
            raise SpotPdfError("processing did not produce a PDF output")
        shutil.copymode(transaction.input_path, transaction.temp_path)
        _publish_output(transaction)
        published = True
    finally:
        if transaction.temp_path.exists():
            processing_failed = sys.exc_info()[0] is not None
            try:
                _discard_temporary_output(transaction.temp_path)
            except OSError:
                if not published and not processing_failed:
                    raise


def open_strict(
    path: str | PathLike[str],
    *,
    limits: ProcessingLimits | None = DEFAULT_PROCESSING_LIMITS,
) -> pikepdf.Pdf:
    """Open without recovery, preflight source budgets, and reject warnings."""

    path = Path(path)
    input_bytes = None
    if limits is not None:
        limits = require_processing_limits(limits)
        input_bytes = enforce_input_size(path, limits)

    try:
        pdf = pikepdf.open(
            path,
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=True,
        )
    except (pikepdf.PdfError, pikepdf.PasswordError) as error:
        raise InvalidPdfError(f"cannot open PDF safely: {error}") from error

    try:
        _reject_syntax_findings(pdf.get_warnings())
        if limits is not None and input_bytes is not None:
            audit_pdf(pdf, limits, input_bytes=input_bytes)
        syntax_errors = pdf.check_pdf_syntax()
        _reject_syntax_findings([*syntax_errors, *pdf.get_warnings()])
    except ProcessingBudgetExceeded:
        pdf.close()
        raise
    except InvalidPdfError:
        pdf.close()
        raise
    except (
        pikepdf.DataDecodingError,
        pikepdf.DeletedObjectError,
        pikepdf.PdfError,
        RuntimeError,
    ) as error:
        pdf.close()
        raise InvalidPdfError(f"cannot validate PDF safely: {error}") from error
    except Exception:
        pdf.close()
        raise
    return pdf


def save_pdf(pdf: pikepdf.Pdf, path: Path) -> None:
    """Save using the project's compatibility-preserving rewrite policy."""

    pdf.save(
        path,
        force_version=pdf.pdf_version,
        preserve_pdfa=True,
        compress_streams=True,
        object_stream_mode=pikepdf.ObjectStreamMode.preserve,
        linearize=pdf.is_linearized,
    )


def _prepare_output(
    input_path: Path,
    output_path: Path,
    *,
    force: bool,
    limits: ProcessingLimits,
) -> AtomicPdfOutput:
    input_path = input_path.resolve()
    if not input_path.is_file():
        raise InvalidPdfError(f"input PDF does not exist: {input_path}")
    enforce_input_size(input_path, limits)
    output_path = _output_path_without_final_symlink_resolution(output_path)
    if input_path == output_path:
        raise InvalidPdfError("input and output paths must be different")
    if output_path.is_symlink():
        raise InvalidPdfError(f"output path must not be a symbolic link: {output_path}")
    if output_path.exists() and output_path.samefile(input_path):
        raise InvalidPdfError("input and output paths must not identify the same file")
    if output_path.exists() and not force:
        raise InvalidPdfError(f"output already exists (use --force): {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return AtomicPdfOutput(
        input_path=input_path,
        output_path=output_path,
        temp_path=_temporary_output_path(output_path),
        force=force,
    )


def _publish_output(transaction: AtomicPdfOutput) -> None:
    """Commit with replacement or an atomic no-clobber hard link."""

    if transaction.force:
        os.replace(transaction.temp_path, transaction.output_path)
        return
    try:
        if os.name == "nt":
            os.rename(transaction.temp_path, transaction.output_path)
        else:
            os.link(transaction.temp_path, transaction.output_path)
    except FileExistsError as error:
        raise InvalidPdfError(
            f"output appeared during processing (use --force): {transaction.output_path}"
        ) from error


def _temporary_output_path(output_path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{output_path.stem}-",
        suffix=".tmp.pdf",
        dir=output_path.parent,
    )
    os.close(descriptor)
    return Path(name)


def _discard_temporary_output(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:
        path.chmod(path.stat().st_mode | stat.S_IWRITE)
        path.unlink()


def _output_path_without_final_symlink_resolution(path: Path) -> Path:
    """Resolve the parent while preserving the final path component verbatim."""

    if path.name in {"", ".", ".."}:
        raise InvalidPdfError(f"output must name a PDF file: {path}")
    return path.parent.resolve() / path.name


def _reject_syntax_findings(findings: list[object]) -> None:
    if not findings:
        return
    details = "; ".join(str(item) for item in findings)
    raise InvalidPdfError(f"PDF syntax warnings are not accepted: {details}")
