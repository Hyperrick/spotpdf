from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

import pikepdf

from spotpdf.model import SpotPdfError
from spotpdf.rename import rename_spot


class RenameUnsupportedPrepressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_trapnet_target_is_rejected_without_replacing_forced_output(self) -> None:
        for as_string in (False, True):
            with self.subTest(as_string=as_string):
                source = self._make_pdf(
                    lambda pdf, page, as_string=as_string: self._add_trapnet(
                        pdf,
                        page,
                        as_string=as_string,
                    )
                )

                self._assert_failure_preserves_output(source)

    def test_type_five_halftone_target_is_rejected_atomically(self) -> None:
        source = self._make_pdf(self._add_type_five_halftone)

        self._assert_failure_preserves_output(source)

    def test_opi_target_is_rejected_atomically(self) -> None:
        source = self._make_pdf(self._add_opi_dictionary)

        self._assert_failure_preserves_output(source)

    def test_printer_mark_rollover_and_down_appearances_are_rejected(self) -> None:
        for appearance in (pikepdf.Name.R, pikepdf.Name.D):
            with self.subTest(appearance=str(appearance)):
                source = self._make_pdf(
                    lambda pdf, page, appearance=appearance: self._add_printer_mark_appearance(
                        pdf,
                        page,
                        appearance,
                    )
                )

                self._assert_failure_preserves_output(source)

    def test_mismatched_colorants_key_and_inner_separation_are_rejected(self) -> None:
        for key_name, inner_name in (("Old", "Other"), ("Other", "Old")):
            with self.subTest(key=key_name, inner=inner_name):
                source = self._make_pdf(
                    lambda pdf, page, key_name=key_name, inner_name=inner_name: (
                        self._add_malformed_nchannel(
                            pdf,
                            page,
                            key_name=key_name,
                            inner=self._separation(inner_name),
                        )
                    )
                )

                self._assert_failure_preserves_output(source)

    def test_non_separation_individual_colorant_is_rejected(self) -> None:
        source = self._make_pdf(
            lambda pdf, page: self._add_malformed_nchannel(
                pdf,
                page,
                key_name="Old",
                inner=pikepdf.Name.DeviceCMYK,
            )
        )

        self._assert_failure_preserves_output(source)

    def test_source_in_process_components_is_rejected(self) -> None:
        source = self._make_pdf(self._add_process_component)

        self._assert_failure_preserves_output(source)

    def test_malformed_target_name_fields_are_rejected(self) -> None:
        attributes = (
            pikepdf.Dictionary(
                Process=pikepdf.Dictionary(
                    ColorSpace=pikepdf.Name.DeviceGray,
                    Components=pikepdf.Name.Old,
                )
            ),
            pikepdf.Dictionary(Colorants=pikepdf.Name.Old),
            pikepdf.Dictionary(MixingHints=pikepdf.Dictionary(PrintingOrder=pikepdf.Name.Old)),
        )
        for index, value in enumerate(attributes):
            with self.subTest(case=index):
                source = self._make_pdf(
                    lambda pdf, page, value=value: self._add_target_attributes(
                        pdf,
                        page,
                        value,
                        component_names=("Other",),
                    )
                )
                self._assert_failure_preserves_output(source)

    def test_malformed_separation_name_string_is_rejected(self) -> None:
        source = self._make_pdf(self._add_malformed_separation_name)

        self._assert_failure_preserves_output(source)

    def test_required_target_structure_fields_are_validated(self) -> None:
        malformed_attributes = (
            (
                pikepdf.Dictionary(
                    Subtype=pikepdf.Name.NChannel,
                    Process=pikepdf.Dictionary(
                        Components=pikepdf.Array(
                            [pikepdf.Name.Red, pikepdf.Name.Green, pikepdf.Name.Blue]
                        )
                    ),
                    Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                ),
                ("Red", "Green", "Blue", "Old"),
            ),
            (
                pikepdf.Dictionary(
                    Subtype=pikepdf.Name.NChannel,
                    Process=pikepdf.Dictionary(
                        ColorSpace=pikepdf.Name.DeviceRGB,
                        Components=pikepdf.Array([pikepdf.Name.Red]),
                    ),
                    Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                ),
                ("Red", "Old"),
            ),
            (
                pikepdf.Dictionary(
                    Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                    MixingHints=pikepdf.Dictionary(Solidities=pikepdf.Dictionary(Old=0.8)),
                ),
                ("Old",),
            ),
            (
                pikepdf.Dictionary(
                    Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                    MixingHints=pikepdf.Dictionary(DotGain=pikepdf.Dictionary(Old=42)),
                ),
                ("Old",),
            ),
            (
                pikepdf.Dictionary(
                    Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                    MixingHints=pikepdf.Dictionary(
                        Solidities=pikepdf.Dictionary(Old=True),
                        PrintingOrder=pikepdf.Array([pikepdf.Name.Old]),
                    ),
                ),
                ("Old",),
            ),
        )
        for index, (attributes, components) in enumerate(malformed_attributes):
            with self.subTest(case=index):
                source = self._make_pdf(
                    lambda pdf, page, attributes=attributes, components=components: (
                        self._add_target_attributes(
                            pdf,
                            page,
                            attributes,
                            component_names=components,
                        )
                    )
                )
                self._assert_failure_preserves_output(source)

        source = self._make_pdf(self._add_separation_info_without_pages)
        self._assert_failure_preserves_output(source)

    def _assert_failure_preserves_output(self, source: Path) -> None:
        output = self.root / f"forced-{len(list(self.root.glob('forced-*.pdf')))}.pdf"
        output.write_bytes(b"keep-existing")

        with self.assertRaises(SpotPdfError):
            rename_spot(source, output, "Old", "New", force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def _make_pdf(
        self,
        add_unsupported: Callable[[pikepdf.Pdf, pikepdf.Page], None],
    ) -> Path:
        path = self.root / f"unsupported-{len(list(self.root.glob('unsupported-*.pdf')))}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Ink=self._separation("Old"))
            )
            page.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 10 10 re f\n")
            add_unsupported(pdf, page)
            pdf.save(path, min_version="1.6")
        return path

    def _add_trapnet(
        self,
        pdf: pikepdf.Pdf,
        page: pikepdf.Page,
        *,
        as_string: bool = False,
    ) -> None:
        appearance = self._form(pdf)
        appearance.PCM = pikepdf.Name.DeviceCMYK
        target = pikepdf.String("Old") if as_string else pikepdf.Name.Old
        appearance.SeparationColorNames = pikepdf.Array([target])
        annotation = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.TrapNet,
            F=68,
            Rect=pikepdf.Array([0, 0, 10, 10]),
            AP=pikepdf.Dictionary(N=appearance),
            LastModified=pikepdf.String("D:20260830000000+02'00'"),
        )
        page.Annots = pikepdf.Array([annotation])

    def _add_type_five_halftone(self, _pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        component = pikepdf.Dictionary(
            HalftoneType=1,
            Frequency=60,
            Angle=45,
            SpotFunction=pikepdf.Name.Round,
        )
        halftone = pikepdf.Dictionary(HalftoneType=5, Default=component)
        halftone[pikepdf.Name.Old] = component
        page.Resources.ExtGState = pikepdf.Dictionary(
            GS=pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, HT=halftone)
        )

    def _add_opi_dictionary(self, pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        image = self._image(pdf)
        version = pikepdf.Dictionary(
            Type=pikepdf.Name.OPI,
            Version=2.0,
            F=pikepdf.String("synthetic-external-image.tif"),
            Size=pikepdf.Array([1, 1]),
            CropRect=pikepdf.Array([0, 0, 1, 1]),
            Inks=pikepdf.Array([pikepdf.Name.monochrome, pikepdf.String("Old"), 1.0]),
        )
        opi = pikepdf.Dictionary()
        opi[pikepdf.Name("/2.0")] = version
        image.OPI = opi
        page.Resources.XObject = pikepdf.Dictionary(OPIImage=image)

    def _add_printer_mark_appearance(
        self,
        pdf: pikepdf.Pdf,
        page: pikepdf.Page,
        appearance_key: pikepdf.Name,
    ) -> None:
        normal = self._form(pdf)
        target = self._form(pdf)
        target.Colorants = pikepdf.Dictionary(Old=self._separation("Old"))
        appearances = pikepdf.Dictionary(N=normal)
        appearances[appearance_key] = target
        annotation = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.PrinterMark,
            F=68,
            Rect=pikepdf.Array([0, 0, 10, 10]),
            AP=appearances,
        )
        page.Annots = pikepdf.Array([annotation])

    def _add_malformed_nchannel(
        self,
        pdf: pikepdf.Pdf,
        page: pikepdf.Page,
        *,
        key_name: str,
        inner: pikepdf.Object,
    ) -> None:
        colorants = pikepdf.Dictionary()
        colorants[pikepdf.Name(f"/{key_name}")] = inner
        devicen = pikepdf.Array(
            [
                pikepdf.Name.DeviceN,
                pikepdf.Array([pikepdf.Name.Old]),
                pikepdf.Name.DeviceCMYK,
                self._calculator_function(pdf),
                pikepdf.Dictionary(
                    Subtype=pikepdf.Name.NChannel,
                    Colorants=colorants,
                ),
            ]
        )
        page.Resources.ColorSpace[pikepdf.Name.Mixed] = devicen

    def _add_process_component(self, pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        devicen = pikepdf.Array(
            [
                pikepdf.Name.DeviceN,
                pikepdf.Array([pikepdf.Name.Old]),
                pikepdf.Name.DeviceGray,
                self._scalar_function(),
                pikepdf.Dictionary(
                    Subtype=pikepdf.Name.NChannel,
                    Process=pikepdf.Dictionary(
                        ColorSpace=pikepdf.Name.DeviceGray,
                        Components=pikepdf.Array([pikepdf.Name.Old]),
                    ),
                    Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                ),
            ]
        )
        page.Resources.ColorSpace[pikepdf.Name.ProcessMixed] = devicen

    def _add_target_attributes(
        self,
        pdf: pikepdf.Pdf,
        page: pikepdf.Page,
        attributes: pikepdf.Dictionary,
        *,
        component_names: tuple[str, ...],
    ) -> None:
        devicen = pikepdf.Array(
            [
                pikepdf.Name.DeviceN,
                pikepdf.Array([pikepdf.Name(f"/{name}") for name in component_names]),
                pikepdf.Name.DeviceCMYK,
                self._calculator_function(pdf),
                attributes,
            ]
        )
        page.Resources.ColorSpace[pikepdf.Name.TargetAttributes] = devicen

    def _add_malformed_separation_name(
        self,
        _pdf: pikepdf.Pdf,
        page: pikepdf.Page,
    ) -> None:
        page.Resources.ColorSpace[pikepdf.Name.BadSeparation] = pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.String("Old"),
                pikepdf.Name.DeviceCMYK,
                self._separation("Other")[3],
            ]
        )

    @staticmethod
    def _add_separation_info_without_pages(
        _pdf: pikepdf.Pdf,
        page: pikepdf.Page,
    ) -> None:
        page.SeparationInfo = pikepdf.Dictionary(DeviceColorant=pikepdf.Name.Old)

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
    def _form(pdf: pikepdf.Pdf) -> pikepdf.Stream:
        form = pdf.make_stream(b"")
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 10, 10])
        form.Resources = pikepdf.Dictionary()
        return form

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
    def _scalar_function() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0]),
            C1=pikepdf.Array([1]),
            N=1,
        )

    @staticmethod
    def _calculator_function(pdf: pikepdf.Pdf) -> pikepdf.Stream:
        function = pdf.make_stream(b"{ pop 0 0 0 0 }")
        function.FunctionType = 4
        function.Domain = pikepdf.Array([0, 1])
        function.Range = pikepdf.Array([0, 1, 0, 1, 0, 1, 0, 1])
        return function


if __name__ == "__main__":
    unittest.main()
