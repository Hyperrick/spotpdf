from __future__ import annotations

import contextlib
import io
import pickle
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

import pikepdf

from spotpdf import (
    DEFAULT_PROCESSING_LIMITS,
    ProcessingBudgetExceeded,
    ProcessingLimits,
)
from spotpdf.alternate import set_alternate_cmyk
from spotpdf.budget_graph import audit_reachable_graph
from spotpdf.budget_preflight import audit_pdf
from spotpdf.cli import build_parser, main
from spotpdf.convert import convert_spot_to_cmyk
from spotpdf.document import check_spot, inspect_pdf, remove_all_spots, remove_spot
from spotpdf.limits import enforce_limit
from spotpdf.model import (
    AlternateResult,
    BatchRemovalResult,
    ConversionResult,
    InspectionReport,
    RemovalStats,
    RenameResult,
)
from spotpdf.rename import rename_spot
from tests.processing_limit_fixtures import (
    make_alias_pdf,
    make_plain_pdf,
    make_run_length_spot_pdf,
    make_shared_form_pdf,
    make_spot_pdf,
    run_length_encode_repeated,
)


class ProcessingLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_defaults_are_finite_immutable_and_have_exact_boundaries(self) -> None:
        expected = {
            "input_bytes": ("max_input_bytes", "--max-input-bytes", 805_306_368),
            "pages": ("max_pages", "--max-pages", 10_000),
            "reachable_objects": (
                "max_reachable_objects",
                "--max-reachable-objects",
                1_000_000,
            ),
            "decoded_content_bytes": (
                "max_decoded_content_bytes",
                "--max-decoded-content-bytes",
                268_435_456,
            ),
            "operators": ("max_operators", "--max-operators", 5_000_000),
        }
        for metric, (field, option, limit) in expected.items():
            with self.subTest(metric=metric):
                self.assertEqual(getattr(DEFAULT_PROCESSING_LIMITS, field), limit)
                enforce_limit(DEFAULT_PROCESSING_LIMITS, metric, limit)
                with self.assertRaises(ProcessingBudgetExceeded) as raised:
                    enforce_limit(DEFAULT_PROCESSING_LIMITS, metric, limit + 1)
                self.assertEqual(raised.exception.metric, metric)
                self.assertEqual(raised.exception.observed, limit + 1)
                self.assertEqual(raised.exception.limit, limit)
                self.assertEqual(raised.exception.field, field)
                self.assertEqual(raised.exception.option, option)

        with self.assertRaises(FrozenInstanceError):
            DEFAULT_PROCESSING_LIMITS.max_pages = 1  # type: ignore[misc]

    def test_budget_error_survives_worker_process_serialization(self) -> None:
        error = ProcessingBudgetExceeded("pages", 11, 10)

        restored = pickle.loads(pickle.dumps(error))

        self.assertIsInstance(restored, ProcessingBudgetExceeded)
        self.assertEqual(restored.metric, "pages")
        self.assertEqual(restored.observed, 11)
        self.assertEqual(restored.limit, 10)
        self.assertEqual(restored.field, "max_pages")
        self.assertEqual(restored.option, "--max-pages")
        self.assertEqual(str(restored), str(error))

    def test_invalid_programmatic_limits_are_rejected(self) -> None:
        invalid = (True, False, 0, -1, 1.0, "1", b"1")
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max_pages"):
                ProcessingLimits(max_pages=value)  # type: ignore[arg-type]

        disabled = ProcessingLimits(
            max_input_bytes=None,
            max_pages=None,
            max_reachable_objects=None,
            max_decoded_content_bytes=None,
            max_operators=None,
        )
        self.assertIsNone(disabled.max_pages)

    def test_real_pdf_succeeds_at_each_exact_limit_and_fails_one_below(self) -> None:
        source = self._make_spot_pdf(pages=2)
        usage = self._usage(source)
        metrics = {
            "max_input_bytes": usage.input_bytes,
            "max_pages": usage.pages,
            "max_reachable_objects": usage.reachable_objects,
            "max_decoded_content_bytes": usage.decoded_content_bytes,
            "max_operators": usage.operators,
        }
        for field, observed in metrics.items():
            with self.subTest(field=field, boundary="exact"):
                inspect_pdf(source, limits=self._only_limit(field, observed))
            with self.subTest(field=field, boundary="below"):
                with self.assertRaises(ProcessingBudgetExceeded) as raised:
                    inspect_pdf(source, limits=self._only_limit(field, observed - 1))
                self.assertEqual(raised.exception.limit, observed - 1)

    def test_input_size_is_rejected_before_pikepdf_open(self) -> None:
        source = self._make_spot_pdf()
        limits = self._only_limit("max_input_bytes", source.stat().st_size - 1)

        with (
            mock.patch("spotpdf.publication.pikepdf.open") as open_pdf,
            self.assertRaises(ProcessingBudgetExceeded),
        ):
            inspect_pdf(source, limits=limits)

        open_pdf.assert_not_called()

    def test_read_only_library_paths_accept_strings(self) -> None:
        source = self._make_spot_pdf()

        self.assertIn("DemoSpot", inspect_pdf(str(source)).spots)
        self.assertTrue(check_spot(str(source), "DemoSpot"))

    def test_compressed_content_is_charged_by_decoded_size(self) -> None:
        decoded = b"% " + b"A" * (1024 * 1024 - 3) + b"\n"
        source = self.root / "compressed.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary()
            page.Contents = pdf.make_stream(decoded)
            pdf.save(source, compress_streams=True)

        with pikepdf.open(source) as pdf:
            stream = pdf.pages[0].Contents
            self.assertEqual(stream.Filter, pikepdf.Name.FlateDecode)
            self.assertEqual(len(stream.read_bytes()), len(decoded))
            self.assertLess(len(stream.read_raw_bytes()), len(decoded))

        inspect_pdf(
            source,
            limits=self._only_limit("max_decoded_content_bytes", len(decoded)),
        )
        with self.assertRaises(ProcessingBudgetExceeded) as raised:
            inspect_pdf(
                source,
                limits=self._only_limit("max_decoded_content_bytes", len(decoded) - 1),
            )
        self.assertEqual(raised.exception.metric, "decoded_content_bytes")

    def test_run_length_content_remains_supported_and_fully_charged(self) -> None:
        decoded = b" " * (256 * 1024)
        encoded = self._run_length_encode_repeated(decoded[0], len(decoded))
        source = self.root / "run-length.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary()
            stream = pdf.make_stream(encoded)
            stream.Filter = pikepdf.Name.RunLengthDecode
            page.Contents = stream
            pdf.save(source, compress_streams=False)

        with pikepdf.open(source) as pdf:
            stream = pdf.pages[0].Contents
            decoded_buffer = stream.get_stream_buffer(pikepdf.StreamDecodeLevel.specialized)
            self.assertEqual(len(decoded_buffer), len(decoded))
            self.assertLess(len(stream.read_raw_bytes()), len(decoded))
            self.assertEqual(pikepdf.parse_content_stream(pdf.pages[0]), [])

        inspect_pdf(
            source,
            limits=self._only_limit("max_decoded_content_bytes", len(decoded)),
        )
        with self.assertRaises(ProcessingBudgetExceeded):
            inspect_pdf(
                source,
                limits=self._only_limit("max_decoded_content_bytes", len(decoded) - 1),
            )

    def test_all_mutations_accept_valid_run_length_content(self) -> None:
        source = self._make_run_length_spot_pdf()
        operations = (
            lambda output: remove_spot(source, output, "DemoSpot"),
            lambda output: remove_all_spots(source, output),
            lambda output: rename_spot(source, output, "DemoSpot", "RenamedSpot"),
            lambda output: set_alternate_cmyk(
                source,
                output,
                "DemoSpot",
                (100, 0, 0, 0),
            ),
            lambda output: convert_spot_to_cmyk(source, output, "DemoSpot", (100, 0, 0, 0)),
        )
        for index, operation in enumerate(operations):
            with self.subTest(index=index):
                output = self.root / f"run-length-output-{index}.pdf"
                operation(output)
                self.assertTrue(output.is_file())
                inspect_pdf(output)

    def test_content_operators_are_counted_incrementally(self) -> None:
        source = self._make_plain_pdf((b"q\nQ\n" * 32,))
        usage = self._usage(source)
        self.assertEqual(usage.operators, 64)

        inspect_pdf(source, limits=self._only_limit("max_operators", 64))
        with self.assertRaises(ProcessingBudgetExceeded) as raised:
            inspect_pdf(source, limits=self._only_limit("max_operators", 63))
        self.assertEqual(raised.exception.observed, 64)

    def test_inline_image_lexical_operators_match_documented_semantics(self) -> None:
        source = self._make_plain_pdf((b"BI /W 1 /H 1 /CS /RGB /BPC 8 ID abc EI\n",))

        self.assertEqual(self._usage(source).operators, 3)
        inspect_pdf(source, limits=self._only_limit("max_operators", 3))
        with self.assertRaises(ProcessingBudgetExceeded):
            inspect_pdf(source, limits=self._only_limit("max_operators", 2))

    def test_page_contents_array_is_one_operator_sequence(self) -> None:
        path = self.root / "split-contents.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary()
            page.Contents = pikepdf.Array(
                [pdf.make_stream(b"1 0 0"), pdf.make_stream(b" 1 0 0 cm\n")]
            )
            pdf.save(path)

        usage = self._usage(path)
        self.assertEqual(usage.decoded_content_bytes, len(b"1 0 0") + len(b" 1 0 0 cm\n"))
        self.assertEqual(usage.operators, 1)

    def test_shared_form_content_is_charged_once(self) -> None:
        source = self._make_shared_form_pdf()
        usage = self._usage(source)
        self.assertEqual(usage.decoded_content_bytes, 2 * len(b"/Shared Do\n") + len(b"q\nQ\n"))
        self.assertEqual(usage.operators, 4)

    def test_alias_edges_are_charged_but_shared_targets_expand_once(self) -> None:
        single = self._make_alias_pdf(aliases=1)
        many = self._make_alias_pdf(aliases=128)
        self.assertEqual(
            self._usage(many).reachable_objects - self._usage(single).reachable_objects,
            127,
        )

    def test_large_direct_container_stops_at_limit_plus_one(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            pdf.Root.Big = pikepdf.Array(range(10_000))
            limits = self._only_limit("max_reachable_objects", 20)

            with self.assertRaises(ProcessingBudgetExceeded) as raised:
                audit_reachable_graph(pdf, limits)

        self.assertEqual(raised.exception.metric, "reachable_objects")
        self.assertEqual(raised.exception.observed, 21)
        self.assertIn("reachable graph entries 21 > 20", str(raised.exception))

    def test_every_mutation_preserves_forced_output_for_every_budget(self) -> None:
        source = self._make_spot_pdf(pages=2)
        operations = (
            (
                "remove",
                lambda output, limits: remove_spot(
                    source,
                    output,
                    "DemoSpot",
                    force=True,
                    limits=limits,
                ),
            ),
            (
                "remove-all",
                lambda output, limits: remove_all_spots(
                    source,
                    output,
                    force=True,
                    limits=limits,
                ),
            ),
            (
                "rename",
                lambda output, limits: rename_spot(
                    source,
                    output,
                    "DemoSpot",
                    "RenamedSpot",
                    force=True,
                    limits=limits,
                ),
            ),
            (
                "set-alternate",
                lambda output, limits: set_alternate_cmyk(
                    source,
                    output,
                    "DemoSpot",
                    (100, 0, 0, 0),
                    force=True,
                    limits=limits,
                ),
            ),
            (
                "convert",
                lambda output, limits: convert_spot_to_cmyk(
                    source, output, "DemoSpot", (100, 0, 0, 0), force=True, limits=limits
                ),
            ),
        )
        tight_limits = {
            "input": self._only_limit("max_input_bytes", 1),
            "pages": self._only_limit("max_pages", 1),
            "graph": self._only_limit("max_reachable_objects", 1),
            "decoded": self._only_limit("max_decoded_content_bytes", 1),
            "operators": self._only_limit("max_operators", 1),
        }
        for operation_name, operation in operations:
            for metric, limits in tight_limits.items():
                with self.subTest(operation=operation_name, metric=metric):
                    output = self.root / f"{operation_name}-{metric}.pdf"
                    output.write_bytes(b"keep-existing")
                    with self.assertRaises(ProcessingBudgetExceeded):
                        operation(output, limits)
                    self.assertEqual(output.read_bytes(), b"keep-existing")
                    self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_all_library_entrypoints_reject_accidental_none_limits(self) -> None:
        source = self._make_spot_pdf()
        output = self.root / "existing.pdf"
        output.write_bytes(b"keep-existing")
        calls = (
            lambda: inspect_pdf(source, limits=None),  # type: ignore[arg-type]
            lambda: check_spot(source, "DemoSpot", limits=None),  # type: ignore[arg-type]
            lambda: remove_spot(  # type: ignore[arg-type]
                source, output, "DemoSpot", force=True, limits=None
            ),
            lambda: remove_all_spots(  # type: ignore[arg-type]
                source, output, force=True, limits=None
            ),
            lambda: rename_spot(  # type: ignore[arg-type]
                source, output, "DemoSpot", "Renamed", force=True, limits=None
            ),
            lambda: set_alternate_cmyk(  # type: ignore[arg-type]
                source, output, "DemoSpot", (0, 0, 0, 0), force=True, limits=None
            ),
            lambda: convert_spot_to_cmyk(  # type: ignore[arg-type]
                source, output, "DemoSpot", (0, 0, 0, 0), force=True, limits=None
            ),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesRegex(TypeError, "ProcessingLimits"):
                    call()
                self.assertEqual(output.read_bytes(), b"keep-existing")

    def test_cli_exposes_overrides_on_every_subcommand(self) -> None:
        parser = build_parser()
        override = [
            "--max-input-bytes",
            "11",
            "--max-pages",
            "12",
            "--max-reachable-objects",
            "13",
            "--max-decoded-content-bytes",
            "14",
            "--max-operators",
            "15",
        ]
        commands = (
            ["list", "input.pdf"],
            ["check", "input.pdf", "--spot", "DemoSpot"],
            ["remove", "input.pdf", "--all", "-o", "output.pdf"],
            [
                "rename",
                "input.pdf",
                "--spot",
                "DemoSpot",
                "--to",
                "Renamed",
                "-o",
                "output.pdf",
            ],
            [
                "set-alternate",
                "input.pdf",
                "--spot",
                "DemoSpot",
                "--cmyk",
                "0,0,0,0",
                "-o",
                "output.pdf",
            ],
            [
                "convert",
                "input.pdf",
                "--spot",
                "DemoSpot",
                "--to-cmyk",
                "0,0,0,0",
                "-o",
                "output.pdf",
            ],
        )
        for command in commands:
            with self.subTest(command=command[0]):
                args = parser.parse_args([*command, *override])
                self.assertEqual(args.max_input_bytes, 11)
                self.assertEqual(args.max_pages, 12)
                self.assertEqual(args.max_reachable_objects, 13)
                self.assertEqual(args.max_decoded_content_bytes, 14)
                self.assertEqual(args.max_operators, 15)

    def test_cli_rejects_invalid_values_and_reports_budget_failure_cleanly(self) -> None:
        parser = build_parser()
        for value in ("0", "-1", "word"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    parser.parse_args(["list", "input.pdf", "--max-pages", value])
                self.assertEqual(raised.exception.code, 64)

        source = self._make_spot_pdf(pages=2)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["list", str(source), "--max-pages", "1"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "spotpdf: error: processing budget exceeded: pages 2 > 1 "
            "(raise this limit with --max-pages or ProcessingLimits(max_pages=...) "
            "for a trusted large job)\n",
        )
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_native_decode_warning_becomes_one_cli_error(self) -> None:
        source = self.root / "bad-filter.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary()
            stream = pdf.make_stream(b"not-deflate")
            stream.Filter = pikepdf.Name.FlateDecode
            page.Contents = stream
            pdf.save(source, compress_streams=False)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["list", str(source)])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertTrue(stderr.getvalue().startswith("spotpdf: error: cannot validate PDF safely:"))
        self.assertNotIn("WARNING", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_passes_fresh_limits_to_all_seven_library_entrypoints(self) -> None:
        cases = (
            (
                "spotpdf.cli.inspect_pdf",
                ["list", "input.pdf"],
                InspectionReport(),
            ),
            (
                "spotpdf.cli.check_spot",
                ["check", "input.pdf", "--spot", "DemoSpot"],
                False,
            ),
            (
                "spotpdf.cli.remove_spot",
                ["remove", "input.pdf", "--spot", "DemoSpot", "-o", "output.pdf"],
                RemovalStats(),
            ),
            (
                "spotpdf.cli.remove_all_spots",
                ["remove", "input.pdf", "--all", "-o", "output.pdf"],
                BatchRemovalResult((), RemovalStats()),
            ),
            (
                "spotpdf.cli.rename_spot",
                [
                    "rename",
                    "input.pdf",
                    "--spot",
                    "DemoSpot",
                    "--to",
                    "Renamed",
                    "-o",
                    "output.pdf",
                ],
                RenameResult("DemoSpot", "Renamed", 1, 0),
            ),
            (
                "spotpdf.cli.set_alternate_cmyk",
                [
                    "set-alternate",
                    "input.pdf",
                    "--spot",
                    "DemoSpot",
                    "--cmyk",
                    "0,0,0,0",
                    "-o",
                    "output.pdf",
                ],
                AlternateResult("DemoSpot", (0, 0, 0, 0), 1),
            ),
            (
                "spotpdf.cli.convert_spot_to_cmyk",
                [
                    "convert",
                    "input.pdf",
                    "--spot",
                    "DemoSpot",
                    "--to-cmyk",
                    "0,0,0,0",
                    "-o",
                    "output.pdf",
                ],
                ConversionResult("DemoSpot", (0, 0, 0, 0), 1, 1, 1, 0, 2, (1,)),
            ),
        )
        for target, command, result in cases:
            with self.subTest(target=target), mock.patch(target) as patched:
                patched.return_value = result
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = main([*command, "--max-pages", "42"])
                self.assertEqual(exit_code, 0)
                limits = patched.call_args.kwargs["limits"]
                self.assertIsInstance(limits, ProcessingLimits)
                self.assertEqual(limits.max_pages, 42)

    def _usage(self, source: Path):
        unlimited = ProcessingLimits(
            max_input_bytes=None,
            max_pages=None,
            max_reachable_objects=None,
            max_decoded_content_bytes=None,
            max_operators=None,
        )
        with pikepdf.open(source, attempt_recovery=False) as pdf:
            return audit_pdf(pdf, unlimited, input_bytes=source.stat().st_size)

    @staticmethod
    def _only_limit(field: str, limit: int) -> ProcessingLimits:
        values = {
            "max_input_bytes": None,
            "max_pages": None,
            "max_reachable_objects": None,
            "max_decoded_content_bytes": None,
            "max_operators": None,
        }
        values[field] = limit
        return ProcessingLimits(**values)

    def _make_spot_pdf(self, *, pages: int = 1) -> Path:
        return make_spot_pdf(self.root, pages=pages)

    def _make_run_length_spot_pdf(self) -> Path:
        return make_run_length_spot_pdf(self.root)

    def _make_plain_pdf(self, contents: tuple[bytes, ...]) -> Path:
        return make_plain_pdf(self.root, contents)

    def _make_shared_form_pdf(self) -> Path:
        return make_shared_form_pdf(self.root)

    def _make_alias_pdf(self, *, aliases: int) -> Path:
        return make_alias_pdf(self.root, aliases=aliases)

    @staticmethod
    def _run_length_encode_repeated(value: int, length: int) -> bytes:
        return run_length_encode_repeated(value, length)


if __name__ == "__main__":
    unittest.main()
