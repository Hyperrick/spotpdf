from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.cli import _print_report
from spotpdf.document import check_spot, inspect_pdf, remove_spot
from spotpdf.inventory_values import indexed_name_array
from spotpdf.model import NameDependencyKind, SpotKind, UnsupportedSpotUseError


class InventoryEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_shared_indirect_colorants_dictionary_has_one_nested_definition(self) -> None:
        path = self.root / "shared-colorants.pdf"
        with pikepdf.Pdf.new() as pdf:
            shared_colorants = pdf.make_indirect(
                pikepdf.Dictionary(SharedSpot=self._separation("SharedSpot"))
            )
            for resource_name in ("FirstMixed", "SecondMixed"):
                page = pdf.add_blank_page(page_size=(100, 100))
                devicen = pdf.make_indirect(
                    pikepdf.Array(
                        [
                            pikepdf.Name.DeviceN,
                            pikepdf.Array([pikepdf.Name.SharedSpot]),
                            pikepdf.Name.DeviceCMYK,
                            self._cmyk_function(),
                            pikepdf.Dictionary(
                                Subtype=pikepdf.Name.NChannel,
                                Colorants=shared_colorants,
                            ),
                        ]
                    )
                )
                color_spaces = pikepdf.Dictionary()
                color_spaces[pikepdf.Name(f"/{resource_name}")] = devicen
                page.Resources = pikepdf.Dictionary(ColorSpace=color_spaces)
                page.Contents = pdf.make_stream(b"")
            pdf.save(path, min_version="1.6")

        report = inspect_pdf(path)
        nested = [
            definition
            for definition in report.definitions.values()
            if definition.kind is SpotKind.SEPARATION
            and definition.components[0].name == "SharedSpot"
        ]

        self.assertEqual(len(nested), 1)
        self.assertTrue(any(location.startswith("page 1") for location in nested[0].locations))
        self.assertTrue(any(location.startswith("page 2") for location in nested[0].locations))
        self.assertIn(" R /SharedSpot", nested[0].object_id)

    def test_shared_indirect_attributes_dictionary_has_one_nested_definition(self) -> None:
        path = self.root / "shared-attributes.pdf"
        with pikepdf.Pdf.new() as pdf:
            shared_attributes = pdf.make_indirect(
                pikepdf.Dictionary(
                    Subtype=pikepdf.Name.NChannel,
                    Colorants=pikepdf.Dictionary(SharedSpot=self._separation("SharedSpot")),
                )
            )
            for resource_name in ("FirstMixed", "SecondMixed"):
                page = pdf.add_blank_page(page_size=(100, 100))
                devicen = pdf.make_indirect(
                    pikepdf.Array(
                        [
                            pikepdf.Name.DeviceN,
                            pikepdf.Array([pikepdf.Name.SharedSpot]),
                            pikepdf.Name.DeviceCMYK,
                            self._cmyk_function(),
                            shared_attributes,
                        ]
                    )
                )
                color_spaces = pikepdf.Dictionary()
                color_spaces[pikepdf.Name(f"/{resource_name}")] = devicen
                page.Resources = pikepdf.Dictionary(ColorSpace=color_spaces)
                page.Contents = pdf.make_stream(b"")
            pdf.save(path, min_version="1.6")

        report = inspect_pdf(path)
        nested = [
            definition
            for definition in report.definitions.values()
            if definition.kind is SpotKind.SEPARATION
            and definition.components[0].name == "SharedSpot"
        ]

        self.assertEqual(len(nested), 1)
        self.assertTrue(any(location.startswith("page 1") for location in nested[0].locations))
        self.assertTrue(any(location.startswith("page 2") for location in nested[0].locations))
        self.assertIn(" R /Colorants /SharedSpot", nested[0].object_id)
        self.assertNotIn("direct at direct at", nested[0].object_id)

    def test_shared_form_propagates_every_page_context_to_direct_definitions(self) -> None:
        path = self.root / "shared-form-contexts.pdf"
        with pikepdf.Pdf.new() as pdf:
            shared_form = self._form(pdf)
            shared_form.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Ink=self._separation("SharedFormInk"))
            )
            for _ in range(2):
                page = pdf.add_blank_page(page_size=(100, 100))
                page.Resources = pikepdf.Dictionary(
                    XObject=pikepdf.Dictionary(SharedForm=shared_form)
                )
                page.Contents = pdf.make_stream(b"/SharedForm Do\n")
            pdf.save(path)

        report = inspect_pdf(path)
        definitions = [
            definition
            for definition in report.definitions.values()
            if definition.kind is SpotKind.SEPARATION
            and definition.components[0].name == "SharedFormInk"
        ]

        self.assertEqual(len(definitions), 1)
        self.assertTrue(any(location.startswith("page 1") for location in definitions[0].locations))
        self.assertTrue(any(location.startswith("page 2") for location in definitions[0].locations))

    def test_name_path_segments_are_unambiguous(self) -> None:
        path = self.root / "escaped-paths.pdf"
        with pikepdf.Pdf.new() as pdf:
            pdf.Root[pikepdf.Name("/A /B")] = self._separation("First")
            pdf.Root.A = pikepdf.Dictionary(B=self._separation("Second"))
            pdf.save(path)

        report = inspect_pdf(path)
        locations = {
            location
            for definition in report.definitions.values()
            for location in definition.locations
        }

        self.assertEqual(set(report.spots), {"First", "Second"})
        self.assertEqual(len(report.definitions), 2)
        self.assertIn("catalog /A#20#2FB", locations)
        self.assertIn("catalog /A /B", locations)

    def test_prepress_dependencies_are_context_bound_and_trapnet_is_recorded(self) -> None:
        path = self.root / "annotation-contexts.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            ordinary = self._form(pdf)
            ordinary.Colorants = pikepdf.Dictionary(OrdinaryInk=self._separation("OrdinaryInk"))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Ordinary=ordinary))
            page.Contents = pdf.make_stream(b"")

            trap = self._form(pdf)
            trap.PCM = pikepdf.Name.DeviceCMYK
            trap.SeparationColorNames = pikepdf.Array(
                [pikepdf.Name.TrapSpot, pikepdf.Name.SecondTrapSpot]
            )
            trap_annotation = pikepdf.Dictionary(
                Type=pikepdf.Name.Annot,
                Subtype=pikepdf.Name.TrapNet,
                F=68,
                Rect=pikepdf.Array([0, 0, 100, 100]),
                AP=pikepdf.Dictionary(
                    N=pikepdf.Dictionary(Current=trap),
                ),
                AS=pikepdf.Name.Current,
                LastModified=pikepdf.String("D:20260829000000+02'00'"),
            )
            page.Annots = pikepdf.Array([trap_annotation])
            pdf.save(path, min_version="1.4")

        report = inspect_pdf(path)
        trap_dependencies = [
            item
            for item in report.dependencies
            if item.kind is NameDependencyKind.TRAP_NETWORK_COLORANT
        ]

        self.assertEqual(
            [(item.name, item.location.rsplit("[", 1)[-1]) for item in trap_dependencies],
            [("TrapSpot", "0]"), ("SecondTrapSpot", "1]")],
        )
        self.assertFalse(
            any(
                item.kind is NameDependencyKind.PRINTER_MARK_COLORANT and item.name == "OrdinaryInk"
                for item in report.dependencies
            )
        )

    def test_removal_rejects_a_stale_mixing_hint_name_dependency(self) -> None:
        path = self.root / "stale-dependency.pdf"
        output = self.root / "must-not-exist.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            unrelated = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.Other]),
                    pikepdf.Name.DeviceCMYK,
                    self._cmyk_function(),
                    pikepdf.Dictionary(
                        Subtype=pikepdf.Name.NChannel,
                        MixingHints=pikepdf.Dictionary(
                            Solidities=pikepdf.Dictionary(Target=0.8),
                            PrintingOrder=pikepdf.Array([pikepdf.Name.Other, pikepdf.Name.Target]),
                        ),
                    ),
                ]
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Target=self._separation("Target"),
                    Unrelated=unrelated,
                )
            )
            page.Contents = pdf.make_stream(b"/Target cs 1 scn 0 0 10 10 re f\n")
            pdf.save(path, min_version="1.6")

        with self.assertRaisesRegex(UnsupportedSpotUseError, "prepress dependencies"):
            remove_spot(path, output, "Target")

        self.assertFalse(output.exists())

    def test_list_escapes_controls_but_exact_name_stays_unchanged(self) -> None:
        path = self.root / "control-name.pdf"
        raw_name = "Ink\tX\nY"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Contents = pdf.make_stream(b"")
            page.SeparationInfo = pikepdf.Dictionary(
                Pages=pikepdf.Array([page.obj]),
                DeviceColorant=pikepdf.String(raw_name),
            )
            pdf.save(path, min_version="1.3")

        report = inspect_pdf(path)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            _print_report(report)

        self.assertIn(raw_name, report.colorants)
        self.assertTrue(check_spot(path, raw_name))
        self.assertIn(r"Ink\x09X\x0aY", output.getvalue())
        self.assertNotIn(raw_name, output.getvalue())

    def test_indexed_names_keep_physical_array_positions(self) -> None:
        value = pikepdf.Array([pikepdf.Name.A, 7, pikepdf.Name.B])

        self.assertEqual(indexed_name_array(value), ((0, "A"), (2, "B")))

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
    def _form(pdf: pikepdf.Pdf) -> pikepdf.Stream:
        form = pdf.make_stream(b"")
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 100, 100])
        form.Resources = pikepdf.Dictionary()
        return form


if __name__ == "__main__":
    unittest.main()
