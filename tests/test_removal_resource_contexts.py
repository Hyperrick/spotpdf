from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.document import remove_all_spots, remove_spot
from spotpdf.model import UnsupportedSpotUseError
from tests.conversion_fixtures import separation


class RemovalResourceContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_private_form_target_fails_closed_for_exact_and_all_modes(self) -> None:
        source = self.root / "private-form.pdf"
        private_bytes = b"/Ink cs 1 scn 0 0 10 10 re f\n"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            private_form = self._form(pdf, private_bytes)
            private_form.Resources = self._target_resources()
            page.Resources = pikepdf.Dictionary()
            page.Contents = pdf.make_stream(b"")
            page.PieceInfo = pikepdf.Dictionary(
                Vendor=pikepdf.Dictionary(
                    LastModified=pikepdf.String("D:20260830000000Z"),
                    Private=private_form,
                )
            )
            pdf.save(source)
        source_bytes = source.read_bytes()

        for mode in ("exact", "all"):
            with self.subTest(mode=mode):
                output = self.root / f"private-form-{mode}-output.pdf"
                sentinel = f"preserve {mode} output".encode()
                output.write_bytes(sentinel)

                with self.assertRaisesRegex(
                    UnsupportedSpotUseError,
                    "not exclusively a removable content-resource alias",
                ):
                    if mode == "exact":
                        remove_spot(source, output, "DemoSpot", force=True)
                    else:
                        remove_all_spots(source, output, force=True)

                self.assertEqual(source.read_bytes(), source_bytes)
                self.assertEqual(output.read_bytes(), sentinel)
                self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

        with pikepdf.open(source) as unchanged:
            saved = unchanged.pages[0].PieceInfo.Vendor.Private
            self.assertEqual(saved.read_bytes(), private_bytes)
            self.assertIn(pikepdf.Name.Ink, saved.Resources.ColorSpace)

    def test_private_resource_dictionary_is_not_treated_as_page_resources(self) -> None:
        source = self.root / "private-resources.pdf"
        output = self.root / "private-resources-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            page.Resources = pikepdf.Dictionary()
            page.Contents = pdf.make_stream(b"")
            page.PieceInfo = pikepdf.Dictionary(
                Vendor=pikepdf.Dictionary(
                    LastModified=pikepdf.String("D:20260830000000Z"),
                    Private=pikepdf.Dictionary(Resources=self._target_resources()),
                )
            )
            pdf.save(source)

        with self.assertRaisesRegex(
            UnsupportedSpotUseError,
            "not exclusively a removable content-resource alias",
        ):
            remove_spot(source, output, "DemoSpot")

        self.assertFalse(output.exists())
        with pikepdf.open(source) as unchanged:
            spaces = unchanged.pages[0].PieceInfo.Vendor.Private.Resources.ColorSpace
            self.assertIn(pikepdf.Name.Ink, spaces)

    def test_uninvoked_form_with_target_paint_is_rewritten_before_alias_removal(self) -> None:
        source = self.root / "uninvoked-form.pdf"
        output = self.root / "uninvoked-form-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            unused = self._form(pdf, b"/Ink cs 1 scn 0 0 10 10 re f\n")
            unused.Resources = self._target_resources()
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Unused=unused))
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        stats = remove_spot(source, output, "DemoSpot")

        self.assertEqual(stats.forms_changed, 1)
        self.assertEqual(stats.fills_removed, 1)
        self.assertEqual(stats.resources_removed, 1)
        self.assertEqual(stats.pages_changed, set())
        with pikepdf.open(output) as cleaned:
            saved = cleaned.pages[0].Resources.XObject.Unused
            self.assertNotIn(pikepdf.Name.Ink, saved.Resources.ColorSpace)
            operators = [str(item.operator) for item in pikepdf.parse_content_stream(saved)]
            self.assertNotIn("f", operators)
            self.assertIn("n", operators)

    def test_uninvoked_form_with_unused_target_alias_preserves_stream(self) -> None:
        source = self.root / "uninvoked-unused-alias.pdf"
        output = self.root / "uninvoked-unused-alias-output.pdf"
        form_bytes = b"0 0 m 10 10 l S\n% UNUSED_ALIAS_SENTINEL\n"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            unused = self._form(pdf, form_bytes)
            unused.Resources = self._target_resources()
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Unused=unused))
            page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        stats = remove_spot(source, output, "DemoSpot")

        self.assertEqual(stats.forms_changed, 0)
        self.assertEqual(stats.resources_removed, 1)
        self.assertEqual(stats.pages_changed, set())
        with pikepdf.open(output) as cleaned:
            saved = cleaned.pages[0].Resources.XObject.Unused
            self.assertEqual(saved.read_bytes(), form_bytes)
            self.assertNotIn(pikepdf.Name.Ink, saved.Resources.ColorSpace)

    def test_shared_uninvoked_form_allows_identical_unchanged_contexts(self) -> None:
        form_bytes = b"% SHARED_UNCHANGED_SENTINEL\n"
        for target_page in range(3):
            with self.subTest(target_page=target_page):
                source = self.root / f"shared-uninvoked-form-{target_page}.pdf"
                output = self.root / f"shared-uninvoked-form-{target_page}-output.pdf"
                with pikepdf.Pdf.new() as pdf:
                    pages = [pdf.add_blank_page() for _ in range(3)]
                    shared = self._form(pdf, form_bytes)
                    for index, page in enumerate(pages):
                        if index == target_page:
                            resources = self._target_resources()
                            contents = b"/Ink cs 1 scn 0 0 10 10 re f\n"
                        else:
                            resources = pikepdf.Dictionary(
                                ColorSpace=pikepdf.Dictionary(Other=separation(f"OtherSpot{index}"))
                            )
                            contents = b""
                        resources.XObject = pikepdf.Dictionary(Shared=shared)
                        page.Resources = resources
                        page.Contents = pdf.make_stream(contents)
                    pdf.save(source)

                stats = remove_spot(source, output, "DemoSpot")

                self.assertEqual(stats.forms_changed, 0)
                self.assertEqual(stats.resources_removed, 1)
                with pikepdf.open(output) as cleaned:
                    for index, page in enumerate(cleaned.pages):
                        self.assertEqual(
                            page.Resources.XObject.Shared.read_bytes(),
                            form_bytes,
                        )
                        if index == target_page:
                            self.assertNotIn(pikepdf.Name.Ink, page.Resources.ColorSpace)
                        else:
                            self.assertIn(pikepdf.Name.Other, page.Resources.ColorSpace)

    def test_shared_inherited_form_allows_identical_replacements(self) -> None:
        form_bytes = b"/Ink cs 1 scn 0 0 10 10 re f\n"
        for invoked in (False, True):
            with self.subTest(invoked=invoked):
                source = self.root / f"shared-identical-rewrite-{invoked}.pdf"
                output = self.root / f"shared-identical-rewrite-{invoked}-output.pdf"
                with pikepdf.Pdf.new() as pdf:
                    pages = [pdf.add_blank_page() for _ in range(2)]
                    shared = self._form(pdf, form_bytes)
                    for page in pages:
                        resources = self._target_resources()
                        resources.XObject = pikepdf.Dictionary(Shared=shared)
                        page.Resources = resources
                        page.Contents = pdf.make_stream(b"/Shared Do\n" if invoked else b"")
                    pdf.save(source)

                stats = remove_spot(source, output, "DemoSpot")

                self.assertEqual(stats.forms_changed, 1)
                self.assertEqual(stats.fills_removed, 1)
                self.assertEqual(stats.resources_removed, 2)
                self.assertEqual(stats.pages_changed, {1, 2} if invoked else set())
                with pikepdf.open(output) as cleaned:
                    first = cleaned.pages[0].Resources.XObject.Shared
                    second = cleaned.pages[1].Resources.XObject.Shared
                    self.assertEqual(first.read_bytes(), second.read_bytes())
                    operators = [str(item.operator) for item in pikepdf.parse_content_stream(first)]
                    self.assertNotIn("f", operators)
                    self.assertIn("n", operators)
                    for page in cleaned.pages:
                        self.assertNotIn(pikepdf.Name.Ink, page.Resources.ColorSpace)

    def test_form_inline_image_target_alias_fails_atomically(self) -> None:
        form_bytes = b"BI /W 1 /H 1 /BPC 8 /CS /Ink ID \x00 EI\n"
        for invoked in (False, True):
            for mode in ("exact", "all"):
                with self.subTest(invoked=invoked, mode=mode):
                    source = self.root / f"form-inline-{invoked}-{mode}.pdf"
                    output = self.root / f"form-inline-{invoked}-{mode}-output.pdf"
                    with pikepdf.Pdf.new() as pdf:
                        page = pdf.add_blank_page()
                        form = self._form(pdf, form_bytes)
                        form.Resources = self._target_resources()
                        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Paint=form))
                        page.Contents = pdf.make_stream(b"/Paint Do\n" if invoked else b"")
                        pdf.save(source)
                    source_bytes = source.read_bytes()
                    sentinel = f"preserve inline {invoked} {mode} output".encode()
                    output.write_bytes(sentinel)

                    with self.assertRaisesRegex(
                        UnsupportedSpotUseError,
                        "inline images with target spot resources",
                    ):
                        if mode == "exact":
                            remove_spot(source, output, "DemoSpot", force=True)
                        else:
                            remove_all_spots(source, output, force=True)

                    self.assertEqual(source.read_bytes(), source_bytes)
                    self.assertEqual(output.read_bytes(), sentinel)
                    self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_retained_color_space_alias_dependencies_fail_atomically(self) -> None:
        for dependency in (
            "indexed",
            "separation",
            "icc",
            "default",
            "group",
            "trailer",
        ):
            with self.subTest(dependency=dependency):
                source = self.root / f"alias-dependency-{dependency}.pdf"
                output = self.root / f"alias-dependency-{dependency}-output.pdf"
                with pikepdf.Pdf.new() as pdf:
                    page = pdf.add_blank_page()
                    color_spaces = pikepdf.Dictionary(Ink=separation())
                    if dependency == "indexed":
                        color_spaces.IndexedInk = pikepdf.Array(
                            [pikepdf.Name.Indexed, pikepdf.Name.Ink, 1, pikepdf.String(b"\x00\xff")]
                        )
                    elif dependency == "separation":
                        retained = separation("OtherSpot")
                        retained[2] = pikepdf.Name.Ink
                        color_spaces.Other = retained
                    elif dependency == "icc":
                        profile = pdf.make_stream(b"")
                        profile.N = 4
                        profile.Alternate = pikepdf.Name.Ink
                        color_spaces.ICC = pikepdf.Array([pikepdf.Name.ICCBased, profile])
                    elif dependency == "default":
                        color_spaces.DefaultCMYK = pikepdf.Name.Ink
                    page.Resources = pikepdf.Dictionary(ColorSpace=color_spaces)
                    if dependency == "group":
                        page.Group = pikepdf.Dictionary(
                            S=pikepdf.Name.Transparency,
                            CS=pikepdf.Name.Ink,
                        )
                    elif dependency == "trailer":
                        pdf.trailer.Info = pdf.make_indirect(
                            pikepdf.Dictionary(CS=pikepdf.Name.Ink)
                        )
                    page.Contents = pdf.make_stream(b"/Ink cs 1 scn 0 0 10 10 re f\n")
                    pdf.save(source)
                source_bytes = source.read_bytes()
                sentinel = f"preserve {dependency} output".encode()
                output.write_bytes(sentinel)

                with self.assertRaisesRegex(
                    UnsupportedSpotUseError,
                    "removable color-space alias.*still referenced",
                ):
                    remove_spot(source, output, "DemoSpot", force=True)

                self.assertEqual(source.read_bytes(), source_bytes)
                self.assertEqual(output.read_bytes(), sentinel)
                self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_malformed_target_alias_cannot_escape_dependency_scan(self) -> None:
        source = self.root / "malformed-target-alias.pdf"
        output = self.root / "malformed-target-alias-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            malformed = separation()
            malformed[1] = pikepdf.String("DemoSpot")
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Valid=separation(),
                    Ink=malformed,
                )
            )
            page.Group = pikepdf.Dictionary(
                S=pikepdf.Name.Transparency,
                CS=pikepdf.Name.Ink,
            )
            page.Contents = pdf.make_stream(b"/Valid cs 1 scn 0 0 10 10 re f\n")
            pdf.save(source)
        source_bytes = source.read_bytes()
        sentinel = b"preserve malformed target alias output"
        output.write_bytes(sentinel)

        with self.assertRaisesRegex(
            UnsupportedSpotUseError,
            "not exclusively a removable content-resource alias",
        ):
            remove_spot(source, output, "DemoSpot", force=True)

        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(output.read_bytes(), sentinel)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_page_resources_with_non_content_owner_fail_before_rewrite(self) -> None:
        source = self.root / "shared-resources.pdf"
        output = self.root / "shared-resources-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            shared = pdf.make_indirect(self._target_resources())
            page.Resources = shared
            page.Contents = pdf.make_stream(b"/Ink cs 1 scn 0 0 10 10 re f\n")
            pdf.trailer.Info = shared
            pdf.save(source)
        sentinel = b"preserve shared owner output"
        output.write_bytes(sentinel)

        with self.assertRaisesRegex(
            UnsupportedSpotUseError,
            "target resource container has a non-content owner",
        ):
            remove_spot(source, output, "DemoSpot", force=True)

        self.assertEqual(output.read_bytes(), sentinel)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def test_unrelated_uninvoked_form_bytes_are_not_parsed_or_changed(self) -> None:
        source = self.root / "unrelated-uninvoked-form.pdf"
        output = self.root / "unrelated-uninvoked-form-output.pdf"
        unrelated_bytes = b"Q\n"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            unused = self._form(pdf, unrelated_bytes)
            unused.Resources = pikepdf.Dictionary()
            resources = self._target_resources()
            resources.XObject = pikepdf.Dictionary(Unused=unused)
            page.Resources = resources
            page.Contents = pdf.make_stream(b"/Ink cs 1 scn 0 0 10 10 re f\n")
            pdf.save(source)

        stats = remove_spot(source, output, "DemoSpot")

        self.assertEqual(stats.forms_changed, 0)
        with pikepdf.open(output) as cleaned:
            self.assertEqual(
                cleaned.pages[0].Resources.XObject.Unused.read_bytes(),
                unrelated_bytes,
            )

    def test_default_color_space_override_is_never_deleted(self) -> None:
        source = self.root / "default-cmyk-override.pdf"
        output = self.root / "default-cmyk-override-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(DefaultCMYK=separation())
            )
            page.Contents = pdf.make_stream(b"0 0 0 1 k 0 0 10 10 re f\n")
            pdf.save(source)
        sentinel = b"preserve default override output"
        output.write_bytes(sentinel)

        with self.assertRaisesRegex(
            UnsupportedSpotUseError,
            "target is a default color-space override",
        ):
            remove_spot(source, output, "DemoSpot", force=True)

        self.assertEqual(output.read_bytes(), sentinel)
        with pikepdf.open(source) as unchanged:
            self.assertIn(pikepdf.Name.DefaultCMYK, unchanged.pages[0].Resources.ColorSpace)

    def test_rewritten_form_shared_as_attachment_fails_atomically(self) -> None:
        source = self.root / "form-attachment.pdf"
        output = self.root / "form-attachment-output.pdf"
        form_bytes = b"/Ink cs 1 scn 0 0 10 10 re f\n% ATTACHMENT_SENTINEL\n"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            form = self._form(pdf, form_bytes)
            page.Resources = self._target_resources()
            page.Resources.XObject = pikepdf.Dictionary(Paint=form)
            page.Contents = pdf.make_stream(b"/Paint Do\n")
            filespec = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Filespec,
                    F=pikepdf.String("form-content.bin"),
                    EF=pikepdf.Dictionary(F=form),
                )
            )
            pdf.Root.Names = pikepdf.Dictionary(
                EmbeddedFiles=pikepdf.Dictionary(
                    Names=pikepdf.Array([pikepdf.String("form-content.bin"), filespec])
                )
            )
            pdf.save(source)
        sentinel = b"preserve form attachment output"
        output.write_bytes(sentinel)

        with self.assertRaisesRegex(UnsupportedSpotUseError, "embedded-file data"):
            remove_spot(source, output, "DemoSpot", force=True)

        self.assertEqual(output.read_bytes(), sentinel)
        with pikepdf.open(source) as unchanged:
            attachment = unchanged.Root.Names.EmbeddedFiles.Names[1].EF.F
            self.assertEqual(attachment.read_bytes(), form_bytes)

    def test_target_color_space_with_trailer_owner_fails_atomically(self) -> None:
        for target_owner in ("shared", "separate", "devicen"):
            for mode in ("exact", "all"):
                with self.subTest(target_owner=target_owner, mode=mode):
                    source = self.root / f"trailer-{target_owner}-{mode}.pdf"
                    output = self.root / f"trailer-{target_owner}-{mode}-output.pdf"
                    with pikepdf.Pdf.new() as pdf:
                        page = pdf.add_blank_page()
                        page_target = pdf.make_indirect(separation())
                        if target_owner == "shared":
                            private_target = page_target
                        elif target_owner == "devicen":
                            private_target = pikepdf.Array(
                                [
                                    pikepdf.Name.DeviceN,
                                    pikepdf.Array([pikepdf.Name.DemoSpot]),
                                    pikepdf.Name.DeviceCMYK,
                                    separation()[3],
                                ]
                            )
                        else:
                            private_target = separation()
                        page.Resources = pikepdf.Dictionary(
                            ColorSpace=pikepdf.Dictionary(Ink=page_target)
                        )
                        page.Contents = pdf.make_stream(b"/Ink cs 1 scn 0 0 10 10 re f\n")
                        pdf.trailer.Info = pdf.make_indirect(
                            pikepdf.Dictionary(PrivateSpot=private_target)
                        )
                        pdf.save(source)
                    source_bytes = source.read_bytes()
                    sentinel = f"preserve {target_owner} {mode} output".encode()
                    output.write_bytes(sentinel)

                    with self.assertRaisesRegex(
                        UnsupportedSpotUseError,
                        r"trailer /Info /PrivateSpot: target (?:Separation|DeviceN) "
                        r"is not exclusively",
                    ):
                        if mode == "exact":
                            remove_spot(source, output, "DemoSpot", force=True)
                        else:
                            remove_all_spots(source, output, force=True)

                    self.assertEqual(source.read_bytes(), source_bytes)
                    self.assertEqual(output.read_bytes(), sentinel)
                    self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])
                    with pikepdf.open(source) as unchanged:
                        info_target = unchanged.trailer.Info.PrivateSpot
                        if target_owner == "devicen":
                            self.assertEqual(str(info_target[1][0]), "/DemoSpot")
                        else:
                            self.assertEqual(str(info_target[1]), "/DemoSpot")

    @staticmethod
    def _target_resources() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation()))

    @staticmethod
    def _form(pdf: pikepdf.Pdf, content: bytes) -> pikepdf.Stream:
        form = pdf.make_stream(content)
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 10, 10])
        return form


if __name__ == "__main__":
    unittest.main()
