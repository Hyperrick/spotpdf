from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.colors import SPECIAL_COLORANTS
from spotpdf.document import _process_document, inspect_pdf, remove_spot
from spotpdf.inventory import discover_spot_declarations
from spotpdf.model import (
    ColorantRole,
    InvalidPdfError,
    RemovalStats,
    UnsupportedSpotUseError,
)
from spotpdf.publication import open_strict
from spotpdf.scan import validate_spot_uses_for_removal


class SinglePassInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_single_pass_matches_removal_oracle_for_paths_text_and_forms(self) -> None:
        source = self._make_equivalence_pdf()

        expected = self._legacy_usage(source)
        actual = self._usage_snapshot(inspect_pdf(source))

        self.assertEqual(actual, expected)
        self.assertEqual(actual["SpotA"][0:2], ({1}, 2))
        self.assertEqual(actual["SpotB"][0:2], ({1}, 2))
        self.assertEqual(actual["SpotC"][0:2], ({1, 2}, 1))

    def test_content_error_freezes_only_affected_colorant(self) -> None:
        source = self._make_partial_error_pdf()

        expected = self._legacy_usage(source)
        report = inspect_pdf(source)
        actual = self._usage_snapshot(report)

        self.assertEqual(actual, expected)
        self.assertEqual(report.spots["SpotA"].pages, {1})
        self.assertEqual(report.spots["SpotA"].paint_operations, 2)
        self.assertTrue(
            any("target-colored patterns" in item for item in report.spots["SpotA"].contexts)
        )
        self.assertEqual(report.spots["SpotB"].pages, {1, 2})
        self.assertEqual(report.spots["SpotB"].paint_operations, 2)
        self.assertEqual(report.spots["SpotB"].contexts, {"painted"})

    def test_text_block_safety_remains_target_specific_and_transactional(self) -> None:
        cases = {
            "target-and-process": (
                b"BT /A cs 1 scn (target) Tj 0 g (process) Tj ET\n",
                "requires font metrics",
            ),
            "target-only-and-retained": (
                b"BT /A cs 1 scn (only) Tj 2 Tr /B CS 1 SCN (retained) Tj ET\n",
                "mixed target-only and retained paint",
            ),
            "clipping": (
                b"BT 4 Tr /A cs 1 scn (clip) Tj ET\n",
                "clipping text",
            ),
            "quote": (
                b"BT /A cs 1 scn (quote) ' ET\n",
                "quote text operators",
            ),
        }
        for name, (content, error_text) in cases.items():
            with self.subTest(name=name):
                source = self._make_text_edge_pdf(name, content)
                expected = self._legacy_usage(source)
                report = inspect_pdf(source)

                self.assertEqual(self._usage_snapshot(report), expected)
                target = report.spots["SpotA"]
                self.assertEqual(target.pages, set())
                self.assertEqual(target.paint_operations, 0)
                self.assertTrue(any(error_text in item for item in target.contexts))

    def test_invalid_content_is_global_only_when_a_colorant_reaches_it(self) -> None:
        active = self._make_invalid_content_pdf(blocked=False)
        blocked = self._make_invalid_content_pdf(blocked=True)

        with self.assertRaisesRegex(InvalidPdfError, "unresolved color space 'Missing'"):
            inspect_pdf(active)

        summary = inspect_pdf(blocked).spots["BlockedSpot"]
        self.assertTrue(any("reachable DeviceN" in item for item in summary.contexts))
        self.assertEqual(summary.paint_operations, 0)

    def test_structural_hazards_are_attributed_per_colorant(self) -> None:
        source = self._make_structural_hazard_pdf()

        report = inspect_pdf(source)

        self.assertTrue(
            any("reachable DeviceN" in item for item in report.spots["DeviceNSpot"].contexts)
        )
        self.assertTrue(
            any("annotation appearances" in item for item in report.spots["AnnotSpot"].contexts)
        )
        self.assertEqual(report.spots["PaintedSpot"].contexts, {"painted"})
        self.assertEqual(report.spots["PaintedSpot"].paint_operations, 1)

    def test_resource_hazard_pass_matches_removal_oracle(self) -> None:
        cases = {
            "uncolored-pattern": "uncolored patterns",
            "shading": "spot color in shading",
            "pattern": "spot color in pattern",
            "type3": "spot color in Type3 font",
            "soft-mask": "spot color in soft mask",
            "image": "spot-color image",
        }
        for kind, message in cases.items():
            with self.subTest(kind=kind):
                source = self._make_resource_hazard_pdf(kind)
                expected = self._legacy_usage(source)
                report = inspect_pdf(source)

                self.assertEqual(self._usage_snapshot(report), expected)
                self.assertTrue(
                    any(message in item for item in report.spots["HazardSpot"].contexts)
                )

    def test_shared_form_direct_resources_are_stable_and_cover_every_page(self) -> None:
        source = self._make_shared_form_pdf(own_resources=True)

        for _ in range(20):
            summary = inspect_pdf(source).spots["SharedSpot"]
            self.assertEqual(summary.pages, {1, 2})
            self.assertEqual(summary.paint_operations, 1)
            self.assertEqual(summary.contexts, {"painted"})

        output = self.root / "shared-output.pdf"
        stats = remove_spot(source, output, "SharedSpot")
        self.assertEqual(stats.pages_changed, {1, 2})
        self.assertEqual(stats.fills_removed, 1)

    def test_shared_form_with_different_inherited_resources_is_unsupported(self) -> None:
        source = self._make_shared_form_pdf(own_resources=False)

        report = inspect_pdf(source)

        first = report.spots["FirstSpot"]
        second = report.spots["SecondSpot"]
        self.assertEqual(first.pages, {1})
        self.assertEqual(first.paint_operations, 1)
        self.assertTrue(any("context-dependent" in item for item in first.contexts))
        self.assertEqual(second.pages, set())
        self.assertEqual(second.paint_operations, 0)
        self.assertTrue(any("context-dependent" in item for item in second.contexts))

    def test_nested_shared_form_change_reaches_every_calling_page(self) -> None:
        source = self._make_nested_shared_form_pdf()
        output = self.root / "nested-shared-output.pdf"

        summary = inspect_pdf(source).spots["NestedSharedSpot"]
        stats = remove_spot(source, output, "NestedSharedSpot")

        self.assertEqual(summary.pages, {1, 2})
        self.assertEqual(summary.paint_operations, 1)
        self.assertEqual(stats.pages_changed, {1, 2})
        self.assertEqual(stats.fills_removed, 1)

    def test_cached_inner_change_propagates_through_new_outer_form(self) -> None:
        source = self._make_cached_inner_then_outer_pdf()
        output = self.root / "cached-inner-outer-output.pdf"

        summary = inspect_pdf(source).spots["CachedNestedSpot"]
        stats = remove_spot(source, output, "CachedNestedSpot")

        self.assertEqual(summary.pages, {1, 2})
        self.assertEqual(summary.paint_operations, 1)
        self.assertEqual(stats.pages_changed, {1, 2})
        self.assertEqual(stats.fills_removed, 1)

    def test_page_inline_image_guard_blocks_inventory_and_removal(self) -> None:
        source = self._make_inline_page_pdf()
        output = self.root / "inline-output.pdf"

        summary = inspect_pdf(source).spots["InlineSpot"]

        self.assertEqual(summary.pages, set())
        self.assertEqual(summary.paint_operations, 0)
        self.assertEqual(
            summary.contexts,
            {
                "declared",
                "unsupported: page 1: inline images with target spot resources are not supported",
            },
        )
        with self.assertRaisesRegex(
            UnsupportedSpotUseError,
            "inline images with target spot resources",
        ):
            remove_spot(source, output, "InlineSpot")
        self.assertFalse(output.exists())

    def test_form_inline_image_guard_blocks_planned_rewrite(self) -> None:
        source = self._make_inline_form_pdf()
        output = self.root / "inline-form-output.pdf"

        summary = inspect_pdf(source).spots["InlineFormSpot"]

        self.assertEqual(summary.pages, set())
        self.assertEqual(summary.paint_operations, 0)
        self.assertTrue(any("rewriting a stream with inline images" in x for x in summary.contexts))
        with self.assertRaisesRegex(
            UnsupportedSpotUseError,
            "rewriting a stream with inline images",
        ):
            remove_spot(source, output, "InlineFormSpot")
        self.assertFalse(output.exists())

    def _legacy_usage(self, path: Path):
        with open_strict(path) as pdf:
            report = discover_spot_declarations(pdf)
            for name, summary in report.colorants.items():
                if name in SPECIAL_COLORANTS:
                    summary.contexts.add("reserved separation")
                    continue
                if ColorantRole.PROCESS in summary.roles:
                    summary.contexts.add("process colorant; preserved by --all")
                stats = RemovalStats()
                try:
                    validate_spot_uses_for_removal(
                        pdf,
                        frozenset({name}),
                        declarations=report,
                    )
                    _process_document(pdf, frozenset({name}), apply=False, stats=stats)
                except UnsupportedSpotUseError as error:
                    summary.contexts.add(f"unsupported: {error}")
                summary.pages.update(stats.pages_changed)
                summary.paint_operations = (
                    stats.text_show_operations + stats.fills_removed + stats.strokes_removed
                )
                summary.contexts.add("painted" if summary.paint_operations else "declared")
        return self._usage_snapshot(report)

    @staticmethod
    def _usage_snapshot(report):
        return {
            name: (set(summary.pages), summary.paint_operations, set(summary.contexts))
            for name, summary in report.colorants.items()
        }

    def _make_equivalence_pdf(self) -> Path:
        path = self.root / "equivalence.pdf"
        with pikepdf.Pdf.new() as pdf:
            first = pdf.add_blank_page(page_size=(100, 100))
            first.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    A=self._separation("SpotA"),
                    B=self._separation("SpotB"),
                    C=self._separation("SpotC"),
                )
            )
            first.Contents = pdf.make_stream(
                b"/A cs 1 scn 0 G 0 0 10 10 re B\n"
                b"0 g /B CS 1 SCN 20 0 10 10 re B*\n"
                b"BT 2 Tr /A cs 1 scn /B CS 1 SCN (AB) Tj ET\n"
                b"/C cs 1 scn\n0 g 0 G\n"
            )

            form = self._form(
                pdf,
                b"/Ink cs 1 scn 0 0 10 10 re f\n",
                pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=self._separation("SpotC"))),
            )
            second = pdf.add_blank_page(page_size=(100, 100))
            second.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Paint=form))
            second.Contents = pdf.make_stream(b"/Paint Do\n")
            pdf.save(path)
        return path

    def _make_partial_error_pdf(self) -> Path:
        path = self.root / "partial-error.pdf"
        with pikepdf.Pdf.new() as pdf:
            for page_number in range(2):
                page = pdf.add_blank_page(page_size=(100, 100))
                page.Resources = pikepdf.Dictionary(
                    ColorSpace=pikepdf.Dictionary(
                        A=self._separation("SpotA"),
                        B=self._separation("SpotB"),
                    ),
                    Pattern=pikepdf.Dictionary(P=pikepdf.Dictionary()),
                )
                content = (
                    b"/A cs 1 scn 0 0 10 10 re f\n"
                    + (b"/A cs /P scn\n" if page_number else b"")
                    + b"/B cs 1 scn 20 0 10 10 re f\n"
                )
                page.Contents = pdf.make_stream(content)
            pdf.save(path)
        return path

    def _make_structural_hazard_pdf(self) -> Path:
        path = self.root / "structural-hazards.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    DeviceNTarget=self._separation("DeviceNSpot"),
                    Painted=self._separation("PaintedSpot"),
                )
            )
            page.Contents = pdf.make_stream(b"/Painted cs 1 scn 0 0 10 10 re f\n")

            appearance = self._form(
                pdf,
                b"",
                pikepdf.Dictionary(
                    ColorSpace=pikepdf.Dictionary(Ink=self._separation("AnnotSpot"))
                ),
            )
            page.Annots = pikepdf.Array(
                [
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.Annot,
                        Subtype=pikepdf.Name.Stamp,
                        Rect=pikepdf.Array([0, 0, 10, 10]),
                        AP=pikepdf.Dictionary(N=appearance),
                    )
                ]
            )
            pdf.Root.Extra = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.DeviceNSpot]),
                    pikepdf.Name.DeviceCMYK,
                    self._function(),
                ]
            )
            pdf.save(path)
        return path

    def _make_text_edge_pdf(self, name: str, content: bytes) -> Path:
        path = self.root / f"text-edge-{name}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    A=self._separation("SpotA"),
                    B=self._separation("SpotB"),
                )
            )
            page.Contents = pdf.make_stream(content)
            pdf.save(path)
        return path

    def _make_invalid_content_pdf(self, *, blocked: bool) -> Path:
        path = self.root / f"invalid-content-{blocked}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary()
            page.Contents = pdf.make_stream(b"/Missing cs 1 scn\n")
            if blocked:
                pdf.Root.Extra = pikepdf.Array(
                    [
                        pikepdf.Name.DeviceN,
                        pikepdf.Array([pikepdf.Name.BlockedSpot]),
                        pikepdf.Name.DeviceCMYK,
                        self._function(),
                    ]
                )
            else:
                page.Resources.ColorSpace = pikepdf.Dictionary(
                    Target=self._separation("ActiveSpot")
                )
            pdf.save(path)
        return path

    def _make_shared_form_pdf(self, *, own_resources: bool) -> Path:
        path = self.root / f"shared-form-{own_resources}.pdf"
        with pikepdf.Pdf.new() as pdf:
            form_resources = (
                pikepdf.Dictionary(
                    ColorSpace=pikepdf.Dictionary(Ink=self._separation("SharedSpot"))
                )
                if own_resources
                else None
            )
            form = self._form(
                pdf,
                b"/Ink cs 1 scn 0 0 10 10 re f\n",
                form_resources,
            )
            for index in range(2):
                page = pdf.add_blank_page(page_size=(100, 100))
                resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Paint=form))
                if not own_resources:
                    name = "FirstSpot" if index == 0 else "SecondSpot"
                    resources.ColorSpace = pikepdf.Dictionary(Ink=self._separation(name))
                page.Resources = resources
                page.Contents = pdf.make_stream(b"/Paint Do\n")
            pdf.save(path)
        return path

    def _make_resource_hazard_pdf(self, kind: str) -> Path:
        path = self.root / f"resource-hazard-{kind}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            spot = self._separation("HazardSpot")
            resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Target=spot))
            if kind == "uncolored-pattern":
                resources.ColorSpace.PatternSpot = pikepdf.Array([pikepdf.Name.Pattern, spot])
            elif kind == "shading":
                resources.Shading = pikepdf.Dictionary(Shade=pikepdf.Dictionary(ColorSpace=spot))
            elif kind == "pattern":
                resources.Pattern = pikepdf.Dictionary(Tile=pikepdf.Dictionary(ColorSpace=spot))
            elif kind == "type3":
                resources.Font = pikepdf.Dictionary(
                    Glyphs=pikepdf.Dictionary(Subtype=pikepdf.Name.Type3, Extra=spot)
                )
            elif kind == "soft-mask":
                resources.ExtGState = pikepdf.Dictionary(
                    Transparency=pikepdf.Dictionary(SMask=pikepdf.Dictionary(Extra=spot))
                )
            elif kind == "image":
                image = pdf.make_stream(b"\x00")
                image.Type = pikepdf.Name.XObject
                image.Subtype = pikepdf.Name.Image
                image.Width = 1
                image.Height = 1
                image.BitsPerComponent = 8
                image.ColorSpace = spot
                resources.XObject = pikepdf.Dictionary(Photo=image)
            else:  # pragma: no cover - test table is exhaustive
                raise ValueError(kind)
            page.Resources = resources
            page.Contents = pdf.make_stream(b"")
            pdf.save(path)
        return path

    def _make_nested_shared_form_pdf(self) -> Path:
        path = self.root / "nested-shared-form.pdf"
        with pikepdf.Pdf.new() as pdf:
            inner = self._form(
                pdf,
                b"/Ink cs 1 scn 0 0 10 10 re f\n",
                pikepdf.Dictionary(
                    ColorSpace=pikepdf.Dictionary(Ink=self._separation("NestedSharedSpot"))
                ),
            )
            outer = self._form(
                pdf,
                b"/Inner Do\n",
                pikepdf.Dictionary(XObject=pikepdf.Dictionary(Inner=inner)),
            )
            for _ in range(2):
                page = pdf.add_blank_page(page_size=(100, 100))
                page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Outer=outer))
                page.Contents = pdf.make_stream(b"/Outer Do\n")
            pdf.save(path)
        return path

    def _make_cached_inner_then_outer_pdf(self) -> Path:
        path = self.root / "cached-inner-then-outer.pdf"
        with pikepdf.Pdf.new() as pdf:
            inner = self._form(
                pdf,
                b"/Ink cs 1 scn 0 0 10 10 re f\n",
                pikepdf.Dictionary(
                    ColorSpace=pikepdf.Dictionary(Ink=self._separation("CachedNestedSpot"))
                ),
            )
            outer = self._form(pdf, b"/Inner Do\n", None)
            shared_resources = pdf.make_indirect(
                pikepdf.Dictionary(XObject=pikepdf.Dictionary(Inner=inner, Outer=outer))
            )

            first = pdf.add_blank_page(page_size=(100, 100))
            first.Resources = shared_resources
            first.Contents = pdf.make_stream(b"/Inner Do /Outer Do\n")

            second = pdf.add_blank_page(page_size=(100, 100))
            second.Resources = shared_resources
            second.Contents = pdf.make_stream(b"/Outer Do\n")
            pdf.save(path)
        return path

    def _make_inline_page_pdf(self) -> Path:
        path = self.root / "inline-page.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Target=self._separation("InlineSpot"))
            )
            page.Contents = pdf.make_stream(self._inline_image())
            pdf.save(path)
        return path

    def _make_inline_form_pdf(self) -> Path:
        path = self.root / "inline-form.pdf"
        with pikepdf.Pdf.new() as pdf:
            form = self._form(
                pdf,
                self._inline_image() + b"/Target cs 1 scn\n",
                pikepdf.Dictionary(
                    ColorSpace=pikepdf.Dictionary(Target=self._separation("InlineFormSpot"))
                ),
            )
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Paint=form))
            page.Contents = pdf.make_stream(b"/Paint Do\n")
            pdf.save(path)
        return path

    @classmethod
    def _separation(cls, name: str) -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name(f"/{name}"),
                pikepdf.Name.DeviceCMYK,
                cls._function(),
            ]
        )

    @staticmethod
    def _function() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=pikepdf.Array([1, 0, 1, 0]),
            N=1,
        )

    @staticmethod
    def _form(
        pdf: pikepdf.Pdf,
        content: bytes,
        resources: pikepdf.Dictionary | None,
    ) -> pikepdf.Stream:
        form = pdf.make_stream(content)
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 100, 100])
        if resources is not None:
            form.Resources = resources
        return form

    @staticmethod
    def _inline_image() -> bytes:
        return b"BI /W 1 /H 1 /BPC 8 /CS /G ID \x00 EI\n"


if __name__ == "__main__":
    unittest.main()
