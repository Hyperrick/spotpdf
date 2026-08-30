"""ISO content-operator and graphics-object context validation for conversion."""

from __future__ import annotations

from .content_support import TEXT_SHOW_OPERATORS
from .model import InvalidPdfError, UnsupportedSpotUseError

FILL_PATH_OPERATORS = frozenset({"f", "F", "f*", "B", "B*", "b", "b*"})
STROKE_PATH_OPERATORS = frozenset({"S", "s", "B", "B*", "b", "b*"})

_SPECIAL_GRAPHICS_OPERATORS = frozenset({"q", "Q", "cm"})
_PATH_CONSTRUCTION_OPERATORS = frozenset({"m", "l", "c", "v", "y", "h", "re"})
_PATH_OPERATORS = (
    _PATH_CONSTRUCTION_OPERATORS | FILL_PATH_OPERATORS | STROKE_PATH_OPERATORS | {"n", "W", "W*"}
)
_TEXT_POSITIONING_OPERATORS = frozenset({"Td", "TD", "Tm", "T*"})
_TYPE3_WIDTH_OPERATORS = frozenset({"d0", "d1"})

# Pikepdf exposes a complete BI/ID/EI sequence as one ``INLINE IMAGE`` item.
STANDARD_CONTENT_OPERATORS = frozenset(
    {"w", "J", "j", "M", "d", "ri", "i", "gs"}
    | _SPECIAL_GRAPHICS_OPERATORS
    | _PATH_OPERATORS
    | {"BT", "ET", "Tc", "Tw", "Tz", "TL", "Tf", "Tr", "Ts"}
    | _TEXT_POSITIONING_OPERATORS
    | TEXT_SHOW_OPERATORS
    | _TYPE3_WIDTH_OPERATORS
    | {"CS", "cs", "SC", "SCN", "sc", "scn", "G", "g", "RG", "rg", "K", "k"}
    | {"sh", "Do", "MP", "DP", "BMC", "BDC", "EMC", "BX", "EX", "INLINE IMAGE"}
)

_PAGE_DESCRIPTION_ONLY = (
    _SPECIAL_GRAPHICS_OPERATORS | _PATH_OPERATORS | {"Do", "sh", "INLINE IMAGE"}
)
_TEXT_OBJECT_ONLY = _TEXT_POSITIONING_OPERATORS | TEXT_SHOW_OPERATORS


def validate_conversion_operator(
    operator: str,
    *,
    inside_text: bool,
    compatibility_depth: int,
    context: str,
) -> None:
    """Reject unknown operators and operators outside their valid object context."""

    if operator not in STANDARD_CONTENT_OPERATORS and compatibility_depth == 0:
        raise UnsupportedSpotUseError(
            f"{context}: unknown content operator {operator!r} outside a compatibility "
            "section is not supported"
        )
    if operator in _TYPE3_WIDTH_OPERATORS:
        raise InvalidPdfError(
            f"{context}: Type 3 glyph-width operator {operator} is invalid in page/Form content"
        )
    if inside_text and operator in _PAGE_DESCRIPTION_ONLY:
        raise InvalidPdfError(f"{context}: {operator} operator is invalid inside BT/ET")
    if not inside_text and operator in _TEXT_OBJECT_ONLY:
        raise InvalidPdfError(f"{context}: {operator} operator is invalid outside BT/ET")


__all__ = [
    "FILL_PATH_OPERATORS",
    "STANDARD_CONTENT_OPERATORS",
    "STROKE_PATH_OPERATORS",
    "validate_conversion_operator",
]
