"""Create the synthetic PDF used in the README screenshots."""

from __future__ import annotations

import argparse
from pathlib import Path

import pikepdf

PAGE_WIDTH = 720
PAGE_HEIGHT = 420


def build_demo_pdf(output: Path) -> None:
    """Write a one-page vector PDF with process and removable spot artwork."""

    output.parent.mkdir(parents=True, exist_ok=True)
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(PAGE_WIDTH, PAGE_HEIGHT))
        regular = pdf.make_indirect(_base14_font("Helvetica"))
        bold = pdf.make_indirect(_base14_font("Helvetica-Bold"))
        page.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=regular, F2=bold),
            ColorSpace=pikepdf.Dictionary(
                Varnish=_separation("Varnish", (0.0, 0.62, 0.0, 0.0)),
                CutContour=_separation("CutContour", (0.0, 0.95, 0.2, 0.0)),
                Personalization=_separation("Personalization", (0.72, 0.0, 0.68, 0.08)),
            ),
        )
        page.Contents = pdf.make_stream(_content_stream())
        pdf.docinfo[pikepdf.Name.Title] = pikepdf.String("spotpdf synthetic demo")
        pdf.docinfo[pikepdf.Name.Author] = pikepdf.String("spotpdf contributors")
        pdf.save(output, force_version="1.7", compress_streams=True)


def _base14_font(name: str) -> pikepdf.Dictionary:
    return pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type1,
        BaseFont=pikepdf.Name(f"/{name}"),
    )


def _separation(name: str, cmyk: tuple[float, float, float, float]) -> pikepdf.Array:
    return pikepdf.Array(
        [
            pikepdf.Name.Separation,
            pikepdf.Name(f"/{name}"),
            pikepdf.Name.DeviceCMYK,
            pikepdf.Dictionary(
                FunctionType=2,
                Domain=pikepdf.Array([0, 1]),
                C0=pikepdf.Array([0, 0, 0, 0]),
                C1=pikepdf.Array(cmyk),
                N=1,
            ),
        ]
    )


def _content_stream() -> bytes:
    lines = [
        # Page and header.
        "0.965 0.973 0.985 rg 0 0 720 420 re f",
        "0.055 0.075 0.12 rg 0 326 720 94 re f",
        _text("F2", 29, 48, 370, "spotpdf", gray=1.0),
        _text(
            "F1",
            12,
            48,
            347,
            "Remove named spot-color objects. Keep the PDF as vector artwork.",
            gray=0.78,
        ),
        # Three process-color cards.
        "1 g 48 66 194 226 re f",
        "1 g 263 66 194 226 re f",
        "1 g 478 66 194 226 re f",
        "0.82 G 0.8 w 48 66 194 226 re S",
        "0.82 G 0.8 w 263 66 194 226 re S",
        "0.82 G 0.8 w 478 66 194 226 re S",
        _text("F2", 11, 64, 270, "PROCESS CMYK", gray=0.16),
        _text("F2", 11, 279, 270, "SPOT FILL", gray=0.16),
        _text("F2", 11, 494, 270, "SPOT STROKE", gray=0.16),
        # Process object that remains.
        "0.76 0.20 0 0.04 k 64 112 162 124 re f",
        _text("F2", 20, 82, 168, "STAYS", gray=1.0),
        _text("F1", 9, 80, 94, "DeviceCMYK artwork", gray=0.38),
        # Spot Varnish fill and label.
        "/Varnish cs 0.82 scn 279 112 162 124 re f",
        _text("F2", 20, 305, 168, "VARNISH", gray=1.0),
        _text("F1", 9, 297, 94, "Removed by --all", gray=0.38),
        # Process product shape with a removable cut contour.
        "0.12 0.04 0 0 k 500 128 150 94 re f",
        "/CutContour CS 1 SCN 3 w [9 5] 0 d 493 121 164 108 re S",
        "[] 0 d",
        "/CutContour cs 1 scn",
        _text("F2", 14, 518, 164, "CUT LINE", color_already_set=True),
        _text("F1", 9, 514, 94, "Stroke and text removed", gray=0.38),
        # A removable personalized text line across the page.
        "/Personalization cs 1 scn",
        _text(
            "F2",
            14,
            48,
            34,
            "CUSTOM NAME - PERSONALIZATION SPOT",
            color_already_set=True,
        ),
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def _text(
    font: str,
    size: int,
    x: int,
    y: int,
    value: str,
    *,
    gray: float | None = None,
    color_already_set: bool = False,
) -> str:
    escaped = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    prefix = "" if color_already_set else f"{gray if gray is not None else 0} g "
    return f"{prefix}BT /{font} {size} Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="destination PDF")
    args = parser.parse_args()
    build_demo_pdf(args.output)


if __name__ == "__main__":
    main()
