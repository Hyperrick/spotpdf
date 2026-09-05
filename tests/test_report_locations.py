from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from test_report_geometry import make_pdf

from spotpdf.diagnostics import Finding
from spotpdf.report_render import marked_bounds, render
from spotpdf.report_trace import Trace


class ReportLocationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.pdf"
        self.annotated = self.root / "annotated.pdf"

    def test_rotation_cropbox_and_original_page_pixels(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                make_pdf(self.source, rotate=rotation, crop=True)
                with pikepdf.open(self.source) as pdf:
                    from spotpdf.diagnostics import identity

                    finding = Finding(
                        "unsupported_spot_use",
                        "image",
                        ["Varnish"],
                        identity(pdf.pages[0].Resources.XObject.Im),
                    )
                    trace = Trace()
                    trace.instrument(pdf, [1])
                    pdf.save(self.annotated)
                gaps = []
                previews = render(self.source, self.annotated, trace, [finding], [1], gaps, 1000000)
                self.assertEqual(gaps, [])
                self.assertEqual(len(previews[0]["boxes"]), 1)
                box = previews[0]["boxes"][0]["box"]
                # Convert known PDF fixture coordinates through the original page transform.
                with pdfium.PdfDocument(self.source) as doc:
                    page = doc[0]
                    bitmap = page.render(scale=1600 / max(page.get_size()))
                    transform = bitmap.get_posconv(page)
                    corners = [transform.to_bitmap(20, 50), transform.to_bitmap(60, 80)]
                    expected = [
                        min(c[0] for c in corners),
                        min(c[1] for c in corners),
                        max(c[0] for c in corners),
                        max(c[1] for c in corners),
                    ]
                    self.assertEqual(box, expected)
                    bitmap.close()
                    page.close()

    def test_text_paths_clipping_and_invisible_text_preserve_render(self):
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(300, 300))
            page.Resources = pikepdf.Dictionary(
                Font=pikepdf.Dictionary(
                    F=pikepdf.Dictionary(
                        Type=pikepdf.Name.Font,
                        Subtype=pikepdf.Name.Type1,
                        BaseFont=pikepdf.Name.Helvetica,
                    )
                )
            )
            page.Contents = pdf.make_stream(
                b"q 0 0 150 150 re W n 0.5 g 100 100 100 100 re f Q "
                b"BT /F 20 Tf 20 40 Td (Visible) Tj 3 Tr 0 30 Td (Hidden) Tj ET"
            )
            pdf.save(self.source)
        with pikepdf.open(self.source) as pdf:
            trace = Trace()
            trace.instrument(pdf, [1])
            pdf.save(self.annotated)
        with pdfium.PdfDocument(self.source) as a, pdfium.PdfDocument(self.annotated) as b:
            page_a, page_b = a[0], b[0]
            image_a, image_b = page_a.render(), page_b.render()
            self.assertEqual(image_a.to_pil().tobytes(), image_b.to_pil().tobytes())
            bounds = marked_bounds(page_b, trace)
            visible_text = [
                entry
                for mark, entry in trace.entries.items()
                if entry["operator"] == "Tj" and mark in bounds
            ]
            self.assertEqual(len(visible_text), 1)
            self.assertTrue(any(trace.entries[m]["operator"] == "f" for m in bounds))
            image_a.close()
            image_b.close()
            page_a.close()
            page_b.close()

    def test_direct_color_definition_and_stream_sequence_index(self):
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            page.Resources = pikepdf.Dictionary()
            first = pdf.make_stream(b"q 1 0 0 rg")
            second = pdf.make_stream(b"20 30 40 50 re f Q")
            page.Contents = pikepdf.Array([first, second])
            pdf.save(self.source)
        with pikepdf.open(self.source) as pdf:
            stream_id = pdf.pages[0].Contents[1].objgen
            trace = Trace()
            trace.instrument(pdf, [1])
            entry = next(e for e in trace.entries.values() if e["operator"] == "f")
            self.assertEqual(entry["operator_index"], 1)
            self.assertEqual(entry["sequence_index"], 3)
            self.assertEqual(entry["stream"], f"{stream_id[0]} {stream_id[1]} R")

    def test_cycle_and_operator_limit_are_explicit(self):
        make_pdf(self.source, forms=True)
        with pikepdf.open(self.source) as pdf:
            trace = Trace(max_operations=1)
            trace.instrument(pdf, [1])
            self.assertTrue(any("limit" in gap for gap in trace.gaps))
        with pikepdf.open(self.source) as pdf:
            form = pdf.pages[0].Resources.XObject.Fm
            form.Resources.XObject.Loop = form
            form.write(b"/Loop Do")
            trace = Trace()
            trace.instrument(pdf, [1])
            self.assertTrue(any("Cyclic" in gap for gap in trace.gaps))

    def test_existing_mark_names_cannot_forge_provenance(self):
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            page.Contents = pdf.make_stream(
                b"/SpotpdfDiagnostic << /ID 1 >> BDC 0 0 10 10 re f EMC"
            )
            trace = Trace()
            trace.instrument(pdf, [1])
            self.assertNotEqual(trace.tag_key, "ID")
            self.assertEqual(len(trace.entries), 1)
