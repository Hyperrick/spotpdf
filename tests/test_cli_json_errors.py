from __future__ import annotations

import contextlib
import io
import json
import subprocess
import unittest

import pikepdf

from spotpdf.cli_output import emit_runtime_error
from spotpdf.cli_parser import build_parser
from spotpdf.limits import ProcessingBudgetExceeded
from spotpdf.model import (
    InvalidPdfError,
    NestingLimitExceededError,
    SpotPdfError,
    UnsupportedSpotUseError,
)
from tests.cli_json_helpers import PROJECT_ROOT, JsonCliTestCase


class JsonCliErrorTests(JsonCliTestCase):
    def test_usage_errors_are_json_and_uniquely_exit_64(self) -> None:
        output = self.root / "unused.pdf"
        cases = (
            ([], None),
            (["unknown"], None),
            (["list"], "list"),
            (["check", str(self.source)], "check"),
            (["remove", str(self.source), "-o", str(output)], "remove"),
            (
                [
                    "remove",
                    str(self.source),
                    "--all",
                    "--spot",
                    "Varnish",
                    "-o",
                    str(output),
                ],
                "remove",
            ),
            (["rename", str(self.source), "--spot", "Varnish", "-o", str(output)], "rename"),
            (
                [
                    "set-alternate",
                    str(self.source),
                    "--spot",
                    "Varnish",
                    "--cmyk",
                    "bad",
                    "-o",
                    str(output),
                ],
                "set-alternate",
            ),
            (
                [
                    "convert",
                    str(self.source),
                    "--spot",
                    "Varnish",
                    "--to-cmyk",
                    "0,0,0",
                    "-o",
                    str(output),
                ],
                "convert",
            ),
            (["list", str(self.source), "--max-pages", "0"], "list"),
        )
        for arguments, command in cases:
            with self.subTest(arguments=arguments):
                completed = self._run("--format", "json", *arguments)
                payload = self._error(completed, code="usage_error", exit_code=64)
                self.assertEqual(payload["command"], command)
                self.assertEqual(payload["error"]["details"], {})
                self.assertFalse(output.exists())

    def test_trailing_json_formats_early_parser_errors(self) -> None:
        output = self.root / "unused.pdf"
        cases = (
            (["list", "--format", "json"], "list"),
            (["list", self.source, "--max-pages", "bad", "--format", "json"], "list"),
            (
                [
                    "remove",
                    self.source,
                    "--all",
                    "--spot",
                    "Varnish",
                    "-o",
                    output,
                    "--format",
                    "json",
                ],
                "remove",
            ),
            (
                [
                    "set-alternate",
                    self.source,
                    "--spot",
                    "Varnish",
                    "--cmyk",
                    "bad",
                    "-o",
                    output,
                    "--format=json",
                ],
                "set-alternate",
            ),
        )
        for arguments, command in cases:
            with self.subTest(arguments=arguments):
                payload = self._error(
                    self._run(*arguments),
                    code="usage_error",
                    exit_code=64,
                )
                self.assertEqual(payload["command"], command)
                self.assertFalse(output.exists())

    def test_format_preselection_is_exact_ordered_and_stops_at_double_dash(self) -> None:
        abbreviation = self._run("--form", "json", "list", self.source)
        self.assertEqual(abbreviation.returncode, 64)
        self.assertEqual(abbreviation.stdout, "")
        self.assertTrue(abbreviation.stderr.startswith("usage:"))

        leftovers = self._error(
            self._run("--format", "json", "list", self.source, "--unknown"),
            code="usage_error",
            exit_code=64,
        )
        self.assertEqual(leftovers["command"], "list")

        invalid_before_valid = self._error(
            self._run("--format", "--format=json", "list", self.source),
            code="usage_error",
            exit_code=64,
        )
        self.assertIsNone(invalid_before_valid["command"])

        last_valid_wins = self._success(
            self._run("--format", "text", "--format=json", "list", self.source),
            command="list",
        )
        self.assertEqual(last_valid_wins["result"]["input"], str(self.source))

        text_wins = self._run("--format", "json", "unknown", "--format", "text")
        self.assertEqual(text_wins.returncode, 64)
        self.assertTrue(text_wins.stderr.startswith("usage:"))

        after_double_dash = self._run("--format", "--", "--format=json")
        self.assertEqual(after_double_dash.returncode, 64)
        self.assertTrue(after_double_dash.stderr.startswith("usage:"))

    def test_reused_parser_resets_format_and_command_context(self) -> None:
        parser = build_parser()
        first_stderr = io.StringIO()
        with contextlib.redirect_stderr(first_stderr), self.assertRaises(SystemExit):
            parser.parse_args(["--format", "json", "list", str(self.source), "--unknown"])
        first_payload = json.loads(first_stderr.getvalue())
        self.assertEqual(first_payload["command"], "list")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            parser.parse_args(["list"])

        self.assertEqual(raised.exception.code, 64)
        self.assertTrue(stderr.getvalue().startswith("usage:"))

        final_stderr = io.StringIO()
        with contextlib.redirect_stderr(final_stderr), self.assertRaises(SystemExit):
            parser.parse_args(["--format", "json", "unknown"])
        final_payload = json.loads(final_stderr.getvalue())
        self.assertIsNone(final_payload["command"])

    def test_runtime_errors_use_stderr_and_structured_budget_details(self) -> None:
        missing = self._run("--format", "json", "list", self.root / "missing.pdf")
        validation = self._error(missing, code="validation_error")
        self.assertEqual(validation["command"], "list")

        budget = self._run(
            "--format",
            "json",
            "list",
            self.source,
            "--max-input-bytes",
            "1",
        )
        budget_payload = self._error(budget, code="budget_exceeded")
        details = budget_payload["error"]["details"]
        self.assertEqual(details["metric"], "input_bytes")
        self.assertEqual(details["field"], "max_input_bytes")
        self.assertEqual(details["limit"], 1)
        self.assertGreater(details["observed"], 1)
        self.assertEqual(details["option"], "--max-input-bytes")

    def test_budget_failure_preserves_forced_output(self) -> None:
        output = self.root / "existing.pdf"
        original = b"existing output"
        output.write_bytes(original)

        completed = self._run(
            "--format",
            "json",
            "remove",
            self.source,
            "--all",
            "-o",
            output,
            "--force",
            "--max-operators",
            "1",
        )

        payload = self._error(completed, code="budget_exceeded")
        self.assertEqual(payload["error"]["details"]["field"], "max_operators")
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".existing-*.tmp.pdf")), [])

    def test_unsupported_failure_preserves_forced_output(self) -> None:
        source = self.root / "devicen.pdf"
        output = self.root / "existing.pdf"
        original = b"existing output"
        output.write_bytes(original)
        self._make_devicen_pdf(source)

        completed = self._run(
            "--format",
            "json",
            "remove",
            source,
            "--spot",
            "DemoSpot",
            "-o",
            output,
            "--force",
        )

        self._error(completed, code="unsupported_spot_use")
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".existing-*.tmp.pdf")), [])

    def test_native_decode_failure_is_exactly_one_json_error(self) -> None:
        source = self.root / "bad-filter.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary()
            stream = pdf.make_stream(b"not-deflate")
            stream.Filter = pikepdf.Name.FlateDecode
            page.Contents = stream
            pdf.save(source, compress_streams=False)

        completed = self._run("--format", "json", "list", source)
        self._error(completed, code="validation_error")
        self.assertNotIn("WARNING", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_real_form_nesting_limit_has_its_own_error_code(self) -> None:
        source = self.root / "deep-forms.pdf"
        output = self.root / "deep-output.pdf"
        self._make_deep_form_pdf(source)

        completed = self._run(
            "--format",
            "json",
            "remove",
            source,
            "--spot",
            "DemoSpot",
            "-o",
            output,
        )

        self._error(completed, code="nesting_limit_exceeded")
        self.assertFalse(output.exists())

    def test_error_classification_is_stable(self) -> None:
        errors = (
            (ProcessingBudgetExceeded("pages", 2, 1), "budget_exceeded"),
            (UnsupportedSpotUseError("unsupported"), "unsupported_spot_use"),
            (NestingLimitExceededError("deep"), "nesting_limit_exceeded"),
            (InvalidPdfError("invalid"), "validation_error"),
            (pikepdf.PdfError("broken"), "pdf_error"),
            (OSError(5, "I/O"), "io_error"),
            (ValueError("value"), "invalid_input"),
            (SpotPdfError("processing"), "processing_error"),
            (RecursionError(), "nesting_limit_exceeded"),
        )
        for error, expected in errors:
            with self.subTest(error=type(error).__name__):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    emit_runtime_error("list", error, "json")
                payload = json.loads(stderr.getvalue())
                self.assertEqual(payload["error"]["code"], expected)

    def test_wire_record_uses_lf_bytes(self) -> None:
        completed = self._run_bytes("--format", "json", "list", self.source)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, b"")
        self.assertTrue(completed.stdout.endswith(b"\n"))
        self.assertNotIn(b"\r\n", completed.stdout)
        self.assertEqual(completed.stdout.count(b"\n"), 1)
        payload = json.loads(completed.stdout.decode("ascii"))
        self.assertEqual(payload["schema_version"], "spotpdf.cli/v1")

    def test_closed_status_pipe_exits_cleanly_after_atomic_publication(self) -> None:
        output = self.root / "published.pdf"
        process = subprocess.Popen(
            self._command(
                (
                    "--format",
                    "json",
                    "remove",
                    self.source,
                    "--spot",
                    "Varnish",
                    "-o",
                    output,
                )
            ),
            cwd=PROJECT_ROOT,
            env=self._environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        process.stdout.close()
        return_code = process.wait(timeout=30)
        stderr = process.stderr.read().decode("ascii")
        process.stderr.close()

        self.assertEqual(return_code, 1, stderr)
        payload = self._canonical_payload(stderr)
        self.assertEqual(payload["error"]["code"], "io_error")
        self.assertEqual(payload["exit_code"], 1)
        self.assertNotIn("Exception ignored", stderr)
        self.assertTrue(output.is_file())
        with pikepdf.Pdf.open(output):
            pass


if __name__ == "__main__":
    unittest.main()
