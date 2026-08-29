from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pikepdf

import spotpdf.rename as rename_module
from spotpdf.document import inspect_pdf
from spotpdf.model import SpotPdfError
from spotpdf.rename import rename_spot


class RenameSemanticEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_shared_indirect_devicen_component_is_mutated_once(self) -> None:
        source = self.root / "shared-devicen.pdf"
        output = self.root / "shared-devicen-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            separation = pdf.make_indirect(self._separation("Old"))
            shared = pdf.make_indirect(
                pikepdf.Array(
                    [
                        pikepdf.Name.DeviceN,
                        pikepdf.Array([pikepdf.Name.Old]),
                        pikepdf.Name.DeviceCMYK,
                        self._cmyk_function(),
                    ]
                )
            )
            for index in range(2):
                page = pdf.add_blank_page(page_size=(100, 100))
                spaces = pikepdf.Dictionary(Mixed=shared)
                if index == 0:
                    spaces[pikepdf.Name.Ink] = separation
                page.Resources = pikepdf.Dictionary(ColorSpace=spaces)
                page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        result = rename_spot(source, output, "Old", "New")

        self.assertEqual(result.definitions_renamed, 2)
        report = inspect_pdf(output)
        self.assertNotIn("Old", report.colorants)
        with pikepdf.open(output) as pdf:
            first = pdf.pages[0].Resources.ColorSpace.Mixed
            second = pdf.pages[1].Resources.ColorSpace.Mixed
            self.assertEqual(str(first[1][0]), "/New")
            self.assertEqual(first.objgen, second.objgen)

    def test_shared_component_array_counts_each_semantic_definition(self) -> None:
        source = self.root / "shared-components.pdf"
        output = self.root / "shared-components-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            components = pdf.make_indirect(pikepdf.Array([pikepdf.Name.Old]))
            spaces = pikepdf.Dictionary(Ink=self._separation("Old"))
            for name in ("MixedA", "MixedB"):
                spaces[pikepdf.Name(f"/{name}")] = pikepdf.Array(
                    [
                        pikepdf.Name.DeviceN,
                        components,
                        pikepdf.Name.DeviceCMYK,
                        self._cmyk_function(),
                    ]
                )
            page.Resources = pikepdf.Dictionary(ColorSpace=spaces)
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        result = rename_spot(source, output, "Old", "New")

        self.assertEqual(result.definitions_renamed, 3)
        with pikepdf.open(output) as pdf:
            spaces = pdf.pages[0].Resources.ColorSpace
            self.assertEqual(str(spaces.MixedA[1][0]), "/New")
            self.assertEqual(str(spaces.MixedB[1][0]), "/New")

    def test_target_in_malformed_devicen_is_rejected_atomically(self) -> None:
        source = self._basic_pdf("malformed-devicen.pdf")
        with pikepdf.open(source, allow_overwriting_input=True) as pdf:
            pdf.pages[0].Resources.ColorSpace.Bad = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Name.Old,
                    pikepdf.Name.DeviceCMYK,
                    self._cmyk_function(),
                ]
            )
            pdf.save(source)

        self._assert_atomic_failure(source)

    def test_separation_info_name_and_color_space_must_agree(self) -> None:
        cases = (
            ("Old", "Other"),
            ("Other", "Old"),
        )
        for index, (device_name, color_space_name) in enumerate(cases):
            with self.subTest(device=device_name, color_space=color_space_name):
                source = self._basic_pdf(f"separation-info-{index}.pdf")
                with pikepdf.open(source, allow_overwriting_input=True) as pdf:
                    info = pikepdf.Dictionary(
                        Pages=pikepdf.Array([pdf.pages[0].obj]),
                        DeviceColorant=pikepdf.Name(f"/{device_name}"),
                    )
                    if color_space_name is not None:
                        info[pikepdf.Name.ColorSpace] = self._separation(color_space_name)
                    pdf.pages[0].SeparationInfo = info
                    pdf.save(source)

                self._assert_atomic_failure(source)

    def test_separation_info_supports_optional_and_devicen_color_spaces(self) -> None:
        for use_devicen in (False, True):
            with self.subTest(use_devicen=use_devicen):
                source = self._basic_pdf(f"separation-info-valid-{use_devicen}.pdf")
                output = self.root / f"separation-info-valid-{use_devicen}-output.pdf"
                with pikepdf.open(source, allow_overwriting_input=True) as pdf:
                    info = pikepdf.Dictionary(
                        Pages=pikepdf.Array([pdf.pages[0].obj]),
                        DeviceColorant=pikepdf.Name.Old,
                    )
                    if use_devicen:
                        info.ColorSpace = pikepdf.Array(
                            [
                                pikepdf.Name.DeviceN,
                                pikepdf.Array([pikepdf.Name.Old]),
                                pikepdf.Name.DeviceCMYK,
                                self._cmyk_function(),
                            ]
                        )
                    pdf.pages[0].SeparationInfo = info
                    pdf.save(source)

                rename_spot(source, output, "Old", "New")

                report = inspect_pdf(output)
                self.assertNotIn("Old", report.colorants)
                with pikepdf.open(output) as pdf:
                    self.assertEqual(str(pdf.pages[0].SeparationInfo.DeviceColorant), "/New")
                    if use_devicen:
                        self.assertEqual(
                            str(pdf.pages[0].SeparationInfo.ColorSpace[1][0]),
                            "/New",
                        )

    def test_additional_devicen_colorant_definition_is_renamed(self) -> None:
        source = self.root / "additional-colorant.pdf"
        output = self.root / "additional-colorant-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            mixed = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.Other]),
                    pikepdf.Name.DeviceCMYK,
                    self._cmyk_function(),
                    pikepdf.Dictionary(
                        Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                        MixingHints=pikepdf.Dictionary(
                            Solidities=pikepdf.Dictionary(Old=0.8),
                            PrintingOrder=pikepdf.Array([pikepdf.Name.Other, pikepdf.Name.Old]),
                        ),
                    ),
                ]
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Ink=self._separation("Old"),
                    Mixed=mixed,
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        rename_spot(source, output, "Old", "New")

        report = inspect_pdf(output)
        self.assertNotIn("Old", report.colorants)
        self.assertFalse(any(item.name == "Old" for item in report.dependencies))
        with pikepdf.open(output) as pdf:
            mixed = pdf.pages[0].Resources.ColorSpace.Mixed
            self.assertEqual(str(mixed[1][0]), "/Other")
            self.assertIn(pikepdf.Name.New, mixed[4].Colorants)
            self.assertIn(pikepdf.Name.New, mixed[4].MixingHints.Solidities)

    def test_unrelated_prepress_structures_do_not_block_a_rename(self) -> None:
        source = self._basic_pdf("unrelated-prepress.pdf")
        output = self.root / "unrelated-prepress-output.pdf"
        with pikepdf.open(source, allow_overwriting_input=True) as pdf:
            page = pdf.pages[0]
            image = self._image(pdf)
            opi_version = pikepdf.Dictionary(
                Type=pikepdf.Name.OPI,
                Version=2.0,
                Inks=pikepdf.Array([pikepdf.Name.monochrome, pikepdf.String("Other"), 1.0]),
            )
            opi = pikepdf.Dictionary()
            opi[pikepdf.Name("/2.0")] = opi_version
            image.OPI = opi
            page.Resources.XObject = pikepdf.Dictionary(Image=image)

            component = pikepdf.Dictionary(HalftoneType=1)
            halftone = pikepdf.Dictionary(HalftoneType=5, Default=component, Other=component)
            page.Resources.ExtGState = pikepdf.Dictionary(
                GS=pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, HT=halftone)
            )

            normal = self._form(pdf)
            normal.Colorants = pikepdf.Dictionary(Old=self._separation("Old"))
            rollover = self._form(pdf)
            rollover.Colorants = pikepdf.Dictionary(Other=self._separation("Other"))
            printer_mark = self._annotation(
                pikepdf.Name.PrinterMark,
                pikepdf.Dictionary(N=normal, R=rollover),
            )

            trap_form = self._form(pdf)
            trap_form.SeparationColorNames = pikepdf.Array([pikepdf.Name.Other])
            trap_net = self._annotation(
                pikepdf.Name.TrapNet,
                pikepdf.Dictionary(N=trap_form),
            )
            page.Annots = pikepdf.Array([printer_mark, trap_net])
            pdf.save(source)

        rename_spot(source, output, "Old", "New")

        report = inspect_pdf(output)
        self.assertNotIn("Old", report.colorants)
        self.assertIn("New", report.spots)
        with pikepdf.open(output) as pdf:
            appearances = pdf.pages[0].Annots[0].AP
            self.assertIn(pikepdf.Name.New, appearances.N.Colorants)
            self.assertIn(pikepdf.Name.Other, appearances.R.Colorants)

    def test_unicode_normalization_is_not_an_implicit_collision(self) -> None:
        source = self.root / "unicode-normalization.pdf"
        output = self.root / "unicode-normalization-output.pdf"
        nfc = "Caf\u00e9"
        nfd = "Cafe\u0301"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            spaces = pikepdf.Dictionary(Ink=self._separation("Old"))
            spaces[pikepdf.Name.NFD] = self._separation(nfd)
            page.Resources = pikepdf.Dictionary(ColorSpace=spaces)
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        rename_spot(source, output, "Old", nfc)

        report = inspect_pdf(output)
        self.assertIn(nfc, report.spots)
        self.assertIn(nfd, report.spots)
        self.assertNotEqual(nfc, nfd)

    def test_uncompressed_stream_tint_transform_survives_save_compression(self) -> None:
        source = self.root / "stream-tint.pdf"
        output = self.root / "stream-tint-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            tint = pdf.make_stream(b"{ pop 0 0 0 0 }")
            tint.FunctionType = 4
            tint.Domain = pikepdf.Array([0, 1])
            tint.Range = pikepdf.Array([0, 1, 0, 1, 0, 1, 0, 1])
            separation = pikepdf.Array(
                [
                    pikepdf.Name.Separation,
                    pikepdf.Name.Old,
                    pikepdf.Name.DeviceCMYK,
                    tint,
                ]
            )
            page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation))
            page.Contents = pdf.make_stream(b"")
            pdf.save(source, compress_streams=False)

        rename_spot(source, output, "Old", "New")

        self.assertIn("New", inspect_pdf(output).spots)
        with pikepdf.open(source) as before, pikepdf.open(output) as after:
            before_tint = before.pages[0].Resources.ColorSpace.Ink[3]
            after_tint = after.pages[0].Resources.ColorSpace.Ink[3]
            self.assertEqual(before_tint.read_bytes(), after_tint.read_bytes())
            self.assertEqual(before_tint.FunctionType, after_tint.FunctionType)

    def test_indirect_preview_scalars_with_nonzero_generations_survive_save(self) -> None:
        source = self.root / "indirect-preview-scalars.pdf"
        output = self.root / "indirect-preview-scalars-output.pdf"
        source.write_bytes(self._pdf_with_nonzero_generation_preview_objects())

        rename_spot(source, output, "Old", "New")

        with pikepdf.open(output) as pdf:
            separation = pdf.pages[0].Resources.ColorSpace.Ink
            self.assertEqual(str(separation[1]), "/New")
            self.assertEqual(str(separation[2]), "/DeviceCMYK")
            self.assertEqual(separation[3].FunctionType, 2)

    def test_saved_devicen_preview_mutation_is_rejected_atomically(self) -> None:
        source = self.root / "devicen-preview.pdf"
        output = self.root / "devicen-preview-output.pdf"
        output.write_bytes(b"keep-existing")
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            mixed = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.Old]),
                    pikepdf.Name.DeviceCMYK,
                    self._cmyk_function(),
                ]
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Ink=self._separation("Old"),
                    Mixed=mixed,
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        original_save = rename_module.save_pdf

        def mutate_preview(pdf: pikepdf.Pdf, path: Path) -> None:
            pdf.pages[0].Resources.ColorSpace.Mixed[3].C1 = pikepdf.Array([1, 1, 1, 1])
            original_save(pdf, path)

        with (
            mock.patch("spotpdf.rename.save_pdf", side_effect=mutate_preview),
            self.assertRaisesRegex(SpotPdfError, "alternate spaces or tint transforms"),
        ):
            rename_spot(source, output, "Old", "New", force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")

    def test_default_mixing_hint_destination_is_rejected(self) -> None:
        source = self.root / "mixing-default.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            mixed = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.Old]),
                    pikepdf.Name.DeviceCMYK,
                    self._cmyk_function(),
                    pikepdf.Dictionary(
                        Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                        MixingHints=pikepdf.Dictionary(
                            Solidities=pikepdf.Dictionary(Old=0.8, Default=0.0),
                            PrintingOrder=pikepdf.Array([pikepdf.Name.Old]),
                        ),
                    ),
                ]
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Ink=self._separation("Old"),
                    Mixed=mixed,
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        self._assert_atomic_failure(source, destination="Default")

    def test_post_save_verification_failure_preserves_forced_destination(self) -> None:
        source = self._basic_pdf("verify-failure.pdf")
        output = self.root / "verify-failure-output.pdf"
        output.write_bytes(b"keep-existing")

        with (
            mock.patch(
                "spotpdf.rename._verify_saved_pdf",
                side_effect=SpotPdfError("injected verification failure"),
            ),
            self.assertRaisesRegex(SpotPdfError, "injected verification failure"),
        ):
            rename_spot(source, output, "Old", "New", force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_output_created_during_processing_is_not_overwritten(self) -> None:
        source = self._basic_pdf("output-race.pdf")
        output = self.root / "output-race-result.pdf"

        def create_competing_output(*_args: object, **_kwargs: object) -> None:
            output.write_bytes(b"competing-output")

        with (
            mock.patch(
                "spotpdf.rename._verify_saved_pdf",
                side_effect=create_competing_output,
            ),
            self.assertRaisesRegex(SpotPdfError, "appeared during processing"),
        ):
            rename_spot(source, output, "Old", "New")

        self.assertEqual(output.read_bytes(), b"competing-output")
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are unavailable on Windows")
    def test_success_preserves_input_file_mode(self) -> None:
        source = self._basic_pdf("mode.pdf")
        output = self.root / "mode-output.pdf"
        source.chmod(0o640)

        rename_spot(source, output, "Old", "New")

        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o640)

    @unittest.skipUnless(os.name == "nt", "Windows read-only behavior")
    def test_windows_no_force_publish_handles_readonly_input(self) -> None:
        source = self._basic_pdf("windows-readonly.pdf")
        output = self.root / "windows-readonly-output.pdf"
        source.chmod(stat.S_IREAD)
        try:
            rename_spot(source, output, "Old", "New")
            self.assertIn("New", inspect_pdf(output).spots)
        finally:
            source.chmod(stat.S_IREAD | stat.S_IWRITE)
            if output.exists():
                output.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_hard_link_output_cannot_alias_the_input(self) -> None:
        source = self._basic_pdf("hard-link.pdf")
        output = self.root / "hard-link-output.pdf"
        original = source.read_bytes()
        try:
            os.link(source, output)
        except OSError as error:
            self.skipTest(f"hard links are unavailable: {error}")

        with self.assertRaises(SpotPdfError):
            rename_spot(source, output, "Old", "New", force=True)

        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(output.read_bytes(), original)

    def _basic_pdf(self, filename: str) -> Path:
        path = self.root / filename
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Ink=self._separation("Old"))
            )
            page.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 10 10 re f\n")
            pdf.save(path)
        return path

    def _assert_atomic_failure(self, source: Path, *, destination: str = "New") -> None:
        output = self.root / f"failure-{len(list(self.root.glob('failure-*.pdf')))}.pdf"
        output.write_bytes(b"keep-existing")
        with self.assertRaises(SpotPdfError):
            rename_spot(source, output, "Old", destination, force=True)
        self.assertEqual(output.read_bytes(), b"keep-existing")

    @staticmethod
    def _annotation(subtype: pikepdf.Name, appearances: pikepdf.Dictionary) -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=subtype,
            F=68,
            Rect=pikepdf.Array([0, 0, 10, 10]),
            AP=appearances,
        )

    @staticmethod
    def _form(pdf: pikepdf.Pdf) -> pikepdf.Stream:
        form = pdf.make_stream(b"")
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 10, 10])
        form.Resources = pikepdf.Dictionary()
        return form

    @staticmethod
    def _image(pdf: pikepdf.Pdf) -> pikepdf.Stream:
        image = pdf.make_stream(b"\x00\x00\x00")
        image.Type = pikepdf.Name.XObject
        image.Subtype = pikepdf.Name.Image
        image.Width = 1
        image.Height = 1
        image.ColorSpace = pikepdf.Name.DeviceRGB
        image.BitsPerComponent = 8
        return image

    @staticmethod
    def _separation(name: str) -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name(f"/{name}"),
                pikepdf.Name.DeviceCMYK,
                RenameSemanticEdgeTests._cmyk_function(),
            ]
        )

    @staticmethod
    def _cmyk_function() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=pikepdf.Array([0, 0.8, 1, 0]),
            N=1,
        )

    @staticmethod
    def _pdf_with_nonzero_generation_preview_objects() -> bytes:
        objects = (
            (1, 0, b"<< /Type /Catalog /Pages 2 0 R >>"),
            (2, 0, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            (
                3,
                0,
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
                b"/Resources << /ColorSpace << /Ink 7 0 R >> >> /Contents 4 0 R >>",
            ),
            (4, 0, b"<< /Length 1 >>\nstream\n\nendstream"),
            (5, 2, b"/DeviceCMYK"),
            (
                6,
                3,
                b"<< /FunctionType 2 /Domain [0 1] /C0 [0 0 0 0] /C1 [0 .8 1 0] /N 1 >>",
            ),
            (7, 0, b"[ /Separation /Old 5 2 R 6 3 R ]"),
        )
        document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        xref_entries = [(0, 65535, "f")]
        for number, generation, value in objects:
            xref_entries.append((len(document), generation, "n"))
            document.extend(f"{number} {generation} obj\n".encode())
            document.extend(value)
            document.extend(b"\nendobj\n")
        xref_offset = len(document)
        document.extend(f"xref\n0 {len(xref_entries)}\n".encode())
        for offset, generation, state in xref_entries:
            document.extend(f"{offset:010d} {generation:05d} {state} \n".encode())
        document.extend(
            f"trailer\n<< /Size {len(xref_entries)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n".encode()
        )
        return bytes(document)


if __name__ == "__main__":
    unittest.main()
