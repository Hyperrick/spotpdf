from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.convert import convert_spot_to_cmyk
from spotpdf.model import UnsupportedSpotUseError
from tests.conversion_fixtures import separation


class ConvertFormTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_form_with_own_resources_is_converted_once(self) -> None:
        source = self.root / "own-resources.pdf"
        output = self.root / "own-resources-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        form = self._form(pdf, b"/Ink cs 0.5 scn 0 0 10 10 re f")
        form.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Paint=form))
        page.Contents = pdf.make_stream(b"/Paint Do /Paint Do")
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        self.assertEqual(result.page_content_sequences_changed, 0)
        self.assertEqual(result.forms_changed, 1)
        self.assertEqual(result.color_operators_rewritten, 2)
        self.assertEqual(result.pages_affected, (1,))
        with pikepdf.open(output) as converted:
            saved = converted.pages[0].Resources.XObject.Paint
            self.assertEqual(list(saved.Resources.ColorSpace.keys()), [])
            self.assertIn(b"0 0.40 0.50 0 k", saved.read_bytes())

    def test_inherited_target_state_and_nested_form_preserve_caller_tint(self) -> None:
        source = self.root / "inherited.pdf"
        output = self.root / "inherited-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        inner = self._form(pdf, b"0 0 10 10 re f")
        outer = self._form(pdf, b"/Inner Do")
        outer.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Inner=inner))
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(Outer=outer),
        )
        page.Contents = pdf.make_stream(b"/Ink cs 0.25 scn /Outer Do 0.75 scn /Outer Do")
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        self.assertEqual(result.page_content_sequences_changed, 1)
        self.assertEqual(result.forms_changed, 0)
        self.assertEqual(result.color_operators_rewritten, 3)
        with pikepdf.open(output) as converted:
            content = converted.pages[0].Contents.read_bytes()
            self.assertIn(b"0 0.200 0.250 0 k", content)
            self.assertIn(b"0 0.600 0.750 0 k", content)
            self.assertEqual(
                converted.pages[0].Resources.XObject.Outer.Resources.XObject.Inner.read_bytes(),
                b"0 0 10 10 re f",
            )

    def test_reachable_uninvoked_form_with_own_resources_is_kept_valid(self) -> None:
        source = self.root / "uninvoked.pdf"
        output = self.root / "uninvoked-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        form = self._form(pdf, b"/Ink cs 0.5 scn 0 0 10 10 re f")
        form.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Unused=form))
        page.Contents = pdf.make_stream(b"")
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        self.assertEqual(result.forms_changed, 1)
        self.assertEqual(result.pages_affected, ())
        with pikepdf.open(output) as converted:
            saved = converted.pages[0].Resources.XObject.Unused
            self.assertEqual(list(saved.Resources.ColorSpace.keys()), [])
            self.assertIn(b"0 0.40 0.50 0 k", saved.read_bytes())

    def test_uninvoked_form_with_inherited_inline_target_alias_fails_atomically(self) -> None:
        source = self.root / "uninvoked-inline.pdf"
        output = self.root / "uninvoked-inline-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        form = self._form(pdf, b"BI /W 1 /H 1 /BPC 8 /CS /Ink ID \x00 EI")
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(Unused=form),
        )
        page.Contents = pdf.make_stream(b"")
        pdf.save(source)
        original = b"existing inline output"
        output.write_bytes(original)

        with self.assertRaisesRegex(UnsupportedSpotUseError, "target-colored inline images"):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_shared_form_that_needs_different_bytes_fails_atomically(self) -> None:
        source = self.root / "context-conflict.pdf"
        output = self.root / "context-conflict-output.pdf"
        pdf = pikepdf.Pdf.new()
        shared = self._form(pdf, b"/Ink cs 0.5 scn 0 0 10 10 re f")
        first = pdf.add_blank_page()
        first.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(Shared=shared),
        )
        first.Contents = pdf.make_stream(b"/Shared Do")
        second = pdf.add_blank_page()
        second.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=pikepdf.Name.DeviceRGB),
            XObject=pikepdf.Dictionary(Shared=shared),
        )
        second.Contents = pdf.make_stream(b"/Shared Do")
        pdf.save(source)
        original = b"existing output"
        output.write_bytes(original)

        with self.assertRaisesRegex(UnsupportedSpotUseError, "context-dependent"):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(output.read_bytes(), original)

    def test_uninvoked_inherited_form_uses_its_actual_owner_scope(self) -> None:
        source = self.root / "uninvoked-other-scope.pdf"
        output = self.root / "uninvoked-other-scope-output.pdf"
        pdf = pikepdf.Pdf.new()
        form = self._form(pdf, b"/Ink cs 0.5 scn 0 0 10 10 re f")
        target_page = pdf.add_blank_page()
        target_page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        target_page.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 10 10 re f")
        other_page = pdf.add_blank_page()
        other_page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation("OtherSpot")),
            XObject=pikepdf.Dictionary(Unused=form),
        )
        other_page.Contents = pdf.make_stream(b"")
        form_before = form.read_bytes()
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        self.assertEqual(result.forms_changed, 0)
        with pikepdf.open(output) as converted:
            self.assertEqual(
                converted.pages[1].Resources.XObject.Unused.read_bytes(),
                form_before,
            )
            self.assertIn(pikepdf.Name.Ink, converted.pages[1].Resources.ColorSpace)

    def test_uninvoked_inherited_owner_conflict_fails_atomically(self) -> None:
        source = self.root / "inherited-owner-conflict.pdf"
        output = self.root / "inherited-owner-conflict-output.pdf"
        pdf = pikepdf.Pdf.new()
        shared = self._form(pdf, b"/Ink cs 0.5 scn 0 0 10 10 re f")
        target_page = pdf.add_blank_page()
        target_page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(Shared=shared),
        )
        target_page.Contents = pdf.make_stream(b"/Shared Do")
        other_page = pdf.add_blank_page()
        other_page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation("OtherSpot")),
            XObject=pikepdf.Dictionary(Shared=shared),
        )
        other_page.Contents = pdf.make_stream(b"")
        pdf.save(source)
        original = b"keep inherited owner conflict output"
        output.write_bytes(original)

        with self.assertRaisesRegex(UnsupportedSpotUseError, "context-dependent"):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_shared_form_aliases_with_own_resources_are_supported(self) -> None:
        source = self.root / "own-resource-aliases.pdf"
        output = self.root / "own-resource-aliases-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        shared = self._form(pdf, b"/Ink cs 0.5 scn 0 0 10 10 re f")
        shared.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(A=shared, B=shared))
        page.Contents = pdf.make_stream(b"/A Do")
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        self.assertEqual(result.forms_changed, 1)
        self.assertEqual(result.resources_removed, 1)
        with pikepdf.open(output) as converted:
            first = converted.pages[0].Resources.XObject.A
            second = converted.pages[0].Resources.XObject.B
            self.assertEqual(tuple(first.objgen), tuple(second.objgen))
            self.assertIn(b"0 0.40 0.50 0 k", first.read_bytes())

    def test_shared_inherited_aliases_in_one_resource_scope_are_supported(self) -> None:
        source = self.root / "inherited-aliases.pdf"
        output = self.root / "inherited-aliases-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        shared = self._form(pdf, b"/Ink cs 0.5 scn 0 0 10 10 re f")
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(A=shared, B=shared),
        )
        page.Contents = pdf.make_stream(b"/A Do")
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        self.assertEqual(result.forms_changed, 1)
        with pikepdf.open(output) as converted:
            self.assertIn(
                b"0 0.40 0.50 0 k",
                converted.pages[0].Resources.XObject.B.read_bytes(),
            )

    @staticmethod
    def _form(pdf: pikepdf.Pdf, content: bytes) -> pikepdf.Stream:
        form = pdf.make_stream(content)
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 10, 10])
        return form


if __name__ == "__main__":
    unittest.main()
