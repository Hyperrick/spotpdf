from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.convert import convert_spot_to_cmyk
from spotpdf.model import UnsupportedSpotUseError
from tests.conversion_fixtures import separation


class ConvertResourceContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_piece_info_private_resources_are_not_mutation_targets(self) -> None:
        source = self.root / "piece-info-color-space.pdf"
        output = self.root / "piece-info-color-space-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary()
        page.Contents = pdf.make_stream(b"")
        page.PieceInfo = pikepdf.Dictionary(
            Vendor=pikepdf.Dictionary(
                LastModified=pikepdf.String("D:20260830000000Z"),
                Private=pikepdf.Dictionary(
                    Resources=pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
                ),
            )
        )
        pdf.save(source)
        source_sentinel = source.read_bytes()
        output_sentinel = b"existing destination must survive"
        output.write_bytes(output_sentinel)

        with self.assertRaisesRegex(
            UnsupportedSpotUseError,
            "not exclusively a removable resource alias",
        ):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(source.read_bytes(), source_sentinel)
        self.assertEqual(output.read_bytes(), output_sentinel)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])
        with pikepdf.open(source) as unchanged:
            private_spaces = unchanged.pages[0].PieceInfo.Vendor.Private.Resources.ColorSpace
            self.assertIn(pikepdf.Name.Ink, private_spaces)
            self.assertEqual(private_spaces.Ink[1], pikepdf.Name.DemoSpot)

    def test_private_form_resources_are_not_mutation_targets(self) -> None:
        source = self.root / "private-form.pdf"
        output = self.root / "private-form-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        private_form = pdf.make_stream(b"")
        private_form.Type = pikepdf.Name.XObject
        private_form.Subtype = pikepdf.Name.Form
        private_form.BBox = pikepdf.Array([0, 0, 10, 10])
        private_form.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Resources = pikepdf.Dictionary()
        page.Contents = pdf.make_stream(b"")
        page.PieceInfo = pikepdf.Dictionary(
            Vendor=pikepdf.Dictionary(
                LastModified=pikepdf.String("D:20260830000000Z"),
                Private=private_form,
            )
        )
        pdf.save(source)
        original = b"keep private Form output"
        output.write_bytes(original)

        with self.assertRaisesRegex(
            UnsupportedSpotUseError,
            "not exclusively a removable resource alias",
        ):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])
        with pikepdf.open(source) as unchanged:
            self.assertIn(
                pikepdf.Name.Ink,
                unchanged.pages[0].PieceInfo.Vendor.Private.Resources.ColorSpace,
            )

    def test_shared_indirect_page_resources_remove_direct_color_space_once(self) -> None:
        source = self.root / "inherited-shared-resources.pdf"
        output = self.root / "inherited-shared-resources-output.pdf"
        pdf = pikepdf.Pdf.new()
        first = pdf.add_blank_page()
        second = pdf.add_blank_page()
        shared_resources = pdf.make_indirect(
            pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        )
        parent = first.obj.Parent
        self.assertEqual(tuple(parent.objgen), tuple(second.obj.Parent.objgen))
        for page in (first, second):
            if pikepdf.Name.Resources in page.obj:
                del page.obj[pikepdf.Name.Resources]
            page.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 10 10 re f")
        parent.Resources = shared_resources
        pdf.save(source)

        result = convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        self.assertEqual(result.resources_removed, 1)
        self.assertEqual(result.page_content_sequences_changed, 2)
        with pikepdf.open(output) as converted:
            self.assertEqual(
                tuple(converted.pages[0].Resources.objgen),
                tuple(converted.pages[1].Resources.objgen),
            )
            self.assertNotIn(pikepdf.Name.Ink, converted.pages[0].Resources.ColorSpace)

    def test_page_resources_shared_with_trailer_info_are_rejected_atomically(self) -> None:
        source = self.root / "resources-shared-with-info.pdf"
        output = self.root / "resources-shared-with-info-output.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        shared = pdf.make_indirect(
            pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Ink=separation()),
                PrivateMarker=pikepdf.String("keep trailer-owned dictionary intact"),
            )
        )
        page.Resources = shared
        page.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 10 10 re f")
        pdf.trailer.Info = shared
        pdf.save(source)
        source_sentinel = source.read_bytes()
        output_sentinel = b"existing destination must survive"
        output.write_bytes(output_sentinel)

        with self.assertRaisesRegex(
            UnsupportedSpotUseError,
            "trailer /Info: target resource container has a non-content owner",
        ):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(source.read_bytes(), source_sentinel)
        self.assertEqual(output.read_bytes(), output_sentinel)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])
        with pikepdf.open(source) as unchanged:
            self.assertEqual(
                unchanged.trailer.Info.PrivateMarker,
                pikepdf.String("keep trailer-owned dictionary intact"),
            )
            self.assertIn(pikepdf.Name.Ink, unchanged.trailer.Info.ColorSpace)


if __name__ == "__main__":
    unittest.main()
