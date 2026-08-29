from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

import pikepdf

from spotpdf.model import SpotPdfError
from spotpdf.rename import rename_spot


class RenameMalformedNameFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_nested_target_names_in_devicen_fields_are_rejected(self) -> None:
        attributes = (
            pikepdf.Dictionary(
                Process=pikepdf.Dictionary(
                    ColorSpace=pikepdf.Name.DeviceGray,
                    Components=pikepdf.Dictionary(X=pikepdf.Name.Old),
                )
            ),
            pikepdf.Dictionary(Colorants=pikepdf.Dictionary(Other=pikepdf.Name.Old)),
            pikepdf.Dictionary(
                MixingHints=pikepdf.Dictionary(
                    PrintingOrder=pikepdf.Dictionary(X=pikepdf.String("Old"))
                )
            ),
            pikepdf.Dictionary(
                MixingHints=pikepdf.Dictionary(Solidities=pikepdf.Array([pikepdf.Name.Old]))
            ),
            pikepdf.Dictionary(
                MixingHints=pikepdf.Dictionary(DotGain=pikepdf.Array([pikepdf.String("Old")]))
            ),
        )
        for index, value in enumerate(attributes):
            with self.subTest(case=index):
                source = self._make_pdf(
                    lambda pdf, page, value=value: self._add_devicen(
                        pdf,
                        page,
                        ("Other",),
                        value,
                    )
                )
                self._assert_rejected(source)

    def test_scalar_and_nested_inks_targets_are_rejected(self) -> None:
        values = (
            lambda: pikepdf.String("Old"),
            lambda: pikepdf.Dictionary(X=pikepdf.Name.Old),
        )
        for context in ("opi", "image"):
            for index, build_value in enumerate(values):
                with self.subTest(context=context, case=index):
                    source = self._make_pdf(
                        lambda pdf, page, context=context, build_value=build_value: (
                            self._add_ink_reference(
                                pdf,
                                page,
                                context,
                                build_value(),
                            )
                        )
                    )
                    self._assert_rejected(source)

    def test_malformed_separation_info_target_fields_are_rejected(self) -> None:
        values = (
            pikepdf.Dictionary(DeviceColorant=pikepdf.Array([pikepdf.Name.Old])),
            pikepdf.Dictionary(
                DeviceColorant=pikepdf.Name.Other,
                ColorSpace=pikepdf.Dictionary(X=pikepdf.Name.Old),
            ),
        )
        for index, info in enumerate(values):
            with self.subTest(case=index):
                source = self._make_pdf(
                    lambda _pdf, page, info=info: setattr(page, "SeparationInfo", info)
                )
                self._assert_rejected(source)

    def test_malformed_printer_mark_normal_colorants_are_rejected(self) -> None:
        values = (
            lambda: pikepdf.Name.Old,
            lambda: pikepdf.Array([pikepdf.String("Old")]),
            lambda: pikepdf.Dictionary(Other=pikepdf.Name.Old),
        )
        for index, build_value in enumerate(values):
            with self.subTest(case=index):
                source = self._make_pdf(
                    lambda pdf, page, build_value=build_value: self._add_printer_mark(
                        pdf,
                        page,
                        build_value(),
                    )
                )
                self._assert_rejected(source)

    def test_sampled_dot_gain_function_requires_bits_per_sample(self) -> None:
        def add_invalid_dot_gain(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
            sampled = pdf.make_stream(b"\x00\xff")
            sampled.FunctionType = 0
            sampled.Domain = pikepdf.Array([0, 1])
            sampled.Range = pikepdf.Array([0, 1])
            sampled.Size = pikepdf.Array([2])
            attributes = pikepdf.Dictionary(
                Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                MixingHints=pikepdf.Dictionary(DotGain=pikepdf.Dictionary(Old=sampled)),
            )
            self._add_devicen(pdf, page, ("Old",), attributes)

        source = self._make_pdf(add_invalid_dot_gain)

        self._assert_rejected(source)

    def _make_pdf(
        self,
        add_structure: Callable[[pikepdf.Pdf, pikepdf.Page], None],
    ) -> Path:
        source = self.root / f"malformed-{len(list(self.root.glob('malformed-*.pdf')))}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Ink=self._separation("Old"))
            )
            page.Contents = pdf.make_stream(b"")
            add_structure(pdf, page)
            pdf.save(source, min_version="1.6")
        return source

    def _assert_rejected(self, source: Path) -> None:
        output = self.root / f"output-{len(list(self.root.glob('output-*.pdf')))}.pdf"
        output.write_bytes(b"keep-existing")

        with self.assertRaises(SpotPdfError):
            rename_spot(source, output, "Old", "New", force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def _add_devicen(
        self,
        pdf: pikepdf.Pdf,
        page: pikepdf.Page,
        names: tuple[str, ...],
        attributes: pikepdf.Dictionary,
    ) -> None:
        page.Resources.ColorSpace.Mixed = pikepdf.Array(
            [
                pikepdf.Name.DeviceN,
                pikepdf.Array([pikepdf.Name(f"/{name}") for name in names]),
                pikepdf.Name.DeviceCMYK,
                self._calculator_function(pdf, inputs=len(names)),
                attributes,
            ]
        )

    def _add_ink_reference(
        self,
        pdf: pikepdf.Pdf,
        page: pikepdf.Page,
        context: str,
        inks: pikepdf.Object,
    ) -> None:
        image = self._image(pdf)
        if context == "image":
            image.Inks = inks
        else:
            version = pikepdf.Dictionary(Inks=inks)
            image.OPI = pikepdf.Dictionary()
            image.OPI[pikepdf.Name("/2.0")] = version
        page.Resources.XObject = pikepdf.Dictionary(Image=image)

    def _add_printer_mark(
        self,
        pdf: pikepdf.Pdf,
        page: pikepdf.Page,
        colorants: pikepdf.Object,
    ) -> None:
        normal = pdf.make_stream(b"")
        normal.Type = pikepdf.Name.XObject
        normal.Subtype = pikepdf.Name.Form
        normal.BBox = pikepdf.Array([0, 0, 10, 10])
        normal.Resources = pikepdf.Dictionary()
        normal.Colorants = colorants
        page.Annots = pikepdf.Array(
            [
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Annot,
                    Subtype=pikepdf.Name.PrinterMark,
                    Rect=pikepdf.Array([0, 0, 10, 10]),
                    AP=pikepdf.Dictionary(N=normal),
                )
            ]
        )

    @staticmethod
    def _image(pdf: pikepdf.Pdf) -> pikepdf.Stream:
        image = pdf.make_stream(b"\x00\x00\x00")
        image.Type = pikepdf.Name.XObject
        image.Subtype = pikepdf.Name.Image
        image.Width = 1
        image.Height = 1
        image.ColorSpace = pikepdf.Name.DeviceRGB
        image.BitsPerComponent = 8
        return image

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


if __name__ == "__main__":
    unittest.main()
