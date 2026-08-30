from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pikepdf

from scripts.benchmark_inventory import create_benchmark_pdf, structural_metrics
from spotpdf.document import inspect_pdf
from spotpdf.inventory_graph import walk_reachable
from spotpdf.publication import open_strict


class InventoryPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_benchmark_counts_real_stream_parses_and_resource_contexts(self) -> None:
        for spot_count in (64, 128):
            with self.subTest(spot_count=spot_count):
                source = create_benchmark_pdf(self.root, spot_count)

                metrics = structural_metrics(source, spot_count)

                self.assertEqual(metrics["resource_contexts_scanned"], 16)
                self.assertEqual(metrics["actual_parse_calls"], 16)
                self.assertEqual(metrics["unique_parse_objects"], 16)
                self.assertEqual(metrics["max_parses_per_object"], 1)

    def test_page_tree_and_unique_form_graph_work_stays_linear(self) -> None:
        visits = {}
        for count in (64, 128):
            source = self._make_form_scale_pdf(count)
            with open_strict(source) as pdf:
                visits[count] = sum(1 for _ in walk_reachable(pdf))

        self.assertLessEqual(visits[128], 2 * visits[64] + 16)

    def test_non_page_parent_edge_remains_reachable(self) -> None:
        source = self.root / "non-page-parent.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Contents = pdf.make_stream(b"")
            hidden = pdf.make_indirect(
                pikepdf.Dictionary(Spot=self._separation("ParentReachableSpot"))
            )
            pdf.Root.Custom = pikepdf.Dictionary(Child=pikepdf.Dictionary(Parent=hidden))
            pdf.save(source)

        report = inspect_pdf(source)

        self.assertIn("ParentReachableSpot", report.spots)

    def test_shared_hazard_subtree_is_scanned_once(self) -> None:
        counts = {}
        for count in (64, 128):
            source = self._make_shared_hazard_pdf(count)
            import spotpdf.inventory_hazards as hazards

            original = hazards.parse_color_space
            calls = 0

            def counted(value, original_parse=original):
                nonlocal calls
                calls += 1
                return original_parse(value)

            with (
                patch.object(hazards, "parse_color_space", side_effect=counted),
                patch.object(
                    hazards,
                    "_subtree_colorants",
                    wraps=hazards._subtree_colorants,
                ) as subtree_scans,
            ):
                report = inspect_pdf(source)
            counts[count] = calls
            self.assertEqual(subtree_scans.call_count, 1)
            self.assertEqual(len(report.spots), count)
            self.assertTrue(
                all(
                    any("spot color in shading" in item for item in spot.contexts)
                    for spot in report.spots.values()
                )
            )

        self.assertLessEqual(counts[128], 2 * counts[64] + 16)

    def _make_form_scale_pdf(self, count: int) -> Path:
        path = self.root / f"form-scale-{count}.pdf"
        with pikepdf.Pdf.new() as pdf:
            for index in range(count):
                form = pdf.make_stream(b"/Ink cs 1 scn 0 0 1 1 re f\n")
                form.Type = pikepdf.Name.XObject
                form.Subtype = pikepdf.Name.Form
                form.BBox = pikepdf.Array([0, 0, 1, 1])
                form.Resources = pikepdf.Dictionary(
                    ColorSpace=pikepdf.Dictionary(Ink=self._separation(f"FormScaleSpot{index:03d}"))
                )
                page = pdf.add_blank_page(page_size=(10, 10))
                page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Paint=form))
                page.Contents = pdf.make_stream(b"/Paint Do\n")
            pdf.save(path)
        return path

    def _make_shared_hazard_pdf(self, count: int) -> Path:
        path = self.root / f"shared-hazard-{count}.pdf"
        with pikepdf.Pdf.new() as pdf:
            shared = pdf.make_indirect(
                pikepdf.Dictionary(
                    Values=pikepdf.Array(
                        [self._separation(f"HazardSpot{index:03d}") for index in range(count)]
                    )
                )
            )
            aliases = pikepdf.Dictionary({f"/Alias{index:03d}": shared for index in range(count)})
            page = pdf.add_blank_page(page_size=(10, 10))
            page.Resources = pikepdf.Dictionary(Shading=aliases)
            page.Contents = pdf.make_stream(b"")
            pdf.save(path)
        return path

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
                    C1=pikepdf.Array([1, 0, 1, 0]),
                    N=1,
                ),
            ]
        )


if __name__ == "__main__":
    unittest.main()
