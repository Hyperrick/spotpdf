"""Generate the English NORD Coffee brochure used by the README report example."""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

import pikepdf
from reportlab.pdfgen import canvas

try:
    from examples.report_demo.brochure import draw_brochure
    from examples.report_demo.seals import add_seals
except ModuleNotFoundError:
    from report_demo.brochure import draw_brochure
    from report_demo.seals import add_seals


def build_report_demo(output: Path) -> None:
    """Write synthetic vector/text artwork with two deliberate image-spot refusals."""
    buffer = BytesIO()
    draw_brochure(canvas.Canvas(buffer, invariant=1))
    with pikepdf.open(BytesIO(buffer.getvalue())) as pdf:
        add_seals(pdf)
        output.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(output, deterministic_id=True, force_version="1.7")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="destination PDF")
    args = parser.parse_args()
    build_report_demo(args.output)


if __name__ == "__main__":
    main()
