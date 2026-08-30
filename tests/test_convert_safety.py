from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

import pikepdf

from spotpdf.convert import convert_spot_to_cmyk
from spotpdf.model import SpotPdfError, UnsupportedSpotUseError
from tests.conversion_fixtures import separation

PdfMutator = Callable[[pikepdf.Pdf, pikepdf.Page], None]


class ConvertSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_target_related_structural_hazards_fail_atomically(self) -> None:
        cases: tuple[tuple[str, PdfMutator], ...] = (
            ("DeviceN", self._add_devicen),
            ("pattern", self._add_pattern),
            ("annotation", self._add_annotation),
            ("Type3", self._add_type_three_text),
            ("soft-mask", self._add_soft_mask),
            ("image-mask", self._add_image_mask),
            ("image-alternate", self._add_image_alternate),
            ("inline-image", self._add_inline_image),
            ("shading", self._add_shading),
            ("Type5-halftone", self._add_type_five_halftone),
            ("OPI", self._add_opi_image),
            ("DefaultCMYK", self._add_default_cmyk),
            ("transparency-group", self._add_transparency_group),
            ("signature", self._add_signature),
        )
        for index, (label, mutate) in enumerate(cases):
            with self.subTest(hazard=label):
                source = self._make_pdf(f"hazard-{index}.pdf", mutate)
                output = self.root / f"hazard-{index}-output.pdf"
                original = f"keep {label}".encode()
                output.write_bytes(original)

                with self.assertRaises((SpotPdfError, pikepdf.PdfError)):
                    convert_spot_to_cmyk(
                        source,
                        output,
                        "DemoSpot",
                        (0, 80, 100, 0),
                        force=True,
                    )

                self.assertEqual(output.read_bytes(), original)
                self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_cyclic_forms_fail_atomically(self) -> None:
        source = self.root / "cycle.pdf"
        output = self.root / "cycle-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        first = self._form(pdf, b"/Second Do")
        second = self._form(pdf, b"/First Do")
        first.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Second=second))
        second.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(First=first))
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(First=first),
        )
        page.Contents = pdf.make_stream(b"/Ink cs /First Do")
        pdf.save(source)
        original = b"keep cycle output"
        output.write_bytes(original)

        with self.assertRaisesRegex(UnsupportedSpotUseError, "cyclic"):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(output.read_bytes(), original)

    def test_uninvoked_shading_color_space_alias_fails_atomically(self) -> None:
        source = self._make_pdf("uninvoked-shading-alias.pdf", self._add_uninvoked_shading_alias)
        output = self.root / "uninvoked-shading-alias-output.pdf"
        original = b"keep shading alias output"
        output.write_bytes(original)

        with self.assertRaisesRegex(UnsupportedSpotUseError, "shading"):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_type3_charproc_with_inherited_target_alias_fails_atomically(self) -> None:
        source = self.root / "type3-inherited-alias.pdf"
        output = self.root / "type3-inherited-alias-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        glyph = pdf.make_stream(b"1000 0 d0 /Ink cs 1 scn 0 0 1000 1000 re f")
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type3,
                FontBBox=pikepdf.Array([0, 0, 1000, 1000]),
                FontMatrix=pikepdf.Array([0.001, 0, 0, 0.001, 0, 0]),
                CharProcs=pikepdf.Dictionary(A=glyph),
                Encoding=pikepdf.Dictionary(
                    Type=pikepdf.Name.Encoding,
                    Differences=pikepdf.Array([65, pikepdf.Name.A]),
                ),
                FirstChar=65,
                LastChar=65,
                Widths=pikepdf.Array([1000]),
            )
        )
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            Font=pikepdf.Dictionary(F1=font),
        )
        page.Contents = pdf.make_stream(b"BT /F1 50 Tf 50 50 Td (A) Tj ET")
        pdf.save(source)
        original = b"keep Type3 alias output"
        output.write_bytes(original)

        with self.assertRaisesRegex(UnsupportedSpotUseError, "Type3"):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_ext_gstate_type3_font_switch_fails_atomically(self) -> None:
        source = self.root / "ext-gstate-type3.pdf"
        output = self.root / "ext-gstate-type3-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        glyph = pdf.make_stream(b"1000 0 d0 0 0 1000 1000 re f")
        type_three = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type3,
                FontBBox=pikepdf.Array([0, 0, 1000, 1000]),
                FontMatrix=pikepdf.Array([0.001, 0, 0, 0.001, 0, 0]),
                CharProcs=pikepdf.Dictionary(A=glyph),
                Encoding=pikepdf.Dictionary(
                    Type=pikepdf.Name.Encoding,
                    Differences=pikepdf.Array([65, pikepdf.Name.A]),
                ),
                FirstChar=65,
                LastChar=65,
                Widths=pikepdf.Array([1000]),
                Resources=pikepdf.Dictionary(),
            )
        )
        type_one = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            Font=pikepdf.Dictionary(F1=type_one, F3=type_three),
            ExtGState=pikepdf.Dictionary(
                Switch=pikepdf.Dictionary(Font=pikepdf.Array([type_three, 12]))
            ),
        )
        page.Contents = pdf.make_stream(b"/Ink cs BT /F1 12 Tf /Switch gs (A) Tj ET")
        pdf.save(source)
        original = b"keep ExtGState font output"
        output.write_bytes(original)

        with self.assertRaisesRegex(UnsupportedSpotUseError, "Type 3"):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_non_page_rooted_form_resources_are_preflighted_atomically(self) -> None:
        source = self.root / "structure-form.pdf"
        output = self.root / "structure-form-output.pdf"
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page()
        form = self._form(pdf, b"")
        form.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            Shading=pikepdf.Dictionary(Unused=pikepdf.Dictionary(ColorSpace=pikepdf.Name.Ink)),
        )
        pdf.Root.StructTreeRoot = pikepdf.Dictionary(
            Type=pikepdf.Name.StructTreeRoot,
            K=pikepdf.Dictionary(Type=pikepdf.Name.MCR, Stm=form),
        )
        pdf.save(source)
        original = b"keep structure Form output"
        output.write_bytes(original)

        with self.assertRaisesRegex(UnsupportedSpotUseError, "shading"):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_nested_removable_alias_dependencies_fail_atomically(self) -> None:
        cases: tuple[tuple[str, PdfMutator], ...] = (
            ("Indexed-base", self._add_indexed_base_alias),
            ("Separation-alternate", self._add_separation_alternate_alias),
            ("DeviceN-alternate", self._add_devicen_alternate_alias),
            ("DefaultCMYK", self._add_default_cmyk_alias),
            ("ICCBased-alternate", self._add_icc_alternate_alias),
            ("shading-pattern", self._add_shading_pattern_alias),
            ("page-group", self._add_page_group_alias),
            ("detached-form-group", self._add_detached_form_group_alias),
        )
        for index, (label, mutate) in enumerate(cases):
            with self.subTest(dependency=label):
                source = self._make_unused_pdf(f"alias-{index}.pdf", mutate)
                output = self.root / f"alias-{index}-output.pdf"
                original = f"keep {label}".encode()
                output.write_bytes(original)

                with self.assertRaisesRegex(UnsupportedSpotUseError, "still referenced"):
                    convert_spot_to_cmyk(
                        source,
                        output,
                        "DemoSpot",
                        (0, 80, 100, 0),
                        force=True,
                    )

                self.assertEqual(output.read_bytes(), original)
                self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def _make_pdf(self, filename: str, mutate: PdfMutator) -> Path:
        path = self.root / filename
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 10 10 re f")
        mutate(pdf, page)
        pdf.save(path)
        return path

    def _make_unused_pdf(self, filename: str, mutate: PdfMutator) -> Path:
        path = self.root / filename
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Contents = pdf.make_stream(b"")
        mutate(pdf, page)
        pdf.save(path)
        return path

    @staticmethod
    def _add_devicen(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.ColorSpace.Multi = pikepdf.Array(
            [
                pikepdf.Name.DeviceN,
                pikepdf.Array([pikepdf.Name.DemoSpot, pikepdf.Name.Other]),
                pikepdf.Name.DeviceCMYK,
                pikepdf.Dictionary(
                    FunctionType=2,
                    Domain=pikepdf.Array([0, 1, 0, 1]),
                    C0=pikepdf.Array([0, 0, 0, 0]),
                    C1=pikepdf.Array([0, 1, 1, 0]),
                    N=1,
                ),
            ]
        )

    @staticmethod
    def _add_pattern(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.Pattern = pikepdf.Dictionary(
            Tile=pikepdf.Dictionary(ColorSpace=page.Resources.ColorSpace.Ink)
        )

    @classmethod
    def _add_annotation(cls, pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        appearance = cls._form(pdf, b"/Ink cs 0.5 scn 0 0 10 10 re f")
        appearance.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Annots = pikepdf.Array(
            [
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Annot,
                    Subtype=pikepdf.Name.Square,
                    Rect=pikepdf.Array([0, 0, 10, 10]),
                    AP=pikepdf.Dictionary(N=appearance),
                )
            ]
        )

    @staticmethod
    def _add_type_three_text(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.Font = pikepdf.Dictionary(F1=pikepdf.Dictionary(Subtype=pikepdf.Name.Type3))
        page.Contents.write(b"/Ink cs BT /F1 12 Tf (x) Tj ET")

    @staticmethod
    def _add_soft_mask(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.ExtGState = pikepdf.Dictionary(
            Masked=pikepdf.Dictionary(SMask=pikepdf.Dictionary(S=pikepdf.Name.Alpha))
        )
        page.Contents.write(b"/Masked gs /Ink cs 0 0 10 10 re f")

    @staticmethod
    def _add_image_mask(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        image = pdf.make_stream(b"\xff")
        image.Type = pikepdf.Name.XObject
        image.Subtype = pikepdf.Name.Image
        image.Width = 1
        image.Height = 1
        image.ImageMask = True
        image.BitsPerComponent = 1
        page.Resources.XObject = pikepdf.Dictionary(Mask=image)
        page.Contents.write(b"/Ink cs /Mask Do")

    @staticmethod
    def _add_image_alternate(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        alternate = pdf.make_stream(b"\x80")
        alternate.Type = pikepdf.Name.XObject
        alternate.Subtype = pikepdf.Name.Image
        alternate.Width = 1
        alternate.Height = 1
        alternate.BitsPerComponent = 8
        alternate.ColorSpace = pikepdf.Name.Ink
        image = pdf.make_stream(b"\x00\x00\x00")
        image.Type = pikepdf.Name.XObject
        image.Subtype = pikepdf.Name.Image
        image.Width = 1
        image.Height = 1
        image.BitsPerComponent = 8
        image.ColorSpace = pikepdf.Name.DeviceRGB
        image.Alternates = pikepdf.Array(
            [pikepdf.Dictionary(Image=alternate, DefaultForPrinting=True)]
        )
        page.Resources.XObject = pikepdf.Dictionary(Image=image)
        page.Contents.write(b"/Image Do")

    @staticmethod
    def _add_inline_image(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Contents.write(
            b"BI /W 1 /H 1 /BPC 8 /CS /G ID \x00 EI\n/Ink cs 0.5 scn 0 0 10 10 re f"
        )

    @staticmethod
    def _add_shading(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.Shading = pikepdf.Dictionary(
            Shade=pikepdf.Dictionary(ColorSpace=pikepdf.Name.Ink)
        )
        page.Contents.write(b"/Shade sh")

    @staticmethod
    def _add_type_five_halftone(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        component = pikepdf.Dictionary(
            HalftoneType=1,
            Frequency=60,
            Angle=45,
            SpotFunction=pikepdf.Name.Round,
        )
        halftone = pikepdf.Dictionary(HalftoneType=5, Default=component)
        halftone[pikepdf.Name.DemoSpot] = component
        page.Resources.ExtGState = pikepdf.Dictionary(
            GS=pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, HT=halftone)
        )
        page.Contents.write(b"/GS gs")

    @staticmethod
    def _add_opi_image(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        image = pdf.make_stream(b"\x00")
        image.Type = pikepdf.Name.XObject
        image.Subtype = pikepdf.Name.Image
        image.Width = 1
        image.Height = 1
        image.BitsPerComponent = 8
        image.ColorSpace = pikepdf.Name.DeviceGray
        version = pikepdf.Dictionary(
            Type=pikepdf.Name.OPI,
            Version=2.0,
            F=pikepdf.String("synthetic-external-image.tif"),
            Size=pikepdf.Array([1, 1]),
            CropRect=pikepdf.Array([0, 0, 1, 1]),
            Inks=pikepdf.Array([pikepdf.Name.monochrome, pikepdf.String("DemoSpot"), 1.0]),
        )
        image.OPI = pikepdf.Dictionary()
        image.OPI[pikepdf.Name("/2.0")] = version
        page.Resources.XObject = pikepdf.Dictionary(Image=image)
        page.Contents.write(b"/Image Do")

    @staticmethod
    def _add_uninvoked_shading_alias(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        tint = pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0]),
            C1=pikepdf.Array([1]),
            N=1,
        )
        page.Resources.Shading = pikepdf.Dictionary(
            Unused=pikepdf.Dictionary(
                ShadingType=2,
                ColorSpace=pikepdf.Name.Ink,
                Coords=pikepdf.Array([0, 0, 100, 0]),
                Function=tint,
                Extend=pikepdf.Array([True, True]),
            )
        )

    @classmethod
    def _add_indexed_base_alias(cls, pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.ColorSpace.IndexedInk = pikepdf.Array(
            [pikepdf.Name.Indexed, pikepdf.Name.Ink, 1, pikepdf.String(b"\x00\xff")]
        )

    @classmethod
    def _add_separation_alternate_alias(cls, pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.ColorSpace.Other = pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name.OtherSpot,
                pikepdf.Name.Ink,
                cls._four_component_tint(),
            ]
        )

    @classmethod
    def _add_devicen_alternate_alias(cls, pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.ColorSpace.Other = pikepdf.Array(
            [
                pikepdf.Name.DeviceN,
                pikepdf.Array([pikepdf.Name.OtherSpot]),
                pikepdf.Name.Ink,
                cls._four_component_tint(),
            ]
        )

    @staticmethod
    def _add_default_cmyk_alias(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.ColorSpace.DefaultCMYK = pikepdf.Name.Ink

    @staticmethod
    def _add_icc_alternate_alias(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        profile = pdf.make_stream(b"synthetic profile")
        profile.N = 4
        profile.Alternate = pikepdf.Name.Ink
        page.Resources.ColorSpace.ICC = pikepdf.Array([pikepdf.Name.ICCBased, profile])

    @classmethod
    def _add_shading_pattern_alias(cls, pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        shading = pikepdf.Dictionary(
            ShadingType=2,
            ColorSpace=pikepdf.Name.Ink,
            Coords=pikepdf.Array([0, 0, 10, 0]),
            Function=cls._one_component_tint(),
            Extend=pikepdf.Array([True, True]),
        )
        page.Resources.Pattern = pikepdf.Dictionary(
            Shade=pikepdf.Dictionary(
                Type=pikepdf.Name.Pattern,
                PatternType=2,
                Shading=shading,
            )
        )

    @staticmethod
    def _add_page_group_alias(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Group = pikepdf.Dictionary(
            Type=pikepdf.Name.Group,
            S=pikepdf.Name.Transparency,
            CS=pikepdf.Name.Ink,
        )

    @classmethod
    def _add_detached_form_group_alias(cls, pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del page
        form = cls._form(pdf, b"")
        form.Group = pikepdf.Dictionary(
            Type=pikepdf.Name.Group,
            S=pikepdf.Name.Transparency,
            CS=pikepdf.Name.Ink,
        )
        pdf.Root.StructTreeRoot = pikepdf.Dictionary(
            Type=pikepdf.Name.StructTreeRoot,
            K=pikepdf.Dictionary(Type=pikepdf.Name.MCR, Stm=form),
        )

    @staticmethod
    def _four_component_tint() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=pikepdf.Array([0, 1, 1, 0]),
            N=1,
        )

    @staticmethod
    def _one_component_tint() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0]),
            C1=pikepdf.Array([1]),
            N=1,
        )

    @staticmethod
    def _add_default_cmyk(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Resources.ColorSpace.DefaultCMYK = pikepdf.Name.DeviceCMYK

    @staticmethod
    def _add_transparency_group(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del pdf
        page.Group = pikepdf.Dictionary(S=pikepdf.Name.Transparency)

    @staticmethod
    def _add_signature(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        del page
        pdf.Root.Signature = pikepdf.Dictionary(Type=pikepdf.Name.Sig)

    @staticmethod
    def _form(pdf: pikepdf.Pdf, content: bytes) -> pikepdf.Stream:
        form = pdf.make_stream(content)
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 10, 10])
        return form


if __name__ == "__main__":
    unittest.main()
