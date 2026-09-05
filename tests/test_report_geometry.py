"""Known-coordinate fixtures verify provenance independently of HTML presentation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium

from spotpdf.report_render import marked_bounds
from spotpdf.report_trace import Trace


def make_pdf(path, *, forms=False, rotate=0, crop=False):
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(300, 300))
        function = pikepdf.Dictionary(
            FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1
        )
        spot = pdf.make_indirect(
            pikepdf.Array(["/Separation", "/Varnish", "/DeviceCMYK", function])
        )
        # PDF names must be names rather than strings.
        for i in (0, 1, 2):
            spot[i] = pikepdf.Name(str(spot[i]))
        image = pdf.make_stream(b"\xff")
        image.Type = pikepdf.Name.XObject
        image.Subtype = pikepdf.Name.Image
        image.Width = image.Height = 1
        image.BitsPerComponent = 8
        image.ColorSpace = spot
        resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Im=image), ColorSpace=pikepdf.Dictionary(CS=spot)
        )
        content = b"q 40 0 0 30 20 50 cm /Im Do Q /CS cs 1 scn 100 120 40 20 re f"
        if forms:
            form = pdf.make_stream(content)
            form.Type = pikepdf.Name.XObject
            form.Subtype = pikepdf.Name.Form
            form.BBox = [0, 0, 300, 300]
            form.Resources = resources
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm=form))
            page.Contents = pdf.make_stream(
                b"q .5 0 0 .5 10 20 cm /Fm Do Q q .5 0 0 .5 150 120 cm /Fm Do Q"
            )
        else:
            page.Resources = resources
            page.Contents = pdf.make_stream(content)
        page.Rotate = rotate
        if crop:
            page.CropBox = [10, 20, 280, 290]
        pdf.save(path)


class ReportGeometryTests(unittest.TestCase):
    def test_marks_preserve_render_and_map_repeated_form_uses(self):
        for forms in (False, True):
            with self.subTest(forms=forms), tempfile.TemporaryDirectory() as name:
                source, annotated = Path(name) / "source.pdf", Path(name) / "marked.pdf"
                make_pdf(source, forms=forms)
                with pikepdf.open(source) as pdf:
                    trace = Trace()
                    trace.instrument(pdf, [1])
                    self.assertEqual(trace.gaps, [])
                    pdf.save(annotated)
                with pdfium.PdfDocument(source) as before, pdfium.PdfDocument(annotated) as after:
                    a, b = before[0].render(), after[0].render()
                    self.assertEqual(a.to_pil().tobytes(), b.to_pil().tobytes())
                    bounds = marked_bounds(after[0], trace)
                    images = [
                        box
                        for mark, boxes in bounds.items()
                        if trace.entries[mark]["operator"] == "Do"
                        and (not forms or len(trace.entries[mark]["form_chain"]) == 1)
                        for box in boxes
                    ]
                    expected = (
                        [[20, 50, 60, 80]]
                        if not forms
                        else [[20, 45, 40, 60], [160, 145, 180, 160]]
                    )
                    self.assertEqual(images, expected)
                    a.close()
                    b.close()


if __name__ == "__main__":
    unittest.main()
