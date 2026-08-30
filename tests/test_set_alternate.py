from __future__ import annotations

import contextlib
import io
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any

import pikepdf

from spotpdf.alternate import set_alternate_cmyk
from spotpdf.cli import build_parser, main
from spotpdf.document import inspect_pdf
from spotpdf.model import SpotPdfError


class SetAlternateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_cli_parser_and_command_change_only_the_alternate_preview(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "set-alternate",
                "input.pdf",
                "--spot",
                "DemoSpot",
                "--cmyk",
                "0,80,100,0",
                "-o",
                "output.pdf",
                "--force",
            ]
        )
        self.assertEqual(args.command, "set-alternate")
        self.assertEqual(args.input, Path("input.pdf"))
        self.assertEqual(args.spot, "DemoSpot")
        self.assertEqual(args.cmyk, (0.0, 80.0, 100.0, 0.0))
        self.assertEqual(args.output, Path("output.pdf"))
        self.assertTrue(args.force)

        source = self._make_basic_pdf(suffix="cli")
        output = self.root / "cli-output.pdf"
        content_before = self._page_content(source)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "set-alternate",
                    str(source),
                    "--spot",
                    "DemoSpot",
                    "--cmyk",
                    "0,80,100,0",
                    "-o",
                    str(output),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("only the alternate preview", stdout.getvalue().lower())
        self.assertEqual(self._page_content(output), content_before)
        self.assertIn("DemoSpot", inspect_pdf(output).spots)

    def test_parser_accepts_boundaries_and_rejects_nonfinite_or_invalid_percentages(self) -> None:
        parser = build_parser()
        valid = parser.parse_args(
            [
                "set-alternate",
                "input.pdf",
                "--spot",
                "DemoSpot",
                "--cmyk",
                "0,100,12.5,1e2",
                "-o",
                "output.pdf",
            ]
        )
        self.assertEqual(valid.cmyk, (0.0, 100.0, 12.5, 100.0))

        invalid_values = (
            "",
            "0,0,0",
            "0,0,0,0,0",
            "word,0,0,0",
            "-0.01,0,0,0",
            "100.01,0,0,0",
            "nan,0,0,0",
            "inf,0,0,0",
            "-inf,0,0,0",
            "1e309,0,0,0",
        )
        for value in invalid_values:
            with self.subTest(cmyk=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    parser.parse_args(
                        [
                            "set-alternate",
                            "input.pdf",
                            "--spot",
                            "DemoSpot",
                            "--cmyk",
                            value,
                            "-o",
                            "output.pdf",
                        ]
                    )
                self.assertEqual(raised.exception.code, 2)

    def test_programmatic_values_are_validated_before_forced_output_is_touched(self) -> None:
        source = self._make_basic_pdf(suffix="invalid-api")
        invalid_values: tuple[Any, ...] = (
            (),
            (0, 0, 0),
            (0, 0, 0, 0, 0),
            (-0.01, 0, 0, 0),
            (100.01, 0, 0, 0),
            (math.nan, 0, 0, 0),
            (math.inf, 0, 0, 0),
            (10**10000, 0, 0, 0),
            ("80", 0, 0, 0),
            (True, 0, 0, 0),
        )
        for value in invalid_values:
            with self.subTest(cmyk=value):
                self._assert_atomic_failure(source, "DemoSpot", value)

    def test_endpoint_adjacent_percentages_use_stable_pdf_numbers(self) -> None:
        cases = (
            ((1e-7, 0, 0, 0), (0.0, 0.0, 0.0, 0.0)),
            ((99.99999, 0, 0, 0), (1.0, 0.0, 0.0, 0.0)),
        )
        for index, (requested, expected) in enumerate(cases):
            with self.subTest(requested=requested):
                source = self._make_basic_pdf(suffix=f"precision-{index}")
                output = self.root / f"precision-{index}-output.pdf"

                result = set_alternate_cmyk(source, output, "DemoSpot", requested)

                with pikepdf.open(output) as pdf:
                    actual = pdf.pages[0].Resources.ColorSpace.Target[3].C1
                    self.assertEqual(tuple(float(value) for value in actual), expected)
                self.assertEqual(
                    result.cmyk_percentages,
                    tuple(component * 100 for component in expected),
                )

    def test_every_matching_definition_changes_consistently_without_renaming(self) -> None:
        source = self._make_multiple_definition_pdf()
        output = self.root / "all-definitions-output.pdf"
        source_before = source.read_bytes()
        contents_before = self._page_content(source)
        unrelated_before = self._resource_preview(source, page=2, resource="Other")

        set_alternate_cmyk(source, output, "DemoSpot", (0, 25, 50, 100))

        report = inspect_pdf(output)
        self.assertIn("DemoSpot", report.spots)
        self.assertNotIn("0,25,50,100", report.colorants)
        self.assertEqual(source.read_bytes(), source_before)
        self.assertEqual(self._page_content(output), contents_before)
        self.assertEqual(self._resource_preview(output, page=2, resource="Other"), unrelated_before)

        with pikepdf.open(output) as pdf:
            definitions = (
                pdf.pages[0].Resources.ColorSpace.First,
                pdf.pages[0].Resources.ColorSpace.Second,
                pdf.pages[1].Resources.ColorSpace.Third,
            )
            snapshots = {definition.unparse(resolved=True) for definition in definitions}
            self.assertEqual(len(snapshots), 1)
            for definition in definitions:
                self.assertEqual(definition[0], pikepdf.Name.Separation)
                self.assertEqual(definition[1], pikepdf.Name.DemoSpot)
                self.assertEqual(definition[2], pikepdf.Name.DeviceCMYK)
                self._assert_linear_function(definition[3], (0.0, 0.25, 0.5, 1.0))

    def test_function_type_two_maps_required_tints_linearly(self) -> None:
        source = self._make_basic_pdf(suffix="linear")
        output = self.root / "linear-output.pdf"

        set_alternate_cmyk(source, output, "DemoSpot", (20, 40, 60, 80))

        with pikepdf.open(output) as pdf:
            function = pdf.pages[0].Resources.ColorSpace.Target[3]
            self._assert_linear_function(function, (0.2, 0.4, 0.6, 0.8))
            c0 = tuple(float(value) for value in function.C0)
            c1 = tuple(float(value) for value in function.C1)
            exponent = float(function.N)
            for tint in (0.0, 0.25, 0.5, 1.0):
                actual = tuple(
                    start + tint**exponent * (end - start)
                    for start, end in zip(c0, c1, strict=True)
                )
                expected = tuple(tint * component for component in (0.2, 0.4, 0.6, 0.8))
                for observed, wanted in zip(actual, expected, strict=True):
                    self.assertAlmostEqual(observed, wanted)

    def test_mixed_separation_and_devicen_target_fails_atomically(self) -> None:
        source = self._make_mixed_devicen_pdf(process_component=False)

        self._assert_atomic_failure(source, "DemoSpot", (0, 80, 100, 0))

    def test_reserved_canonical_and_custom_process_colorants_are_rejected(self) -> None:
        for index, name in enumerate(("All", "None", "Cyan", "Magenta", "Yellow", "Black")):
            with self.subTest(name=name):
                source = self._make_basic_pdf(name=name, suffix=f"reserved-{index}")
                self._assert_atomic_failure(source, name, (0, 80, 100, 0))

        process_source = self._make_mixed_devicen_pdf(process_component=True)
        self._assert_atomic_failure(process_source, "CustomProcess", (0, 80, 100, 0))

        lowercase = self._make_basic_pdf(name="black", suffix="lowercase-black")
        lowercase_output = self.root / "lowercase-black-output.pdf"
        set_alternate_cmyk(lowercase, lowercase_output, "black", (0, 80, 100, 0))
        self.assertIn("black", inspect_pdf(lowercase_output).spots)

    def test_signed_encrypted_restricted_and_malformed_inputs_fail_atomically(self) -> None:
        cases = (
            self._make_basic_pdf(signed=True, suffix="signed"),
            self._make_encrypted_pdf(restricted=False),
            self._make_encrypted_pdf(restricted=True),
            self._make_malformed_separation_pdf(),
        )
        for source in cases:
            with self.subTest(source=source.name):
                self._assert_atomic_failure(source, "DemoSpot", (0, 80, 100, 0))

    def test_missing_spot_and_output_collisions_preserve_files(self) -> None:
        source = self._make_basic_pdf(suffix="guards")
        source_before = source.read_bytes()
        output = self.root / "collision.pdf"
        output.write_bytes(b"keep-existing")

        with self.assertRaises(SpotPdfError):
            set_alternate_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))
        self.assertEqual(output.read_bytes(), b"keep-existing")

        with self.assertRaises(SpotPdfError):
            set_alternate_cmyk(source, source, "DemoSpot", (0, 80, 100, 0), force=True)
        self.assertEqual(source.read_bytes(), source_before)

        self._assert_atomic_failure(source, "Missing", (0, 80, 100, 0))

        set_alternate_cmyk(source, output, "DemoSpot", (0, 80, 100, 0), force=True)
        self.assertIn("DemoSpot", inspect_pdf(output).spots)
        self.assertEqual(self._page_content(output), self._page_content(source))

    def _assert_atomic_failure(
        self,
        source: Path,
        spot: str,
        cmyk: Any,
    ) -> None:
        output = self.root / f"protected-{len(list(self.root.glob('protected-*.pdf')))}.pdf"
        output.write_bytes(b"keep-existing")

        with self.assertRaises(SpotPdfError):
            set_alternate_cmyk(source, output, spot, cmyk, force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def _make_basic_pdf(
        self,
        *,
        name: str = "DemoSpot",
        signed: bool = False,
        suffix: str,
    ) -> Path:
        path = self.root / f"basic-{suffix}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Target=self._separation(name, pikepdf.Name.DeviceRGB, (0.9, 0.2, 0.1)),
                    Other=self._separation("Other", pikepdf.Name.DeviceGray, (0.4,)),
                )
            )
            page.Contents = pdf.make_stream(
                b"/Target cs 0.5 scn 0 0 40 40 re f\n/Other CS 0.75 SCN 5 w 0 0 40 40 re S\n"
            )
            if signed:
                field = pdf.make_indirect(
                    pikepdf.Dictionary(FT=pikepdf.Name.Sig, T=pikepdf.String("Signature1"))
                )
                pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([field]))
            pdf.save(path)
        return path

    def _make_multiple_definition_pdf(self) -> Path:
        path = self.root / "multiple-definitions.pdf"
        with pikepdf.Pdf.new() as pdf:
            first = pdf.add_blank_page(page_size=(100, 100))
            first.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    First=self._separation("DemoSpot", pikepdf.Name.DeviceRGB, (0.9, 0.2, 0.1)),
                    Second=self._separation("DemoSpot", pikepdf.Name.DeviceGray, (0.7,)),
                )
            )
            first.Contents = pdf.make_stream(
                b"/First cs 0.25 scn 0 0 20 20 re f\n/Second CS 0.75 SCN 0 0 20 20 re S\n"
            )
            second = pdf.add_blank_page(page_size=(100, 100))
            second.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Third=self._separation(
                        "DemoSpot", pikepdf.Name.DeviceCMYK, (0.1, 0.2, 0.3, 0.4)
                    ),
                    Other=self._separation("Other", pikepdf.Name.DeviceRGB, (0.1, 0.8, 0.2)),
                )
            )
            second.Contents = pdf.make_stream(
                b"/Third cs 0.5 scn 0 0 30 30 re f\n/Other cs 0.6 scn 40 40 20 20 re f\n"
            )
            pdf.save(path)
        return path

    def _make_mixed_devicen_pdf(self, *, process_component: bool) -> Path:
        path = self.root / f"mixed-{process_component}.pdf"
        target = "CustomProcess" if process_component else "DemoSpot"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            colorants = pikepdf.Dictionary()
            colorants[pikepdf.Name(f"/{target}")] = self._separation(
                target, pikepdf.Name.DeviceCMYK, (0.0, 0.8, 1.0, 0.0)
            )
            attributes = pikepdf.Dictionary(
                Subtype=pikepdf.Name.NChannel,
                Colorants=colorants,
            )
            if process_component:
                attributes.Process = pikepdf.Dictionary(
                    ColorSpace=pikepdf.Name.DeviceGray,
                    Components=pikepdf.Array([pikepdf.Name.CustomProcess]),
                )
            mixed = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name(f"/{target}")]),
                    pikepdf.Name.DeviceGray,
                    self._type_two_function((1.0,)),
                    attributes,
                ]
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Standalone=self._separation(
                        target, pikepdf.Name.DeviceCMYK, (0.0, 0.8, 1.0, 0.0)
                    ),
                    Mixed=mixed,
                )
            )
            page.Contents = pdf.make_stream(b"/Standalone cs 0.5 scn 0 0 20 20 re f\n")
            pdf.save(path, min_version="1.6")
        return path

    def _make_encrypted_pdf(self, *, restricted: bool) -> Path:
        path = self.root / f"encrypted-{restricted}.pdf"
        allow = pikepdf.Permissions(modify_other=not restricted)
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Target=self._separation(
                        "DemoSpot", pikepdf.Name.DeviceCMYK, (0.0, 0.8, 1.0, 0.0)
                    )
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(
                path,
                encryption=pikepdf.Encryption(owner="owner-secret", user="", allow=allow),
            )
        return path

    def _make_malformed_separation_pdf(self) -> Path:
        path = self.root / "malformed-separation.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Target=pikepdf.Array(
                        [pikepdf.Name.Separation, pikepdf.Name.DemoSpot, pikepdf.Name.DeviceCMYK]
                    )
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(path)
        return path

    @staticmethod
    def _separation(
        name: str,
        alternate: pikepdf.Name,
        full_tone: tuple[float, ...],
    ) -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name(f"/{name}"),
                alternate,
                SetAlternateTests._type_two_function(full_tone),
            ]
        )

    @staticmethod
    def _type_two_function(full_tone: tuple[float, ...]) -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0.0] * len(full_tone)),
            C1=pikepdf.Array(full_tone),
            N=1,
        )

    def _assert_linear_function(
        self,
        function: pikepdf.Object,
        expected_full_tone: tuple[float, float, float, float],
    ) -> None:
        self.assertEqual(
            {str(key) for key in function},
            {"/FunctionType", "/Domain", "/Range", "/C0", "/C1", "/N"},
        )
        self.assertEqual(function.FunctionType, 2)
        self.assertEqual(tuple(float(value) for value in function.Domain), (0.0, 1.0))
        self.assertEqual(
            tuple(float(value) for value in function.Range),
            (0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0),
        )
        self.assertEqual(tuple(float(value) for value in function.C0), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(float(function.N), 1.0)
        for actual, expected in zip(function.C1, expected_full_tone, strict=True):
            self.assertAlmostEqual(float(actual), expected)

    @staticmethod
    def _page_content(path: Path) -> tuple[bytes, ...]:
        with pikepdf.open(path) as pdf:
            return tuple(page.Contents.read_bytes() for page in pdf.pages)

    @staticmethod
    def _resource_preview(path: Path, *, page: int, resource: str) -> bytes:
        with pikepdf.open(path) as pdf:
            value = pdf.pages[page - 1].Resources.ColorSpace[pikepdf.Name(f"/{resource}")]
            return value.unparse(resolved=True)


if __name__ == "__main__":
    unittest.main()
