"""Create genuine spot-color image seals without platform-specific fonts."""

from __future__ import annotations

from io import BytesIO

import pikepdf
import pypdfium2 as pdfium
from reportlab.pdfgen import canvas


def seal_mask(top: str, middle: str, bottom: str) -> bytes:
    """Rasterize a synthetic knockout seal using PDF base-14 text."""
    buffer = BytesIO()
    drawing = canvas.Canvas(buffer, pagesize=(600, 600), invariant=1)
    drawing.setFillColorRGB(0, 0, 0)
    drawing.rect(0, 0, 600, 600, fill=1, stroke=0)
    drawing.setFillColorRGB(1, 1, 1)
    drawing.circle(300, 300, 288, fill=1, stroke=0)
    drawing.setStrokeColorRGB(0, 0, 0)
    for radius, width in ((265, 4), (252, 2)):
        drawing.setLineWidth(width)
        drawing.circle(300, 300, radius, fill=0, stroke=1)
    drawing.setFillColorRGB(0, 0, 0)
    for label, y, size in ((top, 421, 43), (middle, 270, 125), (bottom, 168, 40)):
        drawing.setFont("Helvetica-Bold", size)
        drawing.drawCentredString(300, y, label)
    drawing.setLineWidth(3)
    drawing.line(165, 385, 435, 385)
    drawing.line(165, 230, 435, 230)
    drawing.showPage()
    drawing.save()
    with pdfium.PdfDocument(buffer.getvalue()) as doc:
        page = doc[0]
        bitmap = page.render(scale=1)
        try:
            return bitmap.to_pil().convert("L").tobytes()
        finally:
            bitmap.close()
            page.close()


def add_seals(pdf: pikepdf.Pdf) -> None:
    """Add two intentionally unsupported FOIL_GOLD image uses to the brochure."""
    tint = pdf.make_indirect(
        pikepdf.Dictionary(
            FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0.15, 0.34, 0.84, 0.13], N=1
        )
    )
    spot = pdf.make_indirect(
        pikepdf.Array(
            [pikepdf.Name.Separation, pikepdf.Name("/FOIL_GOLD"), pikepdf.Name.DeviceCMYK, tint]
        )
    )
    placements = [
        (("SPECIAL LOT", "250", "LIMITED EDITION"), (434, 480, 108, 108)),
        (("SELECTED", "100%", "ARABICA"), (440, 104, 82, 82)),
    ]
    for page, (labels, (x, y, width, height)) in zip(pdf.pages, placements, strict=True):
        mask = image_stream(pdf, seal_mask(*labels), pikepdf.Name.DeviceGray)
        image = image_stream(pdf, bytes([255]) * (600 * 600), spot)
        image.SMask = mask
        xobjects = pikepdf.Dictionary(page.Resources.get("/XObject", {}))
        xobjects["/GoldSeal"] = image
        page.Resources.XObject = xobjects
        page.contents_add(f"q {width} 0 0 {height} {x} {y} cm /GoldSeal Do Q".encode())


def image_stream(pdf: pikepdf.Pdf, data: bytes, color_space) -> pikepdf.Stream:
    image = pdf.make_stream(data)
    image.Type = pikepdf.Name.XObject
    image.Subtype = pikepdf.Name.Image
    image.Width = image.Height = 600
    image.BitsPerComponent = 8
    image.ColorSpace = color_space
    return image
