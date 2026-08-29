from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.document import inspect_pdf
from spotpdf.model import SpotPdfError
from spotpdf.rename import rename_spot


class RenameDeviceNStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_repeated_none_components_are_supported(self) -> None:
        source = self._make_pdf(
            ("Old", "None", "None"),
            attributes=None,
            filename="repeated-none.pdf",
        )
        output = self.root / "repeated-none-output.pdf"

        rename_spot(source, output, "Old", "New")

        with pikepdf.open(output) as pdf:
            names = pdf.pages[0].Resources.ColorSpace.Mixed[1]
            self.assertEqual([str(item) for item in names], ["/New", "/None", "/None"])

    def test_all_component_is_rejected_atomically(self) -> None:
        source = self._make_pdf(("Old", "All"), attributes=None, filename="all.pdf")

        self._assert_rejected(source)

    def test_non_cmyk_process_components_must_be_sequential_and_ordered(self) -> None:
        attributes = pikepdf.Dictionary(
            Subtype=pikepdf.Name.NChannel,
            Process=pikepdf.Dictionary(
                ColorSpace=pikepdf.Name.DeviceRGB,
                Components=pikepdf.Array([pikepdf.Name.Red, pikepdf.Name.Green, pikepdf.Name.Blue]),
            ),
            Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
            MixingHints=pikepdf.Dictionary(
                DotGain=pikepdf.Dictionary(Old=self._stitching_function())
            ),
        )
        source = self._make_pdf(
            ("Blue", "Old", "Red", "Green"),
            attributes=attributes,
            filename="unordered-rgb.pdf",
        )

        self._assert_rejected(source)

    def test_nchannel_requires_each_spot_component_definition(self) -> None:
        attributes = pikepdf.Dictionary(
            Subtype=pikepdf.Name.NChannel,
            Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
        )
        source = self._make_pdf(
            ("Old", "Other"),
            attributes=attributes,
            filename="missing-colorant.pdf",
        )

        self._assert_rejected(source)

    def test_none_component_is_rejected_for_nchannel(self) -> None:
        attributes = pikepdf.Dictionary(
            Subtype=pikepdf.Name.NChannel,
            Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
        )
        source = self._make_pdf(
            ("Old", "None"),
            attributes=attributes,
            filename="nchannel-none.pdf",
        )

        self._assert_rejected(source)

    def test_valid_rgb_nchannel_is_renamed(self) -> None:
        attributes = pikepdf.Dictionary(
            Subtype=pikepdf.Name.NChannel,
            Process=pikepdf.Dictionary(
                ColorSpace=pikepdf.Name.DeviceRGB,
                Components=pikepdf.Array([pikepdf.Name.Red, pikepdf.Name.Green, pikepdf.Name.Blue]),
            ),
            Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
        )
        source = self._make_pdf(
            ("Red", "Green", "Blue", "Old"),
            attributes=attributes,
            filename="valid-rgb.pdf",
        )
        output = self.root / "valid-rgb-output.pdf"

        rename_spot(source, output, "Old", "New")

        self.assertNotIn("Old", inspect_pdf(output).colorants)
        self.assertIn("New", inspect_pdf(output).spots)

    def test_colorants_key_reordering_with_shared_preview_object_is_supported(self) -> None:
        source = self.root / "shared-colorant-preview.pdf"
        output = self.root / "shared-colorant-preview-output.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            shared_tint = pdf.make_indirect(self._separation("Shared")[3])

            def nested(name: str) -> pikepdf.Array:
                return pikepdf.Array(
                    [
                        pikepdf.Name.Separation,
                        pikepdf.Name(f"/{name}"),
                        pikepdf.Name.DeviceCMYK,
                        shared_tint,
                    ]
                )

            mixed = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.Other, pikepdf.Name.Zulu]),
                    pikepdf.Name.DeviceCMYK,
                    self._calculator_function(pdf, inputs=2),
                    pikepdf.Dictionary(
                        Subtype=pikepdf.Name.NChannel,
                        Colorants=pikepdf.Dictionary(
                            Other=nested("Other"),
                            Zulu=nested("Zulu"),
                        ),
                    ),
                ]
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Ink=self._separation("Zulu"),
                    Mixed=mixed,
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(source, min_version="1.6")

        rename_spot(source, output, "Zulu", "Alpha")

        report = inspect_pdf(output)
        self.assertNotIn("Zulu", report.colorants)
        self.assertIn("Alpha", report.spots)

    def test_string_subtype_is_rejected(self) -> None:
        attributes = pikepdf.Dictionary(
            Subtype=pikepdf.String("NChannel"),
            Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
        )
        source = self._make_pdf(
            ("Old",),
            attributes=attributes,
            filename="string-subtype.pdf",
        )

        self._assert_rejected(source)

    def _make_pdf(
        self,
        names: tuple[str, ...],
        *,
        attributes: pikepdf.Dictionary | None,
        filename: str,
    ) -> Path:
        path = self.root / filename
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            mixed_items: list[object] = [
                pikepdf.Name.DeviceN,
                pikepdf.Array([pikepdf.Name(f"/{name}") for name in names]),
                pikepdf.Name.DeviceCMYK,
                self._calculator_function(pdf, inputs=len(names)),
            ]
            if attributes is not None:
                mixed_items.append(attributes)
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Ink=self._separation("Old"),
                    Mixed=pikepdf.Array(mixed_items),
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(path, min_version="1.6")
        return path

    def _assert_rejected(self, source: Path) -> None:
        output = self.root / f"rejected-{len(list(self.root.glob('rejected-*.pdf')))}.pdf"
        output.write_bytes(b"keep-existing")
        with self.assertRaises(SpotPdfError):
            rename_spot(source, output, "Old", "New", force=True)
        self.assertEqual(output.read_bytes(), b"keep-existing")

    @staticmethod
    def _separation(name: str) -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name(f"/{name}"),
                pikepdf.Name.DeviceCMYK,
                pikepdf.Dictionary(
                    FunctionType=2,
                    Domain=pikepdf.Array([0, 1]),
                    C0=pikepdf.Array([0, 0, 0, 0]),
                    C1=pikepdf.Array([0, 0.8, 1, 0]),
                    N=1,
                ),
            ]
        )

    @staticmethod
    def _calculator_function(pdf: pikepdf.Pdf, *, inputs: int) -> pikepdf.Stream:
        function = pdf.make_stream(("{ " + "pop " * inputs + "0 0 0 0 }").encode())
        function.FunctionType = 4
        function.Domain = pikepdf.Array([item for _ in range(inputs) for item in (0, 1)])
        function.Range = pikepdf.Array([0, 1, 0, 1, 0, 1, 0, 1])
        return function

    @staticmethod
    def _stitching_function() -> pikepdf.Dictionary:
        subfunctions = pikepdf.Array(
            [
                pikepdf.Dictionary(
                    FunctionType=2,
                    Domain=pikepdf.Array([0, 1]),
                    C0=pikepdf.Array([0]),
                    C1=pikepdf.Array([0.5]),
                    N=1,
                ),
                pikepdf.Dictionary(
                    FunctionType=2,
                    Domain=pikepdf.Array([0, 1]),
                    C0=pikepdf.Array([0.5]),
                    C1=pikepdf.Array([1]),
                    N=1,
                ),
            ]
        )
        return pikepdf.Dictionary(
            FunctionType=3,
            Domain=pikepdf.Array([0, 1]),
            Functions=subfunctions,
            Bounds=pikepdf.Array([0.5]),
            Encode=pikepdf.Array([0, 1, 0, 1]),
            Range=pikepdf.Array([0, 1]),
        )


if __name__ == "__main__":
    unittest.main()
