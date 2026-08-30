from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pikepdf

import spotpdf.rename as rename_module
from spotpdf.document import inspect_pdf
from spotpdf.metadata_fingerprint import xml_metadata_fingerprint
from spotpdf.model import SpotPdfError
from spotpdf.rename import rename_spot


class RenamePostSaveVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_changed_mixing_hint_payload_is_rejected_atomically(self) -> None:
        source = self.root / "mixing-hint-input.pdf"
        output = self.root / "mixing-hint-output.pdf"
        output.write_bytes(b"keep-existing")
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            mixed = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.Old]),
                    pikepdf.Name.DeviceCMYK,
                    self._function(c1=pikepdf.Array([0, 0.8, 1, 0])),
                    pikepdf.Dictionary(
                        Colorants=pikepdf.Dictionary(Old=self._separation("Old", 0.8)),
                        MixingHints=pikepdf.Dictionary(
                            Solidities=pikepdf.Dictionary(Old=0.8),
                            PrintingOrder=pikepdf.Array([pikepdf.Name.Old]),
                        ),
                    ),
                ]
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Ink=self._separation("Old", 0.8),
                    Mixed=mixed,
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        original_save = rename_module.save_pdf

        def mutate_payload(pdf: pikepdf.Pdf, path: Path) -> None:
            hints = pdf.pages[0].Resources.ColorSpace.Mixed[4].MixingHints
            hints.Solidities.New = 0.1
            original_save(pdf, path)

        with (
            mock.patch("spotpdf.rename.save_pdf", side_effect=mutate_payload),
            self.assertRaisesRegex(SpotPdfError, "dependency values or definition contexts"),
        ):
            rename_spot(source, output, "Old", "New", force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")

    def test_swapped_tint_transforms_are_rejected_by_definition_context(self) -> None:
        source = self.root / "swapped-preview-input.pdf"
        output = self.root / "swapped-preview-output.pdf"
        output.write_bytes(b"keep-existing")
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    A=self._separation("Old", 0.3),
                    B=self._separation("Old", 0.9),
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        original_save = rename_module.save_pdf

        def swap_previews(pdf: pikepdf.Pdf, path: Path) -> None:
            spaces = pdf.pages[0].Resources.ColorSpace
            first = spaces.A[3]
            second = spaces.B[3]
            spaces.A[3] = second
            spaces.B[3] = first
            original_save(pdf, path)

        with (
            mock.patch("spotpdf.rename.save_pdf", side_effect=swap_previews),
            self.assertRaisesRegex(SpotPdfError, "dependency values or definition contexts"),
        ):
            rename_spot(source, output, "Old", "New", force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")

    def test_unrelated_spot_preview_change_is_rejected_atomically(self) -> None:
        source = self.root / "unrelated-preview-input.pdf"
        output = self.root / "unrelated-preview-output.pdf"
        output.write_bytes(b"keep-existing")
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Target=self._separation("Old", 0.3),
                    Unrelated=self._separation("Other", 0.9),
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        original_save = rename_module.save_pdf

        def mutate_unrelated_preview(pdf: pikepdf.Pdf, path: Path) -> None:
            function = pdf.pages[0].Resources.ColorSpace.Unrelated[3]
            function.C1 = pikepdf.Array([1, 0, 0, 0])
            original_save(pdf, path)

        with (
            mock.patch("spotpdf.rename.save_pdf", side_effect=mutate_unrelated_preview),
            self.assertRaisesRegex(SpotPdfError, "alternate spaces or tint transforms"),
        ):
            rename_spot(source, output, "Old", "New", force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")

    def test_unplanned_same_name_catalog_and_info_changes_are_rejected(self) -> None:
        source = self.root / "unplanned-name-input.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Target=self._separation("Old", 0.3))
            )
            page.Contents = pdf.make_stream(b"")
            pdf.Root.CustomLabel = pikepdf.Name.Old
            pdf.docinfo[pikepdf.Name.Title] = pikepdf.String("Old")
            pdf.save(source)

        mutations = (
            ("catalog", lambda pdf: setattr(pdf.Root, "CustomLabel", pikepdf.Name.New)),
            (
                "info",
                lambda pdf: pdf.docinfo.__setitem__(pikepdf.Name.Title, pikepdf.String("New")),
            ),
        )
        original_save = rename_module.save_pdf

        def save_with_mutation(mutation):
            def mutate_unplanned(pdf: pikepdf.Pdf, path: Path) -> None:
                mutation(pdf)
                original_save(pdf, path)

            return mutate_unplanned

        for label, mutate in mutations:
            with self.subTest(location=label):
                output = self.root / f"unplanned-name-{label}-output.pdf"
                output.write_bytes(b"keep-existing")

                with (
                    mock.patch(
                        "spotpdf.rename.save_pdf",
                        side_effect=save_with_mutation(mutate),
                    ),
                    self.assertRaisesRegex(
                        SpotPdfError,
                        "saved PDF object semantics changed during rewrite",
                    ),
                ):
                    rename_spot(source, output, "Old", "New", force=True)

                self.assertEqual(output.read_bytes(), b"keep-existing")

    def test_unrelated_unfilterable_image_stream_is_preserved(self) -> None:
        source = self.root / "dct-image-input.pdf"
        output = self.root / "dct-image-output.pdf"
        image_data = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0V"
            "FhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/wAALCAAB"
            "AAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAB//EABQQAQAAAAAAAAAAAAAAAAAAA"
            "AD/2gAIAQEAAD8AZn//2Q=="
        )
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            image = pdf.make_stream(image_data)
            image.Type = pikepdf.Name.XObject
            image.Subtype = pikepdf.Name.Image
            image.Width = 1
            image.Height = 1
            image.ColorSpace = pikepdf.Name.DeviceRGB
            image.BitsPerComponent = 8
            image.Filter = pikepdf.Name.DCTDecode
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Target=self._separation("Old", 0.3)),
                XObject=pikepdf.Dictionary(Photo=image),
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        rename_spot(source, output, "Old", "New")

        with pikepdf.open(output) as pdf:
            image = pdf.pages[0].Resources.XObject.Photo
            self.assertEqual(image.get(pikepdf.Name.Filter), pikepdf.Name.DCTDecode)
            self.assertEqual(image.read_raw_bytes(), image_data)

    def test_xmp_packet_reserialization_does_not_block_rename(self) -> None:
        source = self.root / "xmp-reserialization-input.pdf"
        output = self.root / "xmp-reserialization-output.pdf"
        xmp = (
            b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
            b'  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            b'    <rdf:Description rdf:about="" title="Preserve me"/>\n'
            b"  </rdf:RDF>\n"
            b"</x:xmpmeta>\n"
            b'<?xpacket end="w"?>'
        )
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Target=self._separation("Old", 0.3))
            )
            page.Contents = pdf.make_stream(b"")
            metadata = pdf.make_stream(xmp)
            metadata.Type = pikepdf.Name.Metadata
            metadata.Subtype = pikepdf.Name.XML
            pdf.Root.Metadata = metadata
            pdf.save(source, compress_streams=False)

        source_bytes = source.read_bytes()
        double_begin = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        single_begin = b"<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>"
        double_end = b'<?xpacket end="w"?>'
        single_end = b"<?xpacket end='w'?>"
        self.assertIn(double_begin, source_bytes)
        self.assertIn(double_end, source_bytes)
        source.write_bytes(
            source_bytes.replace(double_begin, single_begin).replace(double_end, single_end)
        )
        with pikepdf.open(source) as pdf:
            before_metadata = pdf.Root.Metadata.read_bytes()

        rename_spot(source, output, "Old", "New")

        self.assertNotIn("Old", inspect_pdf(output).colorants)
        with pikepdf.open(output) as pdf:
            after_metadata = pdf.Root.Metadata.read_bytes()
            self.assertEqual(str(pdf.pages[0].Resources.ColorSpace.Target[1]), "/New")
        self.assertNotEqual(before_metadata, after_metadata)
        self.assertEqual(
            xml_metadata_fingerprint(before_metadata),
            xml_metadata_fingerprint(after_metadata),
        )

    @classmethod
    def _separation(cls, name: str, magenta: float) -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name(f"/{name}"),
                pikepdf.Name.DeviceCMYK,
                cls._function(c1=pikepdf.Array([0, magenta, 1, 0])),
            ]
        )

    @staticmethod
    def _function(*, c1: pikepdf.Array) -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=c1,
            N=1,
        )


if __name__ == "__main__":
    unittest.main()
