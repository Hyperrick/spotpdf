from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import pikepdf
from test_report_geometry import make_pdf

from spotpdf.cli import main
from spotpdf.diagnostics import Finding
from spotpdf.report_html import document


class ReportFindingsTests(unittest.TestCase):
    def test_shared_image_is_grouped_across_pages_and_annotation_has_rectangle(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source, edited, report = root / "source.pdf", root / "edited.pdf", root / "report.html"
            make_pdf(source)
            with pikepdf.open(source) as pdf:
                page = pdf.pages[0]
                appearance = pdf.make_stream(b"/CS cs 1 scn 0 0 40 20 re f")
                appearance.Type = pikepdf.Name.XObject
                appearance.Subtype = pikepdf.Name.Form
                appearance.BBox = [0, 0, 40, 20]
                appearance.Resources = page.Resources
                annotation = pdf.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.Annot,
                        Subtype=pikepdf.Name.Stamp,
                        Rect=[30, 180, 70, 200],
                        AP=pikepdf.Dictionary(N=appearance),
                    )
                )
                page.Annots = [annotation]
                second = pdf.add_blank_page(page_size=(300, 300))
                second.Resources = page.Resources
                second.Contents = pdf.make_stream(b"q 40 0 0 30 120 50 cm /Im Do Q")
                pdf.save(edited)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = main(
                    [
                        "remove",
                        str(edited),
                        "--spot",
                        "Varnish",
                        "--dry-run",
                        "--report",
                        str(report),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(status, 1)
            payload = json.loads(err.getvalue())
            findings = payload["error"]["details"]["findings"]
            image_findings = [f for f in findings if f["rule"] == "spot_image"]
            self.assertEqual(len(image_findings), 1)
            self.assertEqual(
                {o["page"] for o in image_findings[0]["occurrences"] if "bbox" in o}, {1, 2}
            )
            primary = next(f for f in findings if f["primary"])
            self.assertTrue(
                any(
                    o.get("bbox") == [30, 180, 70, 200] and o["accuracy"] == "surrounding area"
                    for o in primary["occurrences"]
                )
            )
            self.assertIn("surrounding area", report.read_text())

    def test_additional_alternate_failures_remain_operation_specific(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source.pdf"
            with pikepdf.Pdf.new() as pdf:
                for _ in range(2):
                    page = pdf.add_blank_page()
                    function = pikepdf.Dictionary(
                        FunctionType=2, Domain=[0, 1], C0=[0], C1=[1], N=1
                    )
                    page.Resources = pikepdf.Dictionary(
                        ColorSpace=pikepdf.Dictionary(
                            CS=pikepdf.Array(
                                [
                                    pikepdf.Name.Separation,
                                    pikepdf.Name.Varnish,
                                    pikepdf.Name("/Bogus"),
                                    function,
                                ]
                            )
                        )
                    )
                    page.Contents = pdf.make_stream(b"/CS cs 1 scn 10 10 20 20 re f")
                pdf.save(source)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = main(
                    [
                        "set-alternate",
                        str(source),
                        "--spot",
                        "Varnish",
                        "--cmyk",
                        "0,0,0,50",
                        "--dry-run",
                        "--report",
                        str(root / "report.html"),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(status, 1)
            findings = json.loads(err.getvalue())["error"]["details"]["findings"]
            locations = [f["location"] or "" for f in findings]
            self.assertTrue(any("page 1" in v for v in locations), locations)
            self.assertTrue(any("page 2" in v for v in locations), locations)

    def test_malformed_annotation_container_still_refuses_mutation(self):
        from spotpdf.model import UnsupportedSpotUseError
        from spotpdf.scan import validate_spot_uses_for_removal

        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "source.pdf"
            make_pdf(source)
            with pikepdf.open(source) as pdf:
                page = pdf.pages[0]
                page.Annots = pikepdf.Dictionary(AP=page.Resources)
                with self.assertRaises(UnsupportedSpotUseError):
                    validate_spot_uses_for_removal(pdf, frozenset({"Varnish"}))

    def test_hostile_pdf_text_is_escaped(self):
        hostile = '</script><img src=x onerror=alert(1)> & "'
        request = {"input_name": hostile, "command": "remove"}
        rendered = document(
            request,
            [Finding("unsupported_spot_use", hostile, [hostile])],
            [],
            [],
            "Operation failed",
        ).decode()
        self.assertNotIn("<img src=x", rendered)
        self.assertEqual(rendered.count("<script>"), 1)
        self.assertIn("&lt;/script&gt;", rendered)


if __name__ == "__main__":
    unittest.main()
