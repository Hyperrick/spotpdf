from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.colors import discover_spot_declarations
from spotpdf.document import remove_spot
from spotpdf.model import InvalidPdfError, NestingLimitExceededError
from spotpdf.scan import MAX_FORM_NESTING


class SafetyRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unresolved_color_space_fails_without_replacing_forced_output(self) -> None:
        source = self._make_page_pdf(
            b"/Target cs 1 scn 0 0 10 10 re f\n/Missing cs 1 scn 20 0 10 10 re f\n"
        )
        output = self.root / "existing.pdf"
        output.write_bytes(b"keep-existing")

        with self.assertRaisesRegex(InvalidPdfError, "unresolved color space 'Missing'"):
            remove_spot(source, output, "DemoSpot", force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")

    def test_unresolved_pattern_fails_without_output(self) -> None:
        source = self._make_page_pdf(
            b"/Target cs 1 scn 0 0 10 10 re f\n/Pattern cs /Missing scn 20 0 10 10 re f\n"
        )
        output = self.root / "missing-pattern.pdf"

        with self.assertRaisesRegex(InvalidPdfError, "unresolved pattern 'Missing'"):
            remove_spot(source, output, "DemoSpot")

        self.assertFalse(output.exists())

    def test_forced_output_symlink_never_overwrites_its_target(self) -> None:
        source = self._make_page_pdf(b"/Target cs 1 scn 0 0 10 10 re f\n")

        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                target = self.root / f"target-{dangling}.txt"
                output = self.root / f"output-{dangling}.pdf"
                if not dangling:
                    target.write_bytes(b"keep-target")
                try:
                    output.symlink_to(target)
                except OSError as error:
                    self.skipTest(f"symbolic links are unavailable: {error}")

                with self.assertRaisesRegex(InvalidPdfError, "symbolic link"):
                    remove_spot(source, output, "DemoSpot", force=True)

                self.assertTrue(output.is_symlink())
                if dangling:
                    self.assertFalse(target.exists())
                else:
                    self.assertEqual(target.read_bytes(), b"keep-target")

    def test_deep_object_inventory_is_iterative(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            node = pikepdf.Dictionary(Spot=self._separation())
            for _ in range(1_200):
                node = pikepdf.Dictionary(Next=node)
            pdf.Root.Deep = node

            report = discover_spot_declarations(pdf)

        self.assertEqual(set(report.spots), {"DemoSpot"})

    def test_excessive_form_nesting_fails_without_output(self) -> None:
        source = self._make_deep_form_pdf()
        output = self.root / "deep-output.pdf"

        with self.assertRaisesRegex(NestingLimitExceededError, "Form nesting exceeds"):
            remove_spot(source, output, "DemoSpot")

        self.assertFalse(output.exists())

    def _make_page_pdf(self, content: bytes) -> Path:
        path = self.root / "source.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Target=self._separation())
            )
            page.Contents = pdf.make_stream(content)
            pdf.save(path)
        return path

    def _make_deep_form_pdf(self) -> Path:
        path = self.root / "deep-forms.pdf"
        with pikepdf.Pdf.new() as pdf:
            nested = self._form(
                pdf,
                b"/Target cs 1 scn 0 0 10 10 re f\n",
                pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Target=self._separation())),
            )
            for _ in range(MAX_FORM_NESTING + 1):
                nested = self._form(
                    pdf,
                    b"/Next Do\n",
                    pikepdf.Dictionary(XObject=pikepdf.Dictionary(Next=nested)),
                )
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Root=nested))
            page.Contents = pdf.make_stream(b"/Root Do\n")
            pdf.save(path)
        return path

    @staticmethod
    def _form(pdf: pikepdf.Pdf, content: bytes, resources: pikepdf.Dictionary):
        form = pdf.make_stream(content)
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 100, 100])
        form.Resources = resources
        return form

    @staticmethod
    def _separation() -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name.DemoSpot,
                pikepdf.Name.DeviceCMYK,
                pikepdf.Dictionary(
                    FunctionType=2,
                    Domain=pikepdf.Array([0, 1]),
                    C0=pikepdf.Array([0, 0, 0, 0]),
                    C1=pikepdf.Array([1, 0, 1, 0]),
                    N=1,
                ),
            ]
        )


if __name__ == "__main__":
    unittest.main()
