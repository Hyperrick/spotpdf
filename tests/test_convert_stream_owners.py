from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.convert import convert_spot_to_cmyk
from spotpdf.model import UnsupportedSpotUseError
from tests.conversion_fixtures import separation

_ATTACHMENT_BYTES = b"/Ink cs 0.5 scn 0 0 10 10 re f\n% ATTACHMENT_SENTINEL\n"


class ConvertStreamOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_page_content_shared_as_embedded_file_fails_atomically(self) -> None:
        source = self.root / "page-attachment.pdf"
        pdf = pikepdf.Pdf.new()
        page = self._target_page(pdf, _ATTACHMENT_BYTES)
        self._attach(pdf, page.Contents, "page-content.bin")
        pdf.save(source)

        self._assert_atomic_failure(source, "page-attachment-output.pdf", "embedded-file data")
        with pikepdf.open(source) as unchanged:
            self.assertEqual(self._attachment(unchanged).read_bytes(), _ATTACHMENT_BYTES)

    def test_form_shared_as_embedded_file_fails_atomically(self) -> None:
        source = self.root / "form-attachment.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        form = self._form(pdf, _ATTACHMENT_BYTES)
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(Paint=form),
        )
        page.Contents = pdf.make_stream(b"/Paint Do")
        self._attach(pdf, form, "form-content.bin")
        pdf.save(source)

        self._assert_atomic_failure(source, "form-attachment-output.pdf", "embedded-file data")
        with pikepdf.open(source) as unchanged:
            self.assertEqual(self._attachment(unchanged).read_bytes(), _ATTACHMENT_BYTES)

    def test_tagged_contents_array_member_fails_atomically(self) -> None:
        source = self.root / "tagged-array.pdf"
        pdf = pikepdf.Pdf.new()
        direct_page = pdf.add_blank_page()
        first = pdf.make_stream(b"")
        second_bytes = (
            b"/P <</MCID 0>> BDC /Ink cs 0.5 scn 0 0 10 10 re f EMC\n% TAGGED_STREAM_SENTINEL\n"
        )
        shared_form = self._form(pdf, second_bytes)
        shared_form.StructParents = 0
        direct_page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        direct_page.Contents = pikepdf.Array([first, shared_form])
        invocation_page = pdf.add_blank_page()
        invocation_page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation("OtherSpot")),
            XObject=pikepdf.Dictionary(Tagged=shared_form),
        )
        invocation_page.Contents = pdf.make_stream(b"/Tagged Do")
        self._add_form_structure(pdf, invocation_page, shared_form)
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "tagged-array-output.pdf",
            "Contents-array member.*non-content stream role",
        )
        with pikepdf.open(source) as unchanged:
            saved = unchanged.pages[0].obj.Contents[1]
            self.assertEqual(saved.read_bytes(), second_bytes)
            self.assertEqual(
                tuple(unchanged.Root.StructTreeRoot.K.K.Stm.objgen),
                tuple(saved.objgen),
            )

    def test_tagged_form_stm_fails_atomically(self) -> None:
        source = self.root / "tagged-form.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        form = self._form(
            pdf,
            b"/P <</MCID 0>> BDC /Ink cs 0.5 scn 0 0 10 10 re f EMC",
        )
        form.StructParents = 0
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(Tagged=form),
        )
        page.Contents = pdf.make_stream(b"/Tagged Do")
        self._add_form_structure(pdf, page, form)
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "tagged-form-output.pdf",
            "non-content stream role",
        )

    def test_page_write_shared_as_form_fails_atomically(self) -> None:
        source = self._make_mixed_page_form_pdf(
            "page-write-shared-form.pdf",
            page_spot="DemoSpot",
            form_spot="OtherSpot",
        )

        self._assert_atomic_failure(
            source,
            "page-write-shared-form-output.pdf",
            "Form XObject content",
        )

    def test_form_write_shared_as_page_content_fails_atomically(self) -> None:
        source = self._make_mixed_page_form_pdf(
            "form-write-shared-page.pdf",
            page_spot="OtherSpot",
            form_spot="DemoSpot",
        )

        self._assert_atomic_failure(
            source,
            "form-write-shared-page-output.pdf",
            "Form XObject content",
        )

    def test_form_write_with_only_page_owner_fails_atomically(self) -> None:
        source = self.root / "form-write-page-owner.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        content = self._form(pdf, _ATTACHMENT_BYTES)
        content.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation("OtherSpot"))
        )
        page.Contents = content
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "form-write-page-owner-output.pdf",
            "not exclusively a removable resource alias",
        )

    def test_page_content_shared_as_metadata_fails_atomically(self) -> None:
        source = self.root / "page-metadata.pdf"
        pdf = pikepdf.Pdf.new()
        page = self._target_page(pdf, b"/Ink cs 0.5 scn 0 0 10 10 re f")
        pdf.Root.Metadata = page.Contents
        pdf.save(source, fix_metadata_version=False)

        self._assert_atomic_failure(source, "page-metadata-output.pdf", "metadata stream data")

    def test_nested_custom_xobject_reference_is_not_a_form_owner(self) -> None:
        source = self.root / "nested-xobject-custom.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        nested = self._form(pdf, _ATTACHMENT_BYTES)
        container = self._form(pdf, b"")
        container.Custom = nested
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(Container=container, Nested=nested),
        )
        page.Contents = pdf.make_stream(b"/Container Do")
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "nested-xobject-custom-output.pdf",
            "non-content stream role",
        )

    def test_piece_info_resource_shape_is_not_a_form_owner(self) -> None:
        source = self.root / "piece-info-resource-shape.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        nested = self._form(pdf, _ATTACHMENT_BYTES)
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(Actual=nested),
        )
        page.Contents = pdf.make_stream(b"")
        page.PieceInfo = pikepdf.Dictionary(
            Vendor=pikepdf.Dictionary(
                LastModified=pikepdf.String("D:20260830000000Z"),
                Private=pikepdf.Dictionary(
                    Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Target=nested))
                ),
            )
        )
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "piece-info-resource-shape-output.pdf",
            "non-content stream role",
        )

    def test_piece_info_mcr_shape_is_not_a_marked_content_owner(self) -> None:
        source = self.root / "piece-info-mcr-shape.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        nested = self._form(pdf, _ATTACHMENT_BYTES)
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=pikepdf.Dictionary(Actual=nested),
        )
        page.Contents = pdf.make_stream(b"")
        page.PieceInfo = pikepdf.Dictionary(
            Vendor=pikepdf.Dictionary(
                LastModified=pikepdf.String("D:20260830000000Z"),
                Private=pikepdf.Dictionary(Type=pikepdf.Name.MCR, Stm=nested),
            )
        )
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "piece-info-mcr-shape-output.pdf",
            "non-content stream role",
        )

    def test_shared_xobject_dictionary_private_alias_fails_atomically(self) -> None:
        source = self.root / "shared-xobject-dictionary.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        form = self._form(pdf, _ATTACHMENT_BYTES)
        xobjects = pdf.make_indirect(pikepdf.Dictionary(Paint=form))
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation()),
            XObject=xobjects,
        )
        page.Contents = pdf.make_stream(b"/Paint Do")
        page.ZZPrivate = xobjects
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "shared-xobject-dictionary-output.pdf",
            "owner container has a non-content owner",
        )
        with pikepdf.open(source) as unchanged:
            self.assertEqual(unchanged.pages[0].ZZPrivate.Paint.read_bytes(), _ATTACHMENT_BYTES)

    def test_shared_parent_resources_private_alias_fails_atomically(self) -> None:
        source = self.root / "shared-parent-resources.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        form = self._form(pdf, _ATTACHMENT_BYTES)
        form.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        resources = pdf.make_indirect(pikepdf.Dictionary(XObject=pikepdf.Dictionary(Paint=form)))
        page.Resources = resources
        page.Contents = pdf.make_stream(b"/Paint Do")
        page.ZZPrivate = resources
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "shared-parent-resources-output.pdf",
            "owner container has a non-content owner",
        )
        with pikepdf.open(source) as unchanged:
            private_form = unchanged.pages[0].ZZPrivate.XObject.Paint
            self.assertEqual(private_form.read_bytes(), _ATTACHMENT_BYTES)

    def test_private_alias_to_outer_form_hides_no_nested_form_owner(self) -> None:
        source = self.root / "private-outer-form-alias.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        inner = self._form(pdf, _ATTACHMENT_BYTES)
        inner.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        outer = self._form(pdf, b"/Inner Do")
        outer.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Inner=inner))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Outer=outer))
        page.Contents = pdf.make_stream(b"/Outer Do")
        page.ZZPrivate = outer
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "private-outer-form-alias-output.pdf",
            "owner container has a non-content owner",
        )
        with pikepdf.open(source) as unchanged:
            private_inner = unchanged.pages[0].ZZPrivate.Resources.XObject.Inner
            self.assertEqual(private_inner.read_bytes(), _ATTACHMENT_BYTES)

    def test_shared_contents_array_private_alias_fails_atomically(self) -> None:
        source = self.root / "shared-contents-array.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        content = pdf.make_stream(_ATTACHMENT_BYTES)
        contents = pdf.make_indirect(pikepdf.Array([content]))
        page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Contents = contents
        page.ZZPrivate = contents
        pdf.save(source)

        self._assert_atomic_failure(
            source,
            "shared-contents-array-output.pdf",
            "owner container has a non-content owner",
        )
        with pikepdf.open(source) as unchanged:
            self.assertEqual(unchanged.pages[0].ZZPrivate[0].read_bytes(), _ATTACHMENT_BYTES)

    def _make_mixed_page_form_pdf(
        self,
        filename: str,
        *,
        page_spot: str,
        form_spot: str,
    ) -> Path:
        source = self.root / filename
        pdf = pikepdf.Pdf.new()
        shared = self._form(pdf, _ATTACHMENT_BYTES)
        direct_page = pdf.add_blank_page()
        direct_page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation(page_spot))
        )
        direct_page.Contents = shared
        invocation_page = pdf.add_blank_page()
        invocation_page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation(form_spot)),
            XObject=pikepdf.Dictionary(Shared=shared),
        )
        invocation_page.Contents = pdf.make_stream(b"/Shared Do")
        pdf.save(source)
        return source

    def _assert_atomic_failure(
        self,
        source: Path,
        output_name: str,
        message: str,
    ) -> None:
        output = self.root / output_name
        output_sentinel = b"existing output must remain byte-for-byte unchanged"
        input_sentinel = source.read_bytes()
        output.write_bytes(output_sentinel)

        with self.assertRaisesRegex(UnsupportedSpotUseError, message):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )

        self.assertEqual(source.read_bytes(), input_sentinel)
        self.assertEqual(output.read_bytes(), output_sentinel)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    @staticmethod
    def _target_page(pdf: pikepdf.Pdf, content: bytes) -> pikepdf.Page:
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))
        page.Contents = pdf.make_stream(content)
        return page

    @staticmethod
    def _attach(pdf: pikepdf.Pdf, stream: pikepdf.Stream, filename: str) -> None:
        filespec = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Filespec,
                F=pikepdf.String(filename),
                EF=pikepdf.Dictionary(F=stream),
            )
        )
        pdf.Root.Names = pikepdf.Dictionary(
            EmbeddedFiles=pikepdf.Dictionary(
                Names=pikepdf.Array([pikepdf.String(filename), filespec])
            )
        )

    @staticmethod
    def _attachment(pdf: pikepdf.Pdf) -> pikepdf.Stream:
        return pdf.Root.Names.EmbeddedFiles.Names[1].EF.F

    @staticmethod
    def _add_form_structure(
        pdf: pikepdf.Pdf,
        page: pikepdf.Page,
        form: pikepdf.Stream,
    ) -> None:
        structure_root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
        structure_element = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem,
                S=pikepdf.Name.P,
                P=structure_root,
            )
        )
        structure_element.K = pikepdf.Dictionary(
            Type=pikepdf.Name.MCR,
            Pg=page.obj,
            Stm=form,
            MCID=0,
        )
        structure_root.K = structure_element
        structure_root.ParentTree = pikepdf.Dictionary(
            Nums=pikepdf.Array([0, pikepdf.Array([structure_element])])
        )
        pdf.Root.StructTreeRoot = structure_root

    @staticmethod
    def _form(pdf: pikepdf.Pdf, content: bytes) -> pikepdf.Stream:
        form = pdf.make_stream(content)
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 10, 10])
        return form


if __name__ == "__main__":
    unittest.main()
