from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pikepdf

import spotpdf.convert as convert_module
from spotpdf.convert import convert_spot_to_cmyk
from spotpdf.convert_plan import ConversionPlan
from spotpdf.model import SpotPdfError
from tests.conversion_fixtures import make_basic_conversion_pdf, separation


class ConvertVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_in_memory_unplanned_resource_change_is_rejected(self) -> None:
        source = make_basic_conversion_pdf(self.root / "in-memory.pdf")
        original_apply = ConversionPlan.apply

        def apply_with_extra_resource(plan: ConversionPlan) -> None:
            original_apply(plan)
            plan.resource_removals[0].color_spaces.Unplanned = pikepdf.Name.DeviceRGB

        with mock.patch.object(ConversionPlan, "apply", apply_with_extra_resource):
            self._assert_forced_failure(source, "in-memory-output.pdf")

    def test_post_save_content_metadata_and_stale_target_changes_are_rejected(self) -> None:
        source = make_basic_conversion_pdf(self.root / "post-save.pdf")
        original_save = convert_module.save_pdf

        def tamper_content(pdf: pikepdf.Pdf, path: Path) -> None:
            original_save(pdf, path)
            with pikepdf.open(path, allow_overwriting_input=True) as saved:
                saved.pages[0].Contents.write(b"0 0 10 10 re f")
                saved.save(path)

        def tamper_metadata(pdf: pikepdf.Pdf, path: Path) -> None:
            original_save(pdf, path)
            with pikepdf.open(path, allow_overwriting_input=True) as saved:
                saved.Root.Unplanned = pikepdf.String("changed")
                saved.save(path)

        def restore_target(pdf: pikepdf.Pdf, path: Path) -> None:
            original_save(pdf, path)
            with pikepdf.open(path, allow_overwriting_input=True) as saved:
                saved.pages[0].Resources.ColorSpace.Ink = separation()
                saved.save(path)

        for index, tamper in enumerate((tamper_content, tamper_metadata, restore_target)):
            with (
                self.subTest(tamper=index),
                mock.patch(
                    "spotpdf.convert.save_pdf",
                    side_effect=tamper,
                ),
            ):
                self._assert_forced_failure(source, f"post-save-output-{index}.pdf")

    def _assert_forced_failure(self, source: Path, filename: str) -> None:
        output = self.root / filename
        original = b"keep verified output"
        output.write_bytes(original)
        with self.assertRaises(SpotPdfError):
            convert_spot_to_cmyk(
                source,
                output,
                "DemoSpot",
                (0, 80, 100, 0),
                force=True,
            )
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])


if __name__ == "__main__":
    unittest.main()
