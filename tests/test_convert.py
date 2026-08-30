from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.cli import build_parser, main
from spotpdf.convert import convert_spot_to_cmyk
from spotpdf.document import inspect_pdf
from spotpdf.model import InvalidPdfError
from tests.conversion_fixtures import make_basic_conversion_pdf, parsed_operations, separation


class ConvertSpotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_api_converts_initial_and_explicit_tints_without_using_old_preview(self) -> None:
        source = make_basic_conversion_pdf(self.root / "source.pdf")
        output = self.root / "converted.pdf"

        result = convert_spot_to_cmyk(
            source,
            output,
            "DemoSpot",
            (0, 80, 100, 0),
        )

        self.assertEqual(result.spot, "DemoSpot")
        self.assertEqual(result.cmyk_percentages, (0.0, 80.0, 100.0, 0.0))
        self.assertEqual(result.definitions_removed, 1)
        self.assertEqual(result.resources_removed, 1)
        self.assertEqual(result.page_content_sequences_changed, 1)
        self.assertEqual(result.forms_changed, 0)
        self.assertEqual(result.color_operators_rewritten, 4)
        self.assertEqual(result.pages_affected, (1,))
        self.assertNotIn("DemoSpot", inspect_pdf(output).colorants)

        operations = parsed_operations(output)
        colors = [
            tuple(float(value) for value in operands)
            for operator, operands in operations
            if operator in {"k", "K"}
        ]
        self.assertEqual(
            colors,
            [
                (0.0, 0.8, 1.0, 0.0),
                (0.0, 0.2, 0.25, 0.0),
                (0.0, 0.8, 1.0, 0.0),
                (0.0, 0.4, 0.5, 0.0),
            ],
        )
        with pikepdf.open(output) as pdf:
            self.assertNotIn(pikepdf.Name.Ink, pdf.pages[0].Resources.ColorSpace)

    def test_cli_contract_and_success_output(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "convert",
                "input.pdf",
                "--spot",
                "DemoSpot",
                "--to-cmyk",
                "0,80,100,0",
                "-o",
                "output.pdf",
                "--force",
            ]
        )
        self.assertEqual(args.command, "convert")
        self.assertEqual(args.to_cmyk, (0.0, 80.0, 100.0, 0.0))
        self.assertTrue(args.force)

        source = make_basic_conversion_pdf(self.root / "cli-source.pdf")
        output = self.root / "cli-output.pdf"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "convert",
                    str(source),
                    "--spot",
                    "DemoSpot",
                    "--to-cmyk",
                    "0,80,100,0",
                    "-o",
                    str(output),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("explicit DeviceCMYK 0,80,100,0", stdout.getvalue())
        self.assertTrue(output.is_file())

    def test_invalid_recipe_and_missing_or_reserved_spot_preserve_forced_output(self) -> None:
        source = make_basic_conversion_pdf(self.root / "atomic-source.pdf")
        invalid_cases = (
            ("DemoSpot", (0, 0, 0)),
            ("Missing", (0, 80, 100, 0)),
            ("All", (0, 80, 100, 0)),
            ("Cyan", (0, 80, 100, 0)),
        )
        for index, (spot, recipe) in enumerate(invalid_cases):
            with self.subTest(spot=spot, recipe=recipe):
                output = self.root / f"forced-{index}.pdf"
                original = b"existing output must survive"
                output.write_bytes(original)
                with self.assertRaises(InvalidPdfError):
                    convert_spot_to_cmyk(
                        source,
                        output,
                        spot,
                        recipe,
                        force=True,
                    )
                self.assertEqual(output.read_bytes(), original)

    def test_unused_target_alias_is_removed_without_rewriting_content(self) -> None:
        source = self.root / "unused.pdf"
        output = self.root / "unused-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Contents = pdf.make_stream(b"0 0 0 rg 0 0 10 10 re f")
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        self.assertEqual(result.page_content_sequences_changed, 0)
        self.assertEqual(result.color_operators_rewritten, 0)
        self.assertEqual(result.resources_removed, 1)
        self.assertNotIn("DemoSpot", inspect_pdf(output).colorants)

    def test_shared_definition_and_scope_local_aliases_are_handled_exactly(self) -> None:
        source = self.root / "shared.pdf"
        output = self.root / "shared-output.pdf"
        pdf = pikepdf.Pdf.new()
        target = pdf.make_indirect(separation())
        other = separation("OtherSpot")
        first = pdf.add_blank_page()
        first.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=target, Other=other))
        first.Contents = pdf.make_stream(b"/Ink cs 0.25 scn 0 0 10 10 re f")
        second = pdf.add_blank_page()
        second.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=target))
        second.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 10 10 re f")
        third = pdf.add_blank_page()
        third.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation("OtherSpot"))
        )
        third.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 10 10 re f")
        third_before = third.Contents.read_bytes()
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        self.assertEqual(result.definitions_removed, 1)
        self.assertEqual(result.resources_removed, 2)
        self.assertEqual(result.page_content_sequences_changed, 2)
        with pikepdf.open(output) as converted:
            self.assertNotIn(pikepdf.Name.Ink, converted.pages[0].Resources.ColorSpace)
            self.assertNotIn(pikepdf.Name.Ink, converted.pages[1].Resources.ColorSpace)
            self.assertIn(pikepdf.Name.Other, converted.pages[0].Resources.ColorSpace)
            self.assertIn(pikepdf.Name.Ink, converted.pages[2].Resources.ColorSpace)
            self.assertEqual(converted.pages[2].Contents.read_bytes(), third_before)

    def test_page_contents_array_is_preserved_while_logical_sequence_is_rewritten(self) -> None:
        source = self.root / "contents-array.pdf"
        output = self.root / "contents-array-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Contents = pikepdf.Array(
            [
                pdf.make_stream(b"/Ink cs 0.5"),
                pdf.make_stream(b" scn 0 0 10 10 re f"),
            ]
        )
        pdf.save(source)

        convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        with pikepdf.open(output) as converted:
            contents = converted.pages[0].obj.Contents
            self.assertIsInstance(contents, pikepdf.Array)
            self.assertEqual(len(contents), 2)
            self.assertEqual(contents[1].read_bytes(), b"")
            operations = pikepdf.parse_content_stream(converted.pages[0])
            self.assertEqual(str(operations[0].operator), "k")
            self.assertEqual(str(operations[1].operator), "k")

    def test_lowercase_black_is_a_real_spot_but_matching_remains_case_sensitive(self) -> None:
        source = self.root / "lowercase-black.pdf"
        output = self.root / "lowercase-black-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation("black")))
        page.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 10 10 re f")
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "black", (0, 0, 0, 100))

        self.assertEqual(result.spot, "black")
        self.assertNotIn("black", inspect_pdf(output).colorants)
        with self.assertRaisesRegex(InvalidPdfError, "absent"):
            convert_spot_to_cmyk(
                source,
                self.root / "wrong-case.pdf",
                "BLACK",
                (0, 0, 0, 100),
            )


if __name__ == "__main__":
    unittest.main()
