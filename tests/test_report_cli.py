from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pikepdf
from test_report_geometry import make_pdf

from spotpdf.cli import main
from spotpdf.report_worker import generate


class ReportCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.pdf"
        self.report = self.root / "report.html"
        make_pdf(self.source, forms=True)

    def invoke(self, *extra, command="remove"):
        out, err = io.StringIO(), io.StringIO()
        args = [
            command,
            str(self.source),
            "--spot",
            "Varnish",
            "--dry-run",
            "--report",
            str(self.report),
            "--format",
            "json",
            *extra,
        ]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(args)
        return status, json.loads(err.getvalue() if status else out.getvalue())

    def test_failure_has_both_exact_form_occurrences_and_offline_images(self):
        original = self.source.read_bytes()
        status, payload = self.invoke()
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "unsupported_spot_use")
        primary = next(f for f in payload["error"]["details"]["findings"] if f["primary"])
        positions = [o for o in primary["occurrences"] if "bbox" in o]
        self.assertEqual([o["bbox"] for o in positions], [[20, 45, 40, 60], [160, 145, 180, 160]])
        self.assertNotEqual(positions[0]["form_chain"], positions[1]["form_chain"])
        text = self.report.read_text()
        self.assertIn("data:image/png;base64,", text)
        self.assertIn('href="#raster-1"', text)
        self.assertNotIn("https://", text)
        self.assertEqual(original, self.source.read_bytes())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["report.html", "source.pdf"])

    def test_successful_dry_run_for_all_commands(self):
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            function = pikepdf.Dictionary(
                FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0, 0], N=1
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    CS=pikepdf.Array(
                        [
                            pikepdf.Name.Separation,
                            pikepdf.Name.Varnish,
                            pikepdf.Name.DeviceCMYK,
                            function,
                        ]
                    )
                )
            )
            page.Contents = pdf.make_stream(b"/CS cs 1 scn 20 30 40 50 re f")
            pdf.save(self.source)
        for command, extra in [
            ("remove", []),
            ("rename", ["--to", "New"]),
            ("set-alternate", ["--cmyk", "0,50,0,0"]),
            ("convert", ["--to-cmyk", "0,50,0,0"]),
        ]:
            with self.subTest(command=command):
                status, payload = self.invoke("--report-overwrite", *extra, command=command)
                self.assertEqual(status, 0, payload)
                self.assertEqual(payload["report"]["status"], "complete")
                self.assertTrue(payload["result"]["dry_run"])
                self.assertIn("Dry run verified", self.report.read_text())

    def test_report_collision_never_modifies_input(self):
        original = self.source.read_bytes()
        self.report = self.source
        status, payload = self.invoke("--report-overwrite")
        self.assertEqual(status, 1)
        self.assertIn("aliases", payload["error"]["message"])
        self.assertEqual(original, self.source.read_bytes())

    def test_hardlink_and_symlink_protection(self):
        self.report.hardlink_to(self.source)
        original = self.source.read_bytes()
        self.assertEqual(self.invoke("--report-overwrite")[0], 1)
        self.report.unlink()
        self.report.symlink_to(self.source)
        self.assertEqual(self.invoke("--report-overwrite")[0], 1)
        self.assertEqual(self.source.read_bytes(), original)

    def test_existing_report_requires_separate_overwrite_flag(self):
        self.report.write_text("keep")
        status, _ = self.invoke("--force")
        self.assertEqual(status, 1)
        self.assertEqual(self.report.read_text(), "keep")
        self.invoke("--report-overwrite")
        self.assertIn("<!doctype html>", self.report.read_text())

    def test_timeout_preserves_original_error_and_creates_partial_report(self):
        with patch(
            "spotpdf.report_cli.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)
        ):
            status, payload = self.invoke()
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "unsupported_spot_use")
        self.assertEqual(payload["report"]["status"], "partial")
        self.assertIn("timeout", self.report.read_text())

    def test_report_publication_failure_preserves_operation_failure(self):
        with patch("spotpdf.report_cli.publish", side_effect=OSError("disk full")):
            status, payload = self.invoke()
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "unsupported_spot_use")
        self.assertEqual(payload["report"]["status"], "failed")
        self.assertIn("disk full", payload["report"]["gaps"])

    def test_report_failure_after_publication_explicitly_reports_output(self):
        output = self.root / "out.pdf"
        out, err = io.StringIO(), io.StringIO()
        with (
            patch("spotpdf.report_cli.publish", side_effect=OSError("disk full")),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            status = main(
                [
                    "rename",
                    str(self.source),
                    "--spot",
                    "Varnish",
                    "--to",
                    "New",
                    "-o",
                    str(output),
                    "--report",
                    str(self.report),
                    "--format",
                    "json",
                ]
            )
        payload = json.loads(err.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "report_error")
        self.assertTrue(payload["report"]["output_published"])
        self.assertTrue(output.exists())

    def test_failed_strict_validation_is_not_reopened(self):
        self.source.write_bytes(b"not a PDF")
        status, payload = self.invoke()
        self.assertEqual(status, 1)
        self.assertIn("no diagnostic reopen", self.report.read_text())
        self.assertNotIn("data:image", self.report.read_text())
        request = {
            "findings": [],
            "gaps": ["validation failed"],
            "skip_input": True,
            "failed": True,
            "dry_run": True,
            "input_name": "bad.pdf",
            "command": "remove",
            "max_bytes": 100000,
        }
        with patch(
            "spotpdf.report_worker.open_strict", side_effect=AssertionError("must not open")
        ):
            generate(request, self.root)

    def test_html_budget_and_finding_limit_are_visible(self):
        status, payload = self.invoke("--report-max-bytes", "8000", "--report-max-findings", "1")
        self.assertEqual(status, 1)
        self.assertLessEqual(self.report.stat().st_size, 8000)
        self.assertEqual(payload["report"]["status"], "partial")
        self.assertLessEqual(len(payload["error"]["details"]["findings"]), 1)

    def test_positive_limits_are_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(
                [
                    "remove",
                    str(self.source),
                    "--spot",
                    "Varnish",
                    "--dry-run",
                    "--report",
                    str(self.report),
                    "--report-timeout",
                    "0",
                ]
            )


if __name__ == "__main__":
    unittest.main()
