from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pikepdf

from spotpdf.cli import _print_report
from spotpdf.document import check_spot, inspect_pdf, remove_all_spots, remove_spot
from spotpdf.model import (
    ColorantRole,
    NameDependencyKind,
    SpotKind,
    UnsupportedSpotUseError,
)


class RoleAwareInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_nchannel_classifies_process_spot_and_nested_definitions(self) -> None:
        source = self._make_nchannel_pdf()

        report = inspect_pdf(source)

        self.assertNotIn("CustomProcess", report.spots)
        self.assertNotIn("CustomProcess", report.spot_names)
        self.assertEqual(report.colorants["CustomProcess"].roles, {ColorantRole.PROCESS})
        self.assertEqual(
            report.colorants["CustomProcess"].kinds,
            {SpotKind.DEVICEN, SpotKind.SEPARATION},
        )
        self.assertEqual(report.colorants["Black"].roles, {ColorantRole.PROCESS})
        self.assertEqual(
            report.spot_names,
            frozenset({"ExtraSpot", "SpotOrange"}),
        )

        devicen = self._one_definition(report, SpotKind.DEVICEN)
        self.assertEqual(devicen.subtype, "NChannel")
        self.assertEqual(devicen.process_color_space, "DeviceCMYK")
        self.assertEqual(
            devicen.process_components,
            ("CustomProcess", "Magenta", "Yellow", "Black"),
        )
        self.assertEqual(
            devicen.individual_colorants,
            ("CustomProcess", "ExtraSpot", "SpotOrange"),
        )
        self.assertIsNotNone(devicen.identity.object_number)
        self.assertTrue(any(location.startswith("page 1") for location in devicen.locations))
        self.assertTrue(any(location.startswith("page 2") for location in devicen.locations))

        nested_process = next(
            definition
            for definition in report.definitions.values()
            if definition.kind is SpotKind.SEPARATION
            and definition.components[0].name == "CustomProcess"
        )
        self.assertEqual(nested_process.components[0].role, ColorantRole.PROCESS)
        self.assertTrue(nested_process.object_id)
        self.assertTrue(any(location.startswith("page 1") for location in nested_process.locations))
        self.assertTrue(any(location.startswith("page 2") for location in nested_process.locations))

    def test_canonical_cmyk_names_depend_on_the_nchannel_process_space(self) -> None:
        for process_kind in ("DeviceRGB", "Lab"):
            with self.subTest(process_kind=process_kind):
                report = inspect_pdf(self._make_process_role_pdf(process_kind))

                self.assertEqual(report.colorants["Black"].roles, {ColorantRole.SPOT})
                self.assertIn("Black", report.spots)
                self.assertIn("Black", report.spot_names)

        for process_kind in ("DeviceCMYK", "ICCBasedCMYK"):
            with self.subTest(process_kind=process_kind):
                report = inspect_pdf(self._make_process_role_pdf(process_kind))

                self.assertEqual(report.colorants["Black"].roles, {ColorantRole.PROCESS})
                self.assertNotIn("Black", report.spots)
                self.assertNotIn("Black", report.spot_names)

    def test_nchannel_records_every_supported_name_dependency(self) -> None:
        report = inspect_pdf(self._make_nchannel_pdf())
        dependencies = {(item.kind, item.name) for item in report.dependencies}

        self.assertIn(
            (NameDependencyKind.PROCESS_COMPONENT, "CustomProcess"),
            dependencies,
        )
        self.assertIn(
            (NameDependencyKind.INDIVIDUAL_COLORANT, "ExtraSpot"),
            dependencies,
        )
        self.assertIn((NameDependencyKind.SOLIDITY, "SpotOrange"), dependencies)
        self.assertIn((NameDependencyKind.DOT_GAIN, "ExtraSpot"), dependencies)
        self.assertIn(
            (NameDependencyKind.PRINTING_ORDER, "CustomProcess"),
            dependencies,
        )
        self.assertNotIn((NameDependencyKind.SOLIDITY, "Default"), dependencies)
        self.assertNotIn((NameDependencyKind.DOT_GAIN, "Default"), dependencies)
        self.assertTrue(
            all(
                "[4] /" in item.location
                for item in report.dependencies
                if item.definition_id is not None
            )
        )
        self.assertTrue(
            all(
                item.owner.label and item.location
                for item in report.dependencies
                if item.definition_id is not None
            )
        )

    def test_list_output_labels_custom_nchannel_process_names_as_process(self) -> None:
        report = inspect_pdf(self._make_nchannel_pdf())
        output = StringIO()

        with redirect_stdout(output):
            _print_report(report)

        self.assertIn("NAME\tROLE\tKIND", output.getvalue())
        custom_line = next(
            line for line in output.getvalue().splitlines() if line.startswith("CustomProcess\t")
        )
        self.assertTrue(custom_line.startswith("CustomProcess\tprocess\t"))

    def test_object_identity_and_locations_are_stable_across_inspections(self) -> None:
        source = self._make_nchannel_pdf()

        first = inspect_pdf(source)
        second = inspect_pdf(source)

        first_definitions = {
            definition.object_id: definition.locations for definition in first.definitions.values()
        }
        second_definitions = {
            definition.object_id: definition.locations for definition in second.definitions.values()
        }
        self.assertEqual(first_definitions, second_definitions)
        self.assertTrue(
            all(
                definition.object_id
                and definition.locations
                and all(location for location in definition.locations)
                for definition in first.definitions.values()
            )
        )

    def test_separation_info_and_printer_mark_dependencies_are_inventoried(self) -> None:
        source = self._make_prepress_pdf()

        report = inspect_pdf(source)

        summary = report.spots["PreSeparated Orange"]
        self.assertEqual(summary.roles, {ColorantRole.SPOT})
        self.assertEqual(
            summary.kinds,
            {SpotKind.SEPARATION, SpotKind.SEPARATION_INFO},
        )
        self.assertIn("PreSeparated Orange", report.spot_names)
        self.assertTrue(any("page 1 /SeparationInfo" in item for item in summary.locations))
        dependencies = {(item.kind, item.name) for item in report.dependencies}
        self.assertIn(
            (NameDependencyKind.SEPARATION_INFO, "PreSeparated Orange"),
            dependencies,
        )
        self.assertIn(
            (NameDependencyKind.PRINTER_MARK_COLORANT, "ControlStrip"),
            dependencies,
        )
        self.assertIn("ControlStrip", report.spot_names)

        output = self.root / "must-not-change-preseparated.pdf"
        with self.assertRaisesRegex(UnsupportedSpotUseError, "SeparationInfo"):
            remove_spot(source, output, "PreSeparated Orange")
        self.assertFalse(output.exists())

    def test_remove_all_ignores_a_custom_named_nchannel_process_component(self) -> None:
        source = self._make_process_only_nchannel_pdf()
        output = self.root / "process-only-output.pdf"

        result = remove_all_spots(source, output)

        self.assertEqual(result.spots, ())
        self.assertEqual(source.read_bytes(), output.read_bytes())
        report = inspect_pdf(output)
        self.assertEqual(report.colorants["CustomGray"].roles, {ColorantRole.PROCESS})
        self.assertNotIn("CustomGray", report.spots)
        self.assertNotIn("CustomGray", report.spot_names)
        self.assertFalse(check_spot(output, "CustomGray"))

        exact_output = self.root / "process-only-exact-output.pdf"
        stats = remove_spot(source, exact_output, "CustomGray")
        self.assertFalse(stats.changed)
        self.assertEqual(source.read_bytes(), exact_output.read_bytes())

    def _make_nchannel_pdf(self) -> Path:
        path = self.root / "nchannel.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            second_page = pdf.add_blank_page(page_size=(100, 100))
            colorants = pikepdf.Dictionary(
                CustomProcess=self._separation("CustomProcess"),
                SpotOrange=self._separation("SpotOrange"),
                ExtraSpot=self._separation("ExtraSpot"),
            )
            attributes = pikepdf.Dictionary(
                Subtype=pikepdf.Name.NChannel,
                Process=pikepdf.Dictionary(
                    ColorSpace=pikepdf.Name.DeviceCMYK,
                    Components=pikepdf.Array(
                        [
                            pikepdf.Name.CustomProcess,
                            pikepdf.Name.Magenta,
                            pikepdf.Name.Yellow,
                            pikepdf.Name.Black,
                        ]
                    ),
                ),
                Colorants=colorants,
                MixingHints=pikepdf.Dictionary(
                    Solidities=pikepdf.Dictionary(
                        SpotOrange=0.8,
                        ExtraSpot=0.6,
                        Default=0.0,
                    ),
                    DotGain=pikepdf.Dictionary(
                        SpotOrange=self._scalar_function(),
                        ExtraSpot=self._scalar_function(),
                        Default=self._scalar_function(),
                    ),
                    PrintingOrder=pikepdf.Array(
                        [
                            pikepdf.Name.SpotOrange,
                            pikepdf.Name.CustomProcess,
                            pikepdf.Name.Black,
                            pikepdf.Name.ExtraSpot,
                        ]
                    ),
                ),
            )
            devicen = pdf.make_indirect(
                pikepdf.Array(
                    [
                        pikepdf.Name.DeviceN,
                        pikepdf.Array(
                            [
                                pikepdf.Name.CustomProcess,
                                pikepdf.Name.SpotOrange,
                                pikepdf.Name.Black,
                            ]
                        ),
                        pikepdf.Name.DeviceCMYK,
                        self._calculator_function(pdf, inputs=3, outputs=4),
                        attributes,
                    ]
                )
            )
            page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Mixed=devicen))
            page.Contents = pdf.make_stream(b"0 g 0 0 10 10 re f\n")
            second_page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(SharedMixed=devicen)
            )
            second_page.Contents = pdf.make_stream(b"0 g 0 0 10 10 re f\n")
            pdf.save(path, min_version="1.6")
        return path

    def _make_process_only_nchannel_pdf(self) -> Path:
        path = self.root / "process-only.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            devicen = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.CustomGray]),
                    pikepdf.Name.DeviceGray,
                    self._scalar_function(),
                    pikepdf.Dictionary(
                        Subtype=pikepdf.Name.NChannel,
                        Process=pikepdf.Dictionary(
                            ColorSpace=pikepdf.Name.DeviceGray,
                            Components=pikepdf.Array([pikepdf.Name.CustomGray]),
                        ),
                    ),
                ]
            )
            page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(ProcessOnly=devicen))
            page.Contents = pdf.make_stream(b"0 g 0 0 10 10 re f\n")
            pdf.save(path, min_version="1.6")
        return path

    def _make_process_role_pdf(self, process_kind: str) -> Path:
        path = self.root / f"process-role-{process_kind}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            if process_kind == "DeviceCMYK":
                process_color_space = pikepdf.Name.DeviceCMYK
                process_components = ("CustomCyan", "MAlias", "YAlias", "KAlias")
                names = ("CustomCyan", "Black")
            elif process_kind == "ICCBasedCMYK":
                profile = pdf.make_stream(b"synthetic four-channel profile")
                profile.N = 4
                profile.Alternate = pikepdf.Name.DeviceCMYK
                process_color_space = pikepdf.Array([pikepdf.Name.ICCBased, profile])
                process_components = ("CustomCyan", "MAlias", "YAlias", "KAlias")
                names = ("CustomCyan", "Black")
            elif process_kind == "DeviceRGB":
                process_color_space = pikepdf.Name.DeviceRGB
                process_components = ("RAlias", "GAlias", "BAlias")
                names = (*process_components, "Black")
            elif process_kind == "Lab":
                process_color_space = pikepdf.Array(
                    [
                        pikepdf.Name.Lab,
                        pikepdf.Dictionary(WhitePoint=pikepdf.Array([0.9505, 1.0, 1.0890])),
                    ]
                )
                process_components = ("L", "a", "b")
                names = (*process_components, "Black")
            else:  # pragma: no cover - test helper guard
                raise ValueError(f"unsupported process kind: {process_kind}")

            devicen = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name(f"/{name}") for name in names]),
                    pikepdf.Name.DeviceCMYK,
                    self._calculator_function(pdf, inputs=len(names), outputs=4),
                    pikepdf.Dictionary(
                        Subtype=pikepdf.Name.NChannel,
                        Process=pikepdf.Dictionary(
                            ColorSpace=process_color_space,
                            Components=pikepdf.Array(
                                [pikepdf.Name(f"/{name}") for name in process_components]
                            ),
                        ),
                        Colorants=pikepdf.Dictionary(Black=self._separation("Black")),
                    ),
                ]
            )
            page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(ProcessRole=devicen))
            page.Contents = pdf.make_stream(b"")
            pdf.save(path, min_version="1.6")
        return path

    def _make_prepress_pdf(self) -> Path:
        path = self.root / "prepress.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Contents = pdf.make_stream(b"")
            page.SeparationInfo = pikepdf.Dictionary(
                Pages=pikepdf.Array([page.obj]),
                DeviceColorant=pikepdf.String("PreSeparated Orange"),
                ColorSpace=self._separation("PreSeparated Orange"),
            )
            mark = pdf.make_stream(b"")
            mark.Type = pikepdf.Name.XObject
            mark.Subtype = pikepdf.Name.Form
            mark.BBox = pikepdf.Array([0, 0, 10, 10])
            mark.Resources = pikepdf.Dictionary()
            mark.Colorants = pikepdf.Dictionary(ControlStrip=self._separation("ControlStrip"))
            annotation = pikepdf.Dictionary(
                Type=pikepdf.Name.Annot,
                Subtype=pikepdf.Name.PrinterMark,
                F=68,
                Rect=pikepdf.Array([0, 0, 10, 10]),
                AP=pikepdf.Dictionary(N=mark),
            )
            page.Annots = pikepdf.Array([annotation])
            pdf.save(path, min_version="1.4")
        return path

    @staticmethod
    def _one_definition(report, kind: SpotKind):
        matches = [item for item in report.definitions.values() if item.kind is kind]
        if len(matches) != 1:
            raise AssertionError(f"expected one {kind.value} definition, found {len(matches)}")
        return matches[0]

    @classmethod
    def _separation(cls, name: str) -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name(f"/{name}"),
                pikepdf.Name.DeviceCMYK,
                cls._cmyk_function(),
            ]
        )

    @staticmethod
    def _cmyk_function() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=pikepdf.Array([1, 0, 1, 0]),
            N=1,
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
    def _calculator_function(
        pdf: pikepdf.Pdf,
        *,
        inputs: int,
        outputs: int,
    ) -> pikepdf.Stream:
        program = b"{ " + (b"pop " * inputs) + (b"0 " * outputs) + b"}"
        function = pdf.make_stream(program)
        function.FunctionType = 4
        function.Domain = pikepdf.Array([value for _ in range(inputs) for value in (0, 1)])
        function.Range = pikepdf.Array([value for _ in range(outputs) for value in (0, 1)])
        return function


if __name__ == "__main__":
    unittest.main()
