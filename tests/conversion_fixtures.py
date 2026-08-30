"""Synthetic, redistributable PDFs used only by conversion tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pikepdf


def separation(
    name: str = "DemoSpot",
    *,
    alternate_cmyk: tuple[float, float, float, float] = (1, 0, 0, 0),
) -> pikepdf.Array:
    """Return a complete linear Separation with a deliberately independent preview."""

    tint = pikepdf.Dictionary(
        FunctionType=2,
        Domain=pikepdf.Array([0, 1]),
        Range=pikepdf.Array([0, 1, 0, 1, 0, 1, 0, 1]),
        C0=pikepdf.Array([0, 0, 0, 0]),
        C1=pikepdf.Array(alternate_cmyk),
        N=1,
    )
    return pikepdf.Array(
        [
            pikepdf.Name.Separation,
            pikepdf.Name(f"/{name}"),
            pikepdf.Name.DeviceCMYK,
            tint,
        ]
    )


def make_basic_conversion_pdf(path: Path) -> Path:
    """Create fills and strokes at initial, quarter, and half target tints."""

    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(300, 220))
    page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
    page.Contents = pdf.make_stream(
        b"\n".join(
            (
                b"/Ink cs",
                b"10 10 80 80 re f",
                b"0.25 scn",
                b"110 10 80 80 re f",
                b"/Ink CS",
                b"0.5 SCN",
                b"10 w 20 150 m 260 150 l S",
            )
        )
    )
    pdf.save(path)
    return path


def parsed_operations(path: Path, page_number: int = 1) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    """Return operator names and operands from one saved page."""

    with pikepdf.open(path) as pdf:
        return tuple(
            (str(item.operator), tuple(item.operands))
            for item in pikepdf.parse_content_stream(pdf.pages[page_number - 1])
        )
