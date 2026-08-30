from __future__ import annotations

import contextlib
import io
import json
import subprocess
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pikepdf

import spotpdf.alternate as alternate_module
import spotpdf.convert as convert_module
import spotpdf.document as document_module
import spotpdf.rename as rename_module
from spotpdf.cli import main
from spotpdf.cli_dry_run import mutation_destination
from spotpdf.model import (
    AlternateResult,
    BatchRemovalResult,
    ConversionResult,
    RemovalStats,
    RenameResult,
)
from tests.cli_json_helpers import PROJECT_ROOT, JsonCliTestCase
from tests.conversion_fixtures import make_basic_conversion_pdf


class CliDryRunTests(JsonCliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.convert_source = make_basic_conversion_pdf(self.root / "convert-source.pdf")
        self.scratch = self.root / "temporary-storage"
        self.scratch.mkdir()

    def test_every_mutation_supports_verified_json_dry_run(self) -> None:
        for index, (command, source, arguments) in enumerate(self._mutation_cases()):
            with self.subTest(command=command, arguments=arguments):
                source_before = source.read_bytes()

                completed = self._run_with_private_temp(
                    "--format",
                    "json",
                    *arguments,
                    "--dry-run",
                )

                dry_result = self._success(completed, command=command)["result"]
                self.assertIs(dry_result["dry_run"], True)
                self.assertEqual(dry_result["input"], str(source))
                self.assertNotIn("output", dry_result)
                self.assertEqual(source.read_bytes(), source_before)
                self.assertEqual(list(self.scratch.iterdir()), [])

                output = self.root / f"normal-output-{index}.pdf"
                normal = self._success(
                    self._run("--format", "json", *arguments, "-o", output),
                    command=command,
                )["result"]
                self.assertNotIn("dry_run", normal)
                self.assertEqual(normal["output"], str(output))
                self.assertEqual(
                    {key: value for key, value in dry_result.items() if key != "dry_run"},
                    {key: value for key, value in normal.items() if key != "output"},
                )

    def test_every_mutation_text_output_identifies_the_dry_run(self) -> None:
        for command, source, arguments in self._mutation_cases():
            with self.subTest(command=command, arguments=arguments):
                source_before = source.read_bytes()

                completed = self._run_with_private_temp(*arguments, "--dry-run")

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                self.assertIn("Dry run", completed.stdout)
                self.assertIn("no output published", completed.stdout)
                self.assertEqual(source.read_bytes(), source_before)
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_output_and_dry_run_are_exactly_one_required_for_every_mutation(self) -> None:
        requested_output = self.root / "must-not-exist" / "output.pdf"
        for command, _source, arguments in self._mutation_cases():
            with self.subTest(command=command, mode="missing"):
                payload = self._error(
                    self._run("--format", "json", *arguments),
                    code="usage_error",
                    exit_code=64,
                )
                self.assertEqual(payload["command"], command)
            with self.subTest(command=command, mode="conflicting"):
                payload = self._error(
                    self._run(
                        "--format",
                        "json",
                        *arguments,
                        "--dry-run",
                        "-o",
                        requested_output,
                    ),
                    code="usage_error",
                    exit_code=64,
                )
                self.assertEqual(payload["command"], command)
                self.assertFalse(requested_output.parent.exists())

    def test_help_scopes_dry_run_to_mutating_commands(self) -> None:
        for command in ("remove", "rename", "set-alternate", "convert"):
            with self.subTest(command=command):
                completed = self._run(command, "--help")
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                normalized = " ".join(completed.stdout.split())
                self.assertIn("-o OUTPUT | --dry-run", normalized)
                self.assertIn("full rewrite and post-save verification", normalized)
                self.assertIn("has no effect with --dry-run", normalized)

        for command in ("list", "check"):
            with self.subTest(command=command):
                completed = self._run(command, "--help")
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertNotIn("--dry-run", completed.stdout)

    def test_empty_remove_all_dry_run_does_not_publish_a_copy(self) -> None:
        source = self.root / "plain.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary()
            page.Contents = pdf.make_stream(b"0 g 0 0 10 10 re f\n")
            pdf.save(source)

        completed = self._run_with_private_temp(
            "--format",
            "json",
            "remove",
            source,
            "--all",
            "--dry-run",
        )

        result = self._success(completed, command="remove")["result"]
        self.assertIs(result["dry_run"], True)
        self.assertEqual(result["spots_removed"], [])
        self.assertEqual(result["stats"], self._stats(changed=False, pages_changed=[]))
        self.assertNotIn("output", result)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_failed_dry_run_discards_temporary_output_and_keeps_one_json_error(self) -> None:
        source = self.root / "unsupported.pdf"
        self._make_devicen_pdf(source)

        completed = self._run_with_private_temp(
            "--format",
            "json",
            "remove",
            source,
            "--spot",
            "DemoSpot",
            "--dry-run",
        )

        payload = self._error(completed, code="unsupported_spot_use")
        self.assertEqual(payload["command"], "remove")
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_budget_failure_and_force_noop_keep_dry_run_contract(self) -> None:
        budget = self._run_with_private_temp(
            "--format",
            "json",
            "remove",
            self.source,
            "--all",
            "--dry-run",
            "--max-operators",
            "1",
        )

        payload = self._error(budget, code="budget_exceeded")
        self.assertEqual(payload["error"]["details"]["field"], "max_operators")
        self.assertEqual(list(self.scratch.iterdir()), [])

        regular = self._success(
            self._run_with_private_temp(
                "--format",
                "json",
                "remove",
                self.source,
                "--spot",
                "Varnish",
                "--dry-run",
            ),
            command="remove",
        )["result"]
        forced = self._success(
            self._run_with_private_temp(
                "--format",
                "json",
                "remove",
                self.source,
                "--spot",
                "Varnish",
                "--dry-run",
                "--force",
            ),
            command="remove",
        )["result"]
        self.assertEqual(forced, regular)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_force_is_forwarded_only_for_a_published_output(self) -> None:
        operations = (
            ("spotpdf.cli.remove_spot", RemovalStats()),
            ("spotpdf.cli.remove_all_spots", BatchRemovalResult((), RemovalStats())),
            ("spotpdf.cli.rename_spot", RenameResult("Varnish", "Renamed", 1, 0)),
            (
                "spotpdf.cli.set_alternate_cmyk",
                AlternateResult("Varnish", (0.0, 80.0, 100.0, 0.0), 1),
            ),
            (
                "spotpdf.cli.convert_spot_to_cmyk",
                ConversionResult(
                    "DemoSpot",
                    (0.0, 80.0, 100.0, 0.0),
                    1,
                    1,
                    1,
                    0,
                    4,
                    (1,),
                ),
            ),
        )
        for index, ((command, _source, arguments), (target, result)) in enumerate(
            zip(self._mutation_cases(), operations, strict=True)
        ):
            destinations = (
                (("--dry-run",), False),
                (("-o", self.root / f"output-{index}.pdf"), True),
            )
            for destination, expected_force in destinations:
                with self.subTest(command=command, destination=destination):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        mock.patch(target, return_value=result) as operation,
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        exit_code = main(
                            [
                                *(str(item) for item in arguments),
                                *(str(item) for item in destination),
                                "--force",
                            ]
                        )

                    self.assertEqual(exit_code, 0, stderr.getvalue())
                    self.assertEqual(stderr.getvalue(), "")
                    self.assertEqual(operation.call_args.kwargs["force"], expected_force)

    def test_dry_runs_reach_each_saved_pdf_verifier(self) -> None:
        cases = (
            (
                document_module,
                (
                    "remove",
                    self.source,
                    "--spot",
                    "Varnish",
                    "--dry-run",
                ),
            ),
            (
                document_module,
                (
                    "remove",
                    self.source,
                    "--all",
                    "--dry-run",
                ),
            ),
            (
                rename_module,
                (
                    "rename",
                    self.source,
                    "--spot",
                    "Varnish",
                    "--to",
                    "Renamed",
                    "--dry-run",
                ),
            ),
            (
                alternate_module,
                (
                    "set-alternate",
                    self.source,
                    "--spot",
                    "Varnish",
                    "--cmyk",
                    "0,80,100,0",
                    "--dry-run",
                ),
            ),
            (
                convert_module,
                (
                    "convert",
                    self.convert_source,
                    "--spot",
                    "DemoSpot",
                    "--to-cmyk",
                    "0,80,100,0",
                    "--dry-run",
                ),
            ),
        )
        for module, arguments in cases:
            with self.subTest(command=arguments[0]):
                verifier = module._verify_saved_pdf
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(module, "_verify_saved_pdf", wraps=verifier) as called,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    exit_code = main(["--format", "json", *(str(item) for item in arguments)])

                self.assertEqual(exit_code, 0, stderr.getvalue())
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(json.loads(stdout.getvalue())["result"]["dry_run"], True)
                called.assert_called_once()

    def test_cleanup_failure_never_follows_a_remove_success_record(self) -> None:
        @contextmanager
        def failing_destination(
            output_path: Path | None,
            *,
            dry_run: bool,
        ) -> Iterator[Path]:
            self.assertIsNone(output_path)
            self.assertTrue(dry_run)
            yield self.root / "discarded.pdf"
            raise OSError(5, "simulated dry-run cleanup failure")

        cases = (
            (
                ("remove", self.source, "--spot", "Varnish", "--dry-run"),
                "remove_spot",
            ),
            (("remove", self.source, "--all", "--dry-run"), "remove_all_spots"),
        )
        for arguments, expected_call in cases:
            with self.subTest(arguments=arguments):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch("spotpdf.cli.mutation_destination", failing_destination),
                    mock.patch(
                        "spotpdf.cli.remove_spot",
                        return_value=RemovalStats(),
                    ) as remove_one,
                    mock.patch(
                        "spotpdf.cli.remove_all_spots",
                        return_value=BatchRemovalResult((), RemovalStats()),
                    ) as remove_all,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    exit_code = main(["--format", "json", *(str(item) for item in arguments)])

                self.assertEqual(exit_code, 1)
                self.assertEqual(stdout.getvalue(), "")
                payload = json.loads(stderr.getvalue())
                self.assertEqual(payload["error"]["code"], "io_error")
                self.assertEqual(stderr.getvalue().count("\n"), 1)
                if expected_call == "remove_spot":
                    remove_one.assert_called_once()
                    remove_all.assert_not_called()
                else:
                    remove_all.assert_called_once()
                    remove_one.assert_not_called()

    def test_destination_context_discards_files_on_success_and_failure(self) -> None:
        with mutation_destination(None, dry_run=True) as destination:
            success_parent = destination.parent
            destination.write_bytes(b"verified")
            self.assertTrue(destination.is_file())
        self.assertFalse(success_parent.exists())

        failure_parent: Path | None = None
        with (
            self.assertRaisesRegex(RuntimeError, "simulated mutation failure"),
            mutation_destination(None, dry_run=True) as destination,
        ):
            failure_parent = destination.parent
            destination.write_bytes(b"partial")
            raise RuntimeError("simulated mutation failure")
        assert failure_parent is not None
        self.assertFalse(failure_parent.exists())

        output = self.root / "published.pdf"
        with mutation_destination(output, dry_run=False) as destination:
            self.assertEqual(destination, output)
        self.assertFalse(output.exists())

        with (
            self.assertRaisesRegex(ValueError, "must not have an output"),
            mutation_destination(output, dry_run=True),
        ):
            pass
        with (
            self.assertRaisesRegex(ValueError, "requires an output"),
            mutation_destination(None, dry_run=False),
        ):
            pass

    def _mutation_cases(self) -> tuple[tuple[str, Path, tuple[object, ...]], ...]:
        return (
            (
                "remove",
                self.source,
                ("remove", self.source, "--spot", "Varnish"),
            ),
            ("remove", self.source, ("remove", self.source, "--all")),
            (
                "rename",
                self.source,
                (
                    "rename",
                    self.source,
                    "--spot",
                    "Varnish",
                    "--to",
                    "Renamed",
                ),
            ),
            (
                "set-alternate",
                self.source,
                (
                    "set-alternate",
                    self.source,
                    "--spot",
                    "Varnish",
                    "--cmyk",
                    "0,80,100,0",
                ),
            ),
            (
                "convert",
                self.convert_source,
                (
                    "convert",
                    self.convert_source,
                    "--spot",
                    "DemoSpot",
                    "--to-cmyk",
                    "0,80,100,0",
                ),
            ),
        )

    def _run_with_private_temp(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        environment = self._environment()
        for variable in ("TMPDIR", "TEMP", "TMP"):
            environment[variable] = str(self.scratch)
        return subprocess.run(
            self._command(arguments),
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
