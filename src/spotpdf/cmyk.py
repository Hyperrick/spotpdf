"""Shared parsing and deterministic PDF-number handling for CMYK recipes."""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, TypeAlias

import pikepdf

from .model import InvalidPdfError

PercentageCmyk: TypeAlias = tuple[float, float, float, float]
NormalizedCmyk: TypeAlias = tuple[float, float, float, float]
PdfCmyk: TypeAlias = tuple[Any, Any, Any, Any]


def parse_cmyk_percentages(value: str) -> PercentageCmyk:
    """Parse one comma-separated CMYK percentage tuple for argparse."""

    parts = value.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "CMYK must contain exactly four comma-separated percentages"
        )
    try:
        return validate_cmyk_percentages(parts, allow_numeric_strings=True)
    except InvalidPdfError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def validate_cmyk_percentages(
    values: Sequence[object],
    *,
    allow_numeric_strings: bool = False,
) -> PercentageCmyk:
    """Return four finite percentage values in the inclusive range 0..100."""

    if len(values) != 4:
        raise InvalidPdfError("CMYK must contain exactly four percentage values")
    parsed: list[float] = []
    for value in values:
        if isinstance(value, bool) or (
            isinstance(value, (str, bytes)) and not allow_numeric_strings
        ):
            raise InvalidPdfError(f"invalid CMYK percentage: {_safe_repr(value)}")
        component_decimal = _finite_decimal(value, "CMYK percentage")
        if not Decimal(0) <= component_decimal <= Decimal(100):
            raise InvalidPdfError(
                f"CMYK percentage must be finite and within 0..100: {_safe_repr(value)}"
            )
        component = float(component_decimal)
        parsed.append(0.0 if component == 0 else component)
    return parsed[0], parsed[1], parsed[2], parsed[3]


def normalized_cmyk(percentages: PercentageCmyk) -> NormalizedCmyk:
    """Convert validated percentages to canonical PDF-storable components."""

    return canonicalize_normalized_cmyk(tuple(value / 100 for value in percentages))


def canonicalize_normalized_cmyk(cmyk: Sequence[object]) -> NormalizedCmyk:
    """Round four normalized components exactly as pikepdf stores PDF numbers."""

    if len(cmyk) != 4:
        raise InvalidPdfError("normalized CMYK must contain exactly four components")
    values = tuple(float(canonical_pdf_number(value)) for value in cmyk)
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise InvalidPdfError("normalized CMYK components must be finite and within 0..1")
    return values[0], values[1], values[2], values[3]


def scale_cmyk_tint(tint: object, cmyk: NormalizedCmyk) -> PdfCmyk:
    """Return canonical PDF operands for ``tint * requested CMYK``."""

    tint_value = _tint_decimal(tint)
    scaled = tuple(canonical_pdf_number(tint_value * Decimal(str(component))) for component in cmyk)
    return scaled[0], scaled[1], scaled[2], scaled[3]


def canonical_pdf_number(value: object) -> Any:
    """Round one finite number exactly as a pikepdf PDF scalar."""

    if isinstance(value, bool):
        raise InvalidPdfError(f"invalid PDF number: {_safe_repr(value)}")
    try:
        stored = pikepdf.Array([value])[0]
        numeric = float(stored)
    except (InvalidOperation, OverflowError, TypeError, ValueError) as error:
        raise InvalidPdfError(f"invalid PDF number: {_safe_repr(value)}") from error
    if not math.isfinite(numeric):
        raise InvalidPdfError(f"invalid PDF number: {_safe_repr(value)}")
    return int(stored) if stored in {0, 1} else stored


def _tint_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise InvalidPdfError(
            f"Separation tint must be one number within 0..1: {_safe_repr(value)}"
        )
    numeric = _finite_decimal(value, "Separation tint")
    if not Decimal(0) <= numeric <= Decimal(1):
        raise InvalidPdfError(
            f"Separation tint must be finite and within 0..1: {_safe_repr(value)}"
        )
    return numeric


def _finite_decimal(value: object, label: str) -> Decimal:
    try:
        if isinstance(value, Decimal):
            numeric = value
        elif isinstance(value, int):
            numeric = Decimal(value)
        else:
            numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidPdfError(f"invalid {label}: {_safe_repr(value)}") from error
    if not numeric.is_finite():
        raise InvalidPdfError(f"{label} must be finite: {_safe_repr(value)}")
    return numeric


def _safe_repr(value: object) -> str:
    try:
        return repr(value)
    except (OverflowError, ValueError):
        return f"<{type(value).__name__}>"


__all__ = [
    "NormalizedCmyk",
    "PdfCmyk",
    "PercentageCmyk",
    "canonical_pdf_number",
    "canonicalize_normalized_cmyk",
    "normalized_cmyk",
    "parse_cmyk_percentages",
    "scale_cmyk_tint",
    "validate_cmyk_percentages",
]
