from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pikepdf

import spotpdf.alternate as alternate_module
from spotpdf.alternate import set_alternate_cmyk
from spotpdf.alternate_plan import AlternatePlan
from spotpdf.model import SpotPdfError


class AlternateSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_shared_definition_and_old_function_are_not_mutated_in_place(self) -> None:
        source = self.root / "shared-input.pdf"
        output = self.root / "shared-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            old_function = pdf.make_indirect(self._function((0.9, 0.2, 0.1)))
            target = pdf.make_indirect(self._separation("Target", old_function))
            unrelated = self._separation("Other", old_function)
            for index in range(2):
                page = pdf.add_blank_page(page_size=(100, 100))
                spaces = pikepdf.Dictionary()
                spaces[pikepdf.Name(f"/Target{index}")] = target
                if index == 0:
                    spaces.Other = unrelated
                page.Resources = pikepdf.Dictionary(ColorSpace=spaces)
                page.Contents = pdf.make_stream(b"")
            pdf.save(source)

        result = set_alternate_cmyk(source, output, "Target", (0, 80, 100, 0))

        self.assertEqual(result.definitions_changed, 1)
        with pikepdf.open(output) as pdf:
            first = pdf.pages[0].Resources.ColorSpace.Target0
            second = pdf.pages[1].Resources.ColorSpace.Target1
            unrelated = pdf.pages[0].Resources.ColorSpace.Other
            self.assertEqual(first.objgen, second.objgen)
            self.assertEqual(first[2], pikepdf.Name.DeviceCMYK)
            self.assertEqual(tuple(float(value) for value in first[3].C1), (0, 0.8, 1, 0))
            self.assertEqual(tuple(float(value) for value in unrelated[3].C1), (0.9, 0.2, 0.1))

    def test_unrelated_devicen_is_allowed_but_nested_target_is_rejected(self) -> None:
        allowed = self._basic_pdf("unrelated-devicen.pdf", include_unrelated_devicen=True)
        output = self.root / "unrelated-devicen-output.pdf"

        set_alternate_cmyk(allowed, output, "Target", (0, 80, 100, 0))

        with pikepdf.open(output) as pdf:
            self.assertEqual(pdf.pages[0].Resources.ColorSpace.Target[2], pikepdf.Name.DeviceCMYK)
            self.assertEqual(
                pdf.pages[0].Resources.ColorSpace.Mixed[1][0],
                pikepdf.Name.Other,
            )

        blocked = self._basic_pdf("nested-target-devicen.pdf")
        with pikepdf.open(blocked, allow_overwriting_input=True) as pdf:
            colorants = pikepdf.Dictionary(Target=self._separation("Target"))
            pdf.pages[0].Resources.ColorSpace.Mixed = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.Other]),
                    pikepdf.Name.DeviceRGB,
                    self._function((0.1, 0.2, 0.3)),
                    pikepdf.Dictionary(Colorants=colorants),
                ]
            )
            pdf.save(blocked)

        self._assert_forced_failure(blocked)

    def test_malformed_target_name_fields_are_rejected_fail_closed(self) -> None:
        malformed_cases = (
            (
                pikepdf.Name.DeviceN,
                pikepdf.Dictionary(X=pikepdf.Name.Target),
            ),
            (
                pikepdf.Name.DeviceN,
                pikepdf.Array([pikepdf.Dictionary(X=pikepdf.String("Target"))]),
            ),
            (
                pikepdf.Name.Separation,
                pikepdf.Dictionary(X=pikepdf.Name.Target),
            ),
            (
                pikepdf.Name.Separation,
                pikepdf.Array([pikepdf.String("Target")]),
            ),
        )
        for index, (family, components) in enumerate(malformed_cases):
            with self.subTest(family=str(family), components=str(components)):
                label = str(family).removeprefix("/").lower()
                source = self._basic_pdf(f"malformed-{label}-{index}.pdf")
                with pikepdf.open(source, allow_overwriting_input=True) as pdf:
                    malformed = pikepdf.Array(
                        [
                            family,
                            components,
                            pikepdf.Name.DeviceGray,
                            self._function((0.4,)),
                        ]
                    )
                    pdf.pages[0].Resources.ColorSpace.Malformed = malformed
                    pdf.save(source)

                self._assert_forced_failure(source)

    def test_malformed_existing_preview_definitions_are_not_repaired(self) -> None:
        cases = (
            (pikepdf.Name.Bogus, pikepdf.Dictionary()),
            (
                pikepdf.Name.DeviceRGB,
                self._function((0.2, 0.4)),
            ),
            (
                pikepdf.Name.DeviceRGB,
                pikepdf.Dictionary(
                    FunctionType=2,
                    Domain=pikepdf.Array([1, 0]),
                    C0=pikepdf.Array([0, 0, 0]),
                    C1=pikepdf.Array([0.1, 0.2, 0.3]),
                    N=1,
                ),
            ),
        )
        for index, (alternate, function) in enumerate(cases):
            with self.subTest(index=index):
                source = self._basic_pdf(f"malformed-preview-{index}.pdf")
                with pikepdf.open(source, allow_overwriting_input=True) as pdf:
                    target = pdf.pages[0].Resources.ColorSpace.Target
                    target[2] = alternate
                    target[3] = function
                    pdf.save(source)

                self._assert_forced_failure(source)

    def test_inline_target_definition_is_rejected_but_resource_alias_is_supported(self) -> None:
        direct = self._basic_pdf("inline-direct.pdf")
        direct_space = (
            b"[/Separation /Target /DeviceRGB << /FunctionType 2 /Domain [0 1] "
            b"/C0 [0 0 0] /C1 [0 1 0] /N 1 >>]"
        )
        with pikepdf.open(direct, allow_overwriting_input=True) as pdf:
            pdf.pages[0].Contents.write(self._inline_image_content(direct_space))
            pdf.save(direct)
        self._assert_forced_failure(direct)

        devicen = self._basic_pdf("inline-devicen.pdf")
        devicen_space = (
            b"[/DeviceN [/Target] /DeviceRGB << /FunctionType 2 /Domain [0 1] "
            b"/C0 [0 0 0] /C1 [0 1 0] /N 1 >>]"
        )
        with pikepdf.open(devicen, allow_overwriting_input=True) as pdf:
            pdf.pages[0].Contents.write(self._inline_image_content(devicen_space))
            pdf.save(devicen)
        self._assert_forced_failure(devicen)

        alias = self._basic_pdf("inline-alias.pdf")
        with pikepdf.open(alias, allow_overwriting_input=True) as pdf:
            pdf.pages[0].Contents.write(self._inline_image_content(b"/Target"))
            pdf.save(alias)
        alias_before = alias.read_bytes()
        alias_output = self.root / "inline-alias-output.pdf"
        set_alternate_cmyk(alias, alias_output, "Target", (0, 80, 100, 0))
        self.assertEqual(alias.read_bytes(), alias_before)
        with pikepdf.open(alias_output) as pdf:
            self.assertEqual(pdf.pages[0].Resources.ColorSpace.Target[2], pikepdf.Name.DeviceCMYK)

    def test_in_memory_and_post_save_unplanned_changes_are_rejected(self) -> None:
        source = self._basic_pdf("tampering.pdf")
        original_apply = AlternatePlan.apply

        def apply_with_unrelated_change(plan: AlternatePlan) -> None:
            original_apply(plan)
            other = plan.pdf.pages[0].Resources.ColorSpace.Other
            other[3].C1 = pikepdf.Array([0.9])

        with mock.patch.object(AlternatePlan, "apply", apply_with_unrelated_change):
            self._assert_forced_failure(source)

        original_save = alternate_module.save_pdf

        def saved_with_target_tamper(pdf: pikepdf.Pdf, path: Path) -> None:
            original_save(pdf, path)
            with pikepdf.open(path, allow_overwriting_input=True) as saved:
                saved.pages[0].Resources.ColorSpace.Target[3].C1 = pikepdf.Array([1, 0, 0, 0])
                saved.save(path)

        with mock.patch(
            "spotpdf.alternate.save_pdf",
            side_effect=saved_with_target_tamper,
        ):
            self._assert_forced_failure(source)

        def saved_with_content_tamper(pdf: pikepdf.Pdf, path: Path) -> None:
            original_save(pdf, path)
            with pikepdf.open(path, allow_overwriting_input=True) as saved:
                saved.pages[0].Contents.write(b"0 0 10 10 re f\n")
                saved.save(path)

        with mock.patch(
            "spotpdf.alternate.save_pdf",
            side_effect=saved_with_content_tamper,
        ):
            self._assert_forced_failure(source)

    def test_output_alias_guards_and_file_mode_apply_to_set_alternate(self) -> None:
        source = self._basic_pdf("output-guards.pdf")
        source.chmod(0o640)
        output = self.root / "mode-output.pdf"

        set_alternate_cmyk(source, output, "Target", (0, 80, 100, 0))

        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o640)
        hard_link = self.root / "hard-link.pdf"
        try:
            os.link(source, hard_link)
        except OSError as error:
            self.skipTest(f"hard links are unavailable: {error}")
        with self.assertRaises(SpotPdfError):
            set_alternate_cmyk(source, hard_link, "Target", (0, 80, 100, 0), force=True)

        link_target = self.root / "link-target.txt"
        link_target.write_bytes(b"keep-target")
        symlink = self.root / "output-link.pdf"
        try:
            symlink.symlink_to(link_target)
        except OSError as error:
            self.skipTest(f"symbolic links are unavailable: {error}")
        with self.assertRaises(SpotPdfError):
            set_alternate_cmyk(source, symlink, "Target", (0, 80, 100, 0), force=True)
        self.assertEqual(link_target.read_bytes(), b"keep-target")

    def _assert_forced_failure(self, source: Path) -> None:
        output = self.root / f"protected-{len(list(self.root.glob('protected-*.pdf')))}.pdf"
        output.write_bytes(b"keep-existing")
        with self.assertRaises(SpotPdfError):
            set_alternate_cmyk(source, output, "Target", (0, 80, 100, 0), force=True)
        self.assertEqual(output.read_bytes(), b"keep-existing")
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def _basic_pdf(self, name: str, *, include_unrelated_devicen: bool = False) -> Path:
        path = self.root / name
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            spaces = pikepdf.Dictionary(
                Target=self._separation("Target"),
                Other=self._separation("Other", self._function((0.4,))),
            )
            if include_unrelated_devicen:
                spaces.Mixed = pikepdf.Array(
                    [
                        pikepdf.Name.DeviceN,
                        pikepdf.Array([pikepdf.Name.Other]),
                        pikepdf.Name.DeviceRGB,
                        self._function((0.1, 0.2, 0.3)),
                    ]
                )
            page.Resources = pikepdf.Dictionary(ColorSpace=spaces)
            page.Contents = pdf.make_stream(b"/Target cs 0.5 scn 0 0 20 20 re f\n")
            pdf.save(path, min_version="1.6" if include_unrelated_devicen else "1.3")
        return path

    @classmethod
    def _separation(
        cls,
        name: str,
        function: pikepdf.Object | None = None,
    ) -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name(f"/{name}"),
                pikepdf.Name.DeviceRGB if name == "Target" else pikepdf.Name.DeviceGray,
                function or cls._function((0.9, 0.2, 0.1) if name == "Target" else (0.4,)),
            ]
        )

    @staticmethod
    def _function(c1: tuple[float, ...]) -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0] * len(c1)),
            C1=pikepdf.Array(c1),
            N=1,
        )

    @staticmethod
    def _inline_image_content(color_space: bytes) -> bytes:
        return b"BI /W 1 /H 1 /BPC 8 /CS " + color_space + b" ID \x00\x00\x00 EI\n"


if __name__ == "__main__":
    unittest.main()
