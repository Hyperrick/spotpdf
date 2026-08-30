"""Shared content-stream state and parsing helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import pikepdf

from .colors import pdf_name, resolve_color_space
from .model import ColorSpaceInfo, InvalidPdfError

Instruction = pikepdf.ContentStreamInstruction

TEXT_SHOW_OPERATORS = {"Tj", "TJ", "'", '"'}
FILL_TEXT_MODES = {0, 2, 4, 6}
STROKE_TEXT_MODES = {1, 2, 5, 6}
CLIP_TEXT_MODES = {4, 5, 6, 7}


@dataclass
class GraphicsState:
    """Subset of PDF graphics state relevant to named-color paint."""

    nonstroking: ColorSpaceInfo = ColorSpaceInfo()
    stroking: ColorSpaceInfo = ColorSpaceInfo()
    text_render_mode: int = 0

    def clone(self) -> GraphicsState:
        return replace(self)


def instruction(operator: str, *operands: Any) -> Instruction:
    """Build one pikepdf content-stream instruction."""

    return Instruction(list(operands), pikepdf.Operator(operator))


def operator_name(item: Any) -> str:
    """Return an operator name, including pikepdf inline-image objects."""

    try:
        return str(item.operator)
    except AttributeError:
        return "INLINE IMAGE"


def find_text_end(instructions: Sequence[Any], start: int) -> int:
    """Find the matching ET for one non-nested PDF text object."""

    for index in range(start + 1, len(instructions)):
        operator = operator_name(instructions[index])
        if operator == "BT":
            raise InvalidPdfError("nested BT text object")
        if operator == "ET":
            return index
    raise InvalidPdfError("BT without matching ET")


def render_mode(fill: bool, stroke: bool, clip: bool) -> int:
    """Return the PDF text rendering mode for three paint flags."""

    modes = {
        (True, False, False): 0,
        (False, True, False): 1,
        (True, True, False): 2,
        (False, False, False): 3,
        (True, False, True): 4,
        (False, True, True): 5,
        (True, True, True): 6,
        (False, False, True): 7,
    }
    return modes[(fill, stroke, clip)]


def resource_dictionary(resources: Any, name: str) -> Any | None:
    """Return one resource subdictionary when its container is valid."""

    if not isinstance(resources, (pikepdf.Dictionary, pikepdf.Stream)):
        return None
    return resources.get(pikepdf.Name(name), None)


def named_resource(dictionary: Any, name: Any) -> Any | None:
    """Resolve one decoded or encoded PDF resource name."""

    if not isinstance(dictionary, (pikepdf.Dictionary, pikepdf.Stream)):
        return None
    return dictionary.get(pikepdf.Name(f"/{pdf_name(name)}"), None)


def color_object_colorants(value: Any, resources: Any) -> frozenset[str]:
    """Return named colorants from an image or shading color-space value."""

    if isinstance(value, pikepdf.Name):
        return frozenset(resolve_color_space(resources, value).colorants)
    if not isinstance(value, pikepdf.Array) or not value:
        return frozenset()
    family = pdf_name(value[0])
    if family == "Separation" and len(value) >= 2:
        return frozenset({pdf_name(value[1])})
    if family == "DeviceN" and len(value) >= 2 and isinstance(value[1], pikepdf.Array):
        return frozenset(pdf_name(name) for name in value[1])
    return frozenset()
