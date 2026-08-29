from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.cli import _stats_text, build_parser
from spotpdf.colors import discover_spot_declarations
from spotpdf.document import check_spot, inspect_pdf, remove_all_spots, remove_spot
from spotpdf.model import InvalidPdfError, RemovalStats, SpotKind, UnsupportedSpotUseError


class SpotPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_removes_target_text_and_resource(self) -> None:
        source = self._make_pdf(b"/Target cs 1 scn BT (REMOVE-ME) Tj ET\n0 g BT (KEEP-ME) Tj ET\n")
        source.chmod(0o640)
        output = self.root / "output.pdf"

        stats = remove_spot(source, output, "DemoSpot")

        self.assertTrue(output.exists())
        self.assertEqual(output.stat().st_mode & 0o777, 0o640)
        self.assertFalse(check_spot(output, "DemoSpot"))
        self.assertEqual(stats.text_show_operations, 1)
        with pikepdf.open(output) as pdf:
            content = pdf.pages[0].Contents.read_bytes()
        self.assertNotIn(b"REMOVE-ME", content)
        self.assertIn(b"KEEP-ME", content)

    def test_rewrites_fill_stroke_and_combined_paths(self) -> None:
        source = self._make_pdf(
            b"/Target cs 1 scn 0 G 0 0 10 10 re B\n"
            b"0 g /Target CS 1 SCN 0 0 10 10 re B*\n"
            b"/Target cs 1 scn /Target CS 1 SCN 0 0 10 10 re b\n"
        )
        output = self.root / "paths.pdf"

        stats = remove_spot(source, output, "DemoSpot")

        with pikepdf.open(output) as pdf:
            operators = [str(item.operator) for item in pikepdf.parse_content_stream(pdf.pages[0])]
        self.assertIn("S", operators)
        self.assertIn("f*", operators)
        self.assertIn("n", operators)
        self.assertEqual(stats.fills_removed, 2)
        self.assertEqual(stats.strokes_removed, 2)

    def test_exact_match_preserves_other_spot(self) -> None:
        source = self._make_pdf(
            b"/Target cs 1 scn BT (REMOVE) Tj ET\n/Other cs 1 scn BT (OTHER) Tj ET\n",
            other_spot="Individualisierung",
        )
        output = self.root / "exact.pdf"

        remove_spot(source, output, "DemoSpot")

        self.assertFalse(check_spot(output, "DemoSpot"))
        self.assertTrue(check_spot(output, "Individualisierung"))
        with pikepdf.open(output) as pdf:
            content = pdf.pages[0].Contents.read_bytes()
        self.assertNotIn(b"REMOVE", content)
        self.assertIn(b"OTHER", content)

    def test_devicen_target_fails_without_output(self) -> None:
        source = self._make_pdf(b"/Mixed cs 1 1 scn 0 0 10 10 re f\n", devicen=True)
        output = self.root / "must-not-exist.pdf"

        with self.assertRaises(UnsupportedSpotUseError):
            remove_spot(source, output, "DemoSpot")

        self.assertFalse(output.exists())

    def test_existing_output_requires_force(self) -> None:
        source = self._make_pdf(b"0 g 0 0 10 10 re f\n", include_target=False)
        output = self.root / "existing.pdf"
        output.write_bytes(b"existing")

        with self.assertRaises(InvalidPdfError):
            remove_spot(source, output, "DemoSpot")

        self.assertEqual(output.read_bytes(), b"existing")

    def test_absent_spot_is_copied_byte_for_byte(self) -> None:
        source = self._make_pdf(b"0 g 0 0 10 10 re f\n", include_target=False)
        source.chmod(0o640)
        output = self.root / "copy.pdf"

        stats = remove_spot(source, output, "DemoSpot")

        self.assertFalse(stats.changed)
        self.assertEqual(output.stat().st_mode & 0o777, 0o640)
        self.assertEqual(source.read_bytes(), output.read_bytes())

    def test_direct_object_traversal_discovers_every_spot_reliably(self) -> None:
        expected = {f"Spot-{index}" for index in range(128)}
        with pikepdf.Pdf.new() as pdf:
            declarations = pikepdf.Dictionary()
            for index, name in enumerate(sorted(expected)):
                declarations[pikepdf.Name(f"/Color{index}")] = pikepdf.Array(
                    [
                        pikepdf.Name.Separation,
                        pikepdf.Name(f"/{name}"),
                        pikepdf.Name.DeviceCMYK,
                        pikepdf.Dictionary(
                            FunctionType=2,
                            Domain=pikepdf.Array([0, 1]),
                            C0=pikepdf.Array([0, 0, 0, 0]),
                            C1=pikepdf.Array([1, 0, 0, 0]),
                            N=1,
                        ),
                    ]
                )
            pdf.Root[pikepdf.Name("/TraversalTest")] = declarations

            for _ in range(10):
                self.assertEqual(set(discover_spot_declarations(pdf).spots), expected)

    def test_rewrites_invoked_form_without_corrupting_stream(self) -> None:
        source = self._make_form_pdf(invoke_form=True, target_paint=True)
        output = self.root / "form-output.pdf"

        stats = remove_spot(source, output, "DemoSpot")

        self.assertEqual(stats.forms_changed, 1)
        self.assertEqual(stats.fills_removed, 1)
        self.assertFalse(check_spot(output, "DemoSpot"))
        with pikepdf.open(output, attempt_recovery=False) as pdf:
            form = pdf.pages[0].Resources.XObject[pikepdf.Name.Form]
            operators = [str(item.operator) for item in pikepdf.parse_content_stream(form)]
            self.assertNotIn("f", operators)
            self.assertEqual(pdf.get_warnings(), [])

    def test_removes_target_alias_from_unused_form_resources(self) -> None:
        source = self._make_form_pdf(invoke_form=False, target_paint=False)
        output = self.root / "unused-form-output.pdf"

        stats = remove_spot(source, output, "DemoSpot")

        self.assertEqual(stats.forms_changed, 0)
        self.assertEqual(stats.resources_removed, 1)
        self.assertFalse(check_spot(output, "DemoSpot"))

    def test_remove_all_rewrites_multiple_spots_and_preserves_all(self) -> None:
        source = self._make_pdf(
            b"/Target cs 1 scn /Other CS 1 SCN 0 0 10 10 re B\n",
            other_spot="Alpha",
            include_special=True,
        )
        output = self.root / "all-spots.pdf"

        result = remove_all_spots(source, output)

        self.assertEqual(result.spots, ("Alpha", "DemoSpot"))
        self.assertEqual(result.stats.fills_removed, 1)
        self.assertEqual(result.stats.strokes_removed, 1)
        self.assertFalse(check_spot(output, "Alpha"))
        self.assertFalse(check_spot(output, "DemoSpot"))
        self.assertTrue(check_spot(output, "All"))
        with pikepdf.open(output) as pdf:
            operators = [str(item.operator) for item in pikepdf.parse_content_stream(pdf.pages[0])]
        self.assertIn("n", operators)
        self.assertNotIn("B", operators)

    def test_remove_all_handles_multiple_targets_in_one_text_object(self) -> None:
        source = self._make_pdf(
            b"BT /Target cs 1 scn (FIRST) Tj /Other cs 1 scn (SECOND) Tj ET\n",
            other_spot="OtherSpot",
        )
        output = self.root / "all-text.pdf"

        result = remove_all_spots(source, output)

        self.assertEqual(result.stats.text_show_operations, 2)
        with pikepdf.open(output) as pdf:
            content = pdf.pages[0].Contents.read_bytes()
        self.assertNotIn(b"FIRST", content)
        self.assertNotIn(b"SECOND", content)

    def test_remove_all_without_named_spots_copies_bytes(self) -> None:
        source = self._make_pdf(b"0 g 0 0 10 10 re f\n", include_target=False)
        output = self.root / "all-copy.pdf"

        result = remove_all_spots(source, output)

        self.assertEqual(result.spots, ())
        self.assertEqual(source.read_bytes(), output.read_bytes())

    def test_remove_all_devicen_failure_preserves_forced_output(self) -> None:
        source = self._make_pdf(
            b"/Target cs 1 scn 0 0 10 10 re f\n",
            devicen=True,
        )
        output = self.root / "existing-all.pdf"
        output.write_bytes(b"keep-existing")

        with self.assertRaises(UnsupportedSpotUseError):
            remove_all_spots(source, output, force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")

    def test_inspection_reads_signed_pdf_but_removal_stays_blocked(self) -> None:
        source = self._make_pdf(
            b"/Target cs 1 scn 0 0 10 10 re f\n",
            include_signature=True,
        )
        output = self.root / "signed-output.pdf"

        report = inspect_pdf(source)

        self.assertEqual(report.spots["DemoSpot"].paint_operations, 1)
        self.assertIn("painted", report.spots["DemoSpot"].contexts)
        with self.assertRaisesRegex(InvalidPdfError, "signed PDFs are not modified"):
            remove_all_spots(source, output)
        self.assertFalse(output.exists())

    def test_inspection_treats_none_inside_devicen_as_reserved(self) -> None:
        source = self._make_pdf(
            b"0 g 0 0 10 10 re f\n",
            include_target=False,
            devicen_colorants=("Cyan", "None"),
        )

        report = inspect_pdf(source)

        self.assertEqual(report.spots["None"].kinds, {SpotKind.DEVICEN})
        self.assertEqual(report.spots["None"].contexts, {"reserved separation"})
        with self.assertRaisesRegex(InvalidPdfError, "reserved PDF separation names"):
            remove_spot(source, self.root / "none.pdf", "None")

    def test_reachable_root_devicen_is_rejected_during_preflight(self) -> None:
        source = self._make_pdf(
            b"/Target cs 1 scn 0 0 10 10 re f\n",
            root_devicen_colorants=("DemoSpot",),
        )
        output = self.root / "root-devicen.pdf"

        summary = inspect_pdf(source).spots["DemoSpot"]
        self.assertTrue(any("reachable DeviceN" in context for context in summary.contexts))
        with self.assertRaisesRegex(UnsupportedSpotUseError, "DeviceN"):
            remove_all_spots(source, output)

        self.assertFalse(output.exists())

    def test_unrelated_root_devicen_does_not_block_exact_removal(self) -> None:
        source = self._make_pdf(
            b"/Target cs 1 scn 0 0 10 10 re f\n",
            root_devicen_colorants=("OtherSpot",),
        )
        output = self.root / "unrelated-root-devicen.pdf"

        stats = remove_spot(source, output, "DemoSpot")

        self.assertEqual(stats.fills_removed, 1)
        self.assertFalse(check_spot(output, "DemoSpot"))
        self.assertTrue(check_spot(output, "OtherSpot"))

    def test_remove_all_preserves_process_separation_and_removes_spot(self) -> None:
        source = self._make_pdf(
            b"/Target cs 1 scn 0 0 10 10 re f\n/Other cs 1 scn 20 0 10 10 re f\n",
            other_spot="Black",
        )
        output = self.root / "preserve-process.pdf"

        result = remove_all_spots(source, output)

        self.assertEqual(result.spots, ("DemoSpot",))
        self.assertFalse(check_spot(output, "DemoSpot"))
        self.assertTrue(check_spot(output, "Black"))
        with pikepdf.open(output) as pdf:
            operators = [str(item.operator) for item in pikepdf.parse_content_stream(pdf.pages[0])]
        self.assertIn("n", operators)
        self.assertIn("f", operators)

    def test_remove_all_copies_process_separations_byte_for_byte(self) -> None:
        for colorant in ("Cyan", "Magenta", "Yellow", "Black"):
            with self.subTest(colorant=colorant):
                source = self._make_pdf(
                    b"/Other cs 1 scn 0 0 10 10 re f\n",
                    include_target=False,
                    other_spot=colorant,
                )
                output = self.root / f"preserve-{colorant}.pdf"

                result = remove_all_spots(source, output)

                self.assertEqual(result.spots, ())
                self.assertEqual(source.read_bytes(), output.read_bytes())
                summary = inspect_pdf(output).spots[colorant]
                self.assertIn("process colorant; preserved by --all", summary.contexts)

    def test_explicit_process_separation_removal_is_still_allowed(self) -> None:
        source = self._make_pdf(
            b"/Other cs 1 scn 0 0 10 10 re f\n",
            include_target=False,
            other_spot="Black",
        )
        output = self.root / "explicit-black.pdf"

        stats = remove_spot(source, output, "Black")

        self.assertEqual(stats.fills_removed, 1)
        self.assertFalse(check_spot(output, "Black"))

    def test_remove_all_removes_lowercase_black_spot(self) -> None:
        source = self._make_pdf(
            b"/Other cs 1 scn 0 0 10 10 re f\n",
            include_target=False,
            other_spot="black",
        )
        output = self.root / "lowercase-black.pdf"

        result = remove_all_spots(source, output)

        self.assertEqual(result.spots, ("black",))
        self.assertFalse(check_spot(output, "black"))

    def test_remove_parser_requires_exactly_one_selection_mode(self) -> None:
        parser = build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["remove", "input.pdf", "-o", "output.pdf"])
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "remove",
                        "input.pdf",
                        "--spot",
                        "Target",
                        "--all",
                        "-o",
                        "output.pdf",
                    ]
                )
        args = parser.parse_args(["remove", "input.pdf", "--all", "-o", "output.pdf"])
        self.assertTrue(args.all_spots)

    def test_stats_output_uses_singular_and_plural_nouns(self) -> None:
        singular = _stats_text(
            RemovalStats(
                text_blocks=1,
                text_show_operations=1,
                fills_removed=1,
                strokes_removed=1,
            )
        )
        plural = _stats_text(RemovalStats(fills_removed=2, strokes_removed=2))

        self.assertIn("1 text block, 1 text show, 1 fill, 1 stroke", singular)
        self.assertIn("2 fills, 2 strokes", plural)

    def _make_form_pdf(self, *, invoke_form: bool, target_paint: bool) -> Path:
        path = self.root / f"form-{len(list(self.root.glob('form-*.pdf')))}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            function = pikepdf.Dictionary(
                FunctionType=2,
                Domain=pikepdf.Array([0, 1]),
                C0=pikepdf.Array([0, 0, 0, 0]),
                C1=pikepdf.Array([1, 0, 1, 0]),
                N=1,
            )
            target = pikepdf.Array(
                [
                    pikepdf.Name.Separation,
                    pikepdf.Name("/DemoSpot"),
                    pikepdf.Name.DeviceCMYK,
                    function,
                ]
            )
            form_content = (
                b"/Target cs 1 scn 0 0 10 10 re f\n" if target_paint else b"0 g 0 0 10 10 re f\n"
            )
            form = pdf.make_stream(form_content)
            form.Type = pikepdf.Name.XObject
            form.Subtype = pikepdf.Name.Form
            form.BBox = pikepdf.Array([0, 0, 100, 100])
            form.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Target=target))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Form=form))
            page.Contents = pdf.make_stream(b"/Form Do\n" if invoke_form else b"")
            pdf.save(path)
        return path

    def _make_pdf(
        self,
        content: bytes,
        *,
        include_target: bool = True,
        other_spot: str | None = None,
        devicen: bool = False,
        devicen_colorants: tuple[str, ...] | None = None,
        include_special: bool = False,
        include_signature: bool = False,
        root_devicen_colorants: tuple[str, ...] | None = None,
    ) -> Path:
        path = self.root / f"input-{len(list(self.root.glob('input-*.pdf')))}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            function = pikepdf.Dictionary(
                FunctionType=2,
                Domain=pikepdf.Array([0, 1]),
                C0=pikepdf.Array([0, 0, 0, 0]),
                C1=pikepdf.Array([1, 0, 1, 0]),
                N=1,
            )
            target = pikepdf.Array(
                [
                    pikepdf.Name.Separation,
                    pikepdf.Name("/DemoSpot"),
                    pikepdf.Name.DeviceCMYK,
                    function,
                ]
            )
            color_spaces = pikepdf.Dictionary()
            if include_target:
                color_spaces[pikepdf.Name.Target] = target
            if other_spot:
                color_spaces[pikepdf.Name.Other] = pikepdf.Array(
                    [
                        pikepdf.Name.Separation,
                        pikepdf.Name(f"/{other_spot}"),
                        pikepdf.Name.DeviceCMYK,
                        function,
                    ]
                )
            if devicen or devicen_colorants is not None:
                colorants = devicen_colorants or ("Cyan", "DemoSpot")
                color_spaces[pikepdf.Name.Mixed] = pikepdf.Array(
                    [
                        pikepdf.Name.DeviceN,
                        pikepdf.Array([pikepdf.Name(f"/{colorant}") for colorant in colorants]),
                        pikepdf.Name.DeviceCMYK,
                        function,
                    ]
                )
            if include_special:
                color_spaces[pikepdf.Name.Registration] = pikepdf.Array(
                    [
                        pikepdf.Name.Separation,
                        pikepdf.Name.All,
                        pikepdf.Name.DeviceCMYK,
                        function,
                    ]
                )
            page.Resources = pikepdf.Dictionary(ColorSpace=color_spaces)
            page.Contents = pdf.make_stream(content)
            if include_signature:
                signature_field = pdf.make_indirect(
                    pikepdf.Dictionary(
                        FT=pikepdf.Name.Sig,
                        T=pikepdf.String("Signature1"),
                    )
                )
                pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([signature_field]))
            if root_devicen_colorants is not None:
                pdf.Root.PieceInfo = pikepdf.Dictionary(
                    Inks=pikepdf.Array(
                        [
                            pikepdf.Name.DeviceN,
                            pikepdf.Array(
                                [
                                    pikepdf.Name(f"/{colorant}")
                                    for colorant in root_devicen_colorants
                                ]
                            ),
                            pikepdf.Name.DeviceCMYK,
                            function,
                        ]
                    )
                )
            pdf.save(path)
        return path


if __name__ == "__main__":
    unittest.main()
