from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.convert import convert_spot_to_cmyk
from spotpdf.model import SpotPdfError, UnsupportedSpotUseError
from tests.conversion_fixtures import separation


class ConvertAliasDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_image_icc_alternate_alias_fails_atomically(self) -> None:
        source = self.root / "image-icc-alias.pdf"
        output = self.root / "image-icc-alias-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = self._target_page(pdf)
        profile = pdf.make_stream(b"synthetic profile")
        profile.N = 4
        profile.Alternate = pikepdf.Name.Ink
        image = pdf.make_stream(b"\0\0\0\0")
        image.Type = pikepdf.Name.XObject
        image.Subtype = pikepdf.Name.Image
        image.Width = 1
        image.Height = 1
        image.BitsPerComponent = 8
        image.ColorSpace = pikepdf.Array([pikepdf.Name.ICCBased, profile])
        page.Resources.XObject = pikepdf.Dictionary(Image=image)
        pdf.save(source)

        self._assert_atomic_rejection(source, output, "color-space field")

    def test_tiling_pattern_alias_with_no_resources_fails_atomically(self) -> None:
        source = self.root / "tiling-no-resources.pdf"
        output = self.root / "tiling-no-resources-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = self._target_page(pdf)
        page.Resources.Pattern = pikepdf.Dictionary(
            Tile=self._tiling_pattern(pdf, b"/Ink cs 1 scn 0 0 10 10 re f")
        )
        pdf.save(source)

        self._assert_atomic_rejection(source, output, "tiling Pattern content")

    def test_tiling_pattern_with_own_target_resources_fails_atomically(self) -> None:
        source = self.root / "tiling-own-resources.pdf"
        output = self.root / "tiling-own-resources-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = self._target_page(pdf)
        pattern = self._tiling_pattern(pdf, b"/Ink cs 1 scn 0 0 10 10 re f")
        pattern.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Resources.Pattern = pikepdf.Dictionary(Tile=pattern)
        pdf.save(source)

        self._assert_atomic_rejection(source, output)

    def test_scope_local_unrelated_aliases_are_not_false_dependencies(self) -> None:
        source = self.root / "scope-local.pdf"
        output = self.root / "scope-local-output.pdf"
        pdf = pikepdf.Pdf.new()
        self._target_page(pdf)

        unrelated = pdf.add_blank_page()
        form = self._form(pdf, b"")
        form.Resources = self._unrelated_resources()
        form.Group = self._group()
        pattern = self._tiling_pattern(pdf, b"/Ink cs 0.1 0.2 0.3 scn 0 0 10 10 re f")
        pattern.Resources = self._unrelated_resources()
        unrelated.Resources = self._unrelated_resources()
        unrelated.Resources.XObject = pikepdf.Dictionary(Paint=form)
        unrelated.Resources.Shading = pikepdf.Dictionary(
            Shade=pikepdf.Dictionary(ShadingType=2, ColorSpace=pikepdf.Name.Ink)
        )
        unrelated.Resources.Pattern = pikepdf.Dictionary(Tile=pattern)
        unrelated.Group = self._group()
        unrelated.Contents = pdf.make_stream(b"/Paint Do")
        pdf.save(source)

        convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        with pikepdf.open(output) as saved:
            self.assertNotIn(pikepdf.Name.Ink, saved.pages[0].Resources.ColorSpace)
            self.assertIn(pikepdf.Name.Ink, saved.pages[1].Resources.ColorSpace)
            self.assertEqual(saved.pages[1].Group.CS, pikepdf.Name.Ink)
            self.assertEqual(
                saved.pages[1].Resources.XObject.Paint.Group.CS,
                pikepdf.Name.Ink,
            )

    def _assert_atomic_rejection(
        self,
        source: Path,
        output: Path,
        message: str | None = None,
    ) -> None:
        original = b"keep existing alias output"
        output.write_bytes(original)
        context = (
            self.assertRaisesRegex(UnsupportedSpotUseError, message)
            if message is not None
            else self.assertRaises(SpotPdfError)
        )
        with context:
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    @staticmethod
    def _target_page(pdf: pikepdf.Pdf) -> pikepdf.Page:
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Contents = pdf.make_stream(b"")
        return page

    @staticmethod
    def _unrelated_resources() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=pikepdf.Name.DeviceRGB))

    @staticmethod
    def _group() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            Type=pikepdf.Name.Group,
            S=pikepdf.Name.Transparency,
            CS=pikepdf.Name.Ink,
        )

    @staticmethod
    def _tiling_pattern(pdf: pikepdf.Pdf, content: bytes) -> pikepdf.Stream:
        pattern = pdf.make_stream(content)
        pattern.Type = pikepdf.Name.Pattern
        pattern.PatternType = 1
        pattern.PaintType = 1
        pattern.TilingType = 1
        pattern.BBox = pikepdf.Array([0, 0, 10, 10])
        pattern.XStep = 10
        pattern.YStep = 10
        return pattern

    @staticmethod
    def _form(pdf: pikepdf.Pdf, content: bytes) -> pikepdf.Stream:
        form = pdf.make_stream(content)
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 10, 10])
        return form


if __name__ == "__main__":
    unittest.main()
