"""Small value parsers and role rules used by the semantic inventory."""

from __future__ import annotations

from typing import Any

import pikepdf

from .colors import PROCESS_COLORANTS, pdf_name
from .model import ColorantRole

_PDF_NAME_DELIMITERS = frozenset(b"#%()/<>[]{}")


def name_value(value: Any) -> str | None:
    """Decode a PDF Name, rejecting other object types."""

    return pdf_name(value) if isinstance(value, pikepdf.Name) else None


def name_or_string(value: Any) -> str | None:
    """Decode a PDF Name or text string used as a device colorant."""

    if isinstance(value, pikepdf.Name):
        return pdf_name(value)
    if isinstance(value, pikepdf.String):
        return str(value)
    return None


def name_array(value: Any) -> tuple[str, ...]:
    """Return the valid PDF Names from an array in source order."""

    if not isinstance(value, pikepdf.Array):
        return ()
    return tuple(name for item in value if (name := name_value(item)) is not None)


def indexed_name_array(value: Any) -> tuple[tuple[int, str], ...]:
    """Return valid names with their physical source-array indices."""

    if not isinstance(value, pikepdf.Array):
        return ()
    return tuple(
        (index, name) for index, item in enumerate(value) if (name := name_value(item)) is not None
    )


def separation_name(value: Any) -> str | None:
    """Return the colorant from a syntactically recognizable Separation array."""

    if not isinstance(value, pikepdf.Array) or len(value) < 2:
        return None
    if pdf_name(value[0]) != "Separation":
        return None
    return name_value(value[1])


def color_space_name(value: Any) -> str | None:
    """Return the family name of a named or array color-space value."""

    if isinstance(value, pikepdf.Name):
        return pdf_name(value)
    if isinstance(value, pikepdf.Array) and value:
        return name_value(value[0])
    return None


def is_cmyk_process_color_space(value: Any) -> bool:
    """Return whether an NChannel process space has CMYK component semantics."""

    if isinstance(value, pikepdf.Name):
        return pdf_name(value) == "DeviceCMYK"
    if not isinstance(value, pikepdf.Array) or len(value) < 2:
        return False
    if name_value(value[0]) != "ICCBased":
        return False
    profile = value[1]
    if not isinstance(profile, (pikepdf.Dictionary, pikepdf.Stream)):
        return False
    return profile.get(pikepdf.Name.N, None) == 4


def path_name(value: Any) -> str:
    """Encode one PDF Name as an unambiguous human-readable path segment."""

    encoded = pdf_name(value).encode("utf-8")
    body = "".join(
        chr(byte) if 0x21 <= byte <= 0x7E and byte not in _PDF_NAME_DELIMITERS else f"#{byte:02X}"
        for byte in encoded
    )
    return f"/{body}"


def base_role(name: str) -> ColorantRole:
    """Classify roles that are intrinsic to reserved and canonical names."""

    if name == "All":
        return ColorantRole.ALL
    if name == "None":
        return ColorantRole.NONE
    if name in PROCESS_COLORANTS:
        return ColorantRole.PROCESS
    return ColorantRole.SPOT


def devicen_role(name: str, process_names: frozenset[str]) -> ColorantRole:
    """Classify one DeviceN component using its NChannel process dictionary."""

    if name == "All":
        return ColorantRole.ALL
    if name == "None":
        return ColorantRole.NONE
    if name in process_names:
        return ColorantRole.PROCESS
    return ColorantRole.SPOT


def dominant_role(first: ColorantRole, second: ColorantRole) -> ColorantRole:
    """Conservatively prefer process or special semantics over automatic spot use."""

    priority = {
        ColorantRole.SPOT: 0,
        ColorantRole.PROCESS: 1,
        ColorantRole.ALL: 2,
        ColorantRole.NONE: 2,
    }
    return second if priority[second] > priority[first] else first
