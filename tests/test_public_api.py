from __future__ import annotations

import inspect
import tempfile
import tomllib
import unittest
from dataclasses import fields
from os import PathLike
from pathlib import Path
from typing import get_type_hints

import spotpdf
import spotpdf.alternate as alternate_module
import spotpdf.convert as convert_module
import spotpdf.document as document_module
import spotpdf.limits as limits_module
import spotpdf.model as model_module
import spotpdf.rename as rename_module
from examples.create_demo_pdf import build_demo_pdf
from scripts.release_readme import ReleaseReadmeError, validate_release_readme
from spotpdf import (
    DEFAULT_PROCESSING_LIMITS,
    AlternateResult,
    BatchRemovalResult,
    ColorantRole,
    ConversionResult,
    InspectionReport,
    InvalidPdfError,
    NestingLimitExceededError,
    ProcessingBudgetExceeded,
    RemovalStats,
    RenameResult,
    SpotKind,
    SpotPdfError,
    SpotSummary,
    UnsupportedSpotUseError,
    check_spot,
    convert_spot_to_cmyk,
    inspect_pdf,
    remove_all_spots,
    remove_spot,
    rename_spot,
    set_alternate_cmyk,
)
from spotpdf.model import ColorSpaceDefinition, NameDependency

PUBLIC_API = (
    "DEFAULT_PROCESSING_LIMITS",
    "AlternateResult",
    "BatchRemovalResult",
    "ColorantRole",
    "ConversionResult",
    "InspectionReport",
    "InvalidPdfError",
    "NestingLimitExceededError",
    "ProcessingBudgetExceeded",
    "ProcessingLimits",
    "RemovalStats",
    "RenameResult",
    "SpotKind",
    "SpotPdfError",
    "SpotSummary",
    "UnsupportedSpotUseError",
    "__version__",
    "check_spot",
    "convert_spot_to_cmyk",
    "inspect_pdf",
    "remove_all_spots",
    "remove_spot",
    "rename_spot",
    "set_alternate_cmyk",
)


class PublicApiTests(unittest.TestCase):
    def test_root_exports_are_exact_unique_and_canonical(self) -> None:
        self.assertEqual(tuple(spotpdf.__all__), PUBLIC_API)
        self.assertEqual(len(spotpdf.__all__), len(set(spotpdf.__all__)))
        implementations = {
            "DEFAULT_PROCESSING_LIMITS": limits_module.DEFAULT_PROCESSING_LIMITS,
            "AlternateResult": model_module.AlternateResult,
            "BatchRemovalResult": model_module.BatchRemovalResult,
            "ColorantRole": model_module.ColorantRole,
            "ConversionResult": model_module.ConversionResult,
            "InspectionReport": model_module.InspectionReport,
            "InvalidPdfError": model_module.InvalidPdfError,
            "NestingLimitExceededError": model_module.NestingLimitExceededError,
            "ProcessingBudgetExceeded": limits_module.ProcessingBudgetExceeded,
            "ProcessingLimits": limits_module.ProcessingLimits,
            "RemovalStats": model_module.RemovalStats,
            "RenameResult": model_module.RenameResult,
            "SpotKind": model_module.SpotKind,
            "SpotPdfError": model_module.SpotPdfError,
            "SpotSummary": model_module.SpotSummary,
            "UnsupportedSpotUseError": model_module.UnsupportedSpotUseError,
            "__version__": model_module.__version__,
            "check_spot": document_module.check_spot,
            "convert_spot_to_cmyk": convert_module.convert_spot_to_cmyk,
            "inspect_pdf": document_module.inspect_pdf,
            "remove_all_spots": document_module.remove_all_spots,
            "remove_spot": document_module.remove_spot,
            "rename_spot": rename_module.rename_spot,
            "set_alternate_cmyk": alternate_module.set_alternate_cmyk,
        }
        for name, implementation in implementations.items():
            with self.subTest(name=name):
                self.assertIs(getattr(spotpdf, name), implementation)

        for internal in (
            "ColorSpaceDefinition",
            "NameDependency",
            "PdfObjectIdentity",
            "PercentageCmyk",
            "build_conversion_plan",
            "main",
            "parse_cmyk_percentages",
            "validate_cmyk_percentages",
        ):
            with self.subTest(internal=internal):
                self.assertNotIn(internal, spotpdf.__all__)

    def test_entrypoint_signatures_and_type_hints_are_stable(self) -> None:
        positional = inspect.Parameter.POSITIONAL_OR_KEYWORD
        keyword = inspect.Parameter.KEYWORD_ONLY
        contracts = {
            inspect_pdf: (("path", positional), ("limits", keyword), InspectionReport),
            check_spot: (("path", positional), ("spot", positional), ("limits", keyword), bool),
            remove_spot: (
                ("input_path", positional),
                ("output_path", positional),
                ("spot", positional),
                ("force", keyword),
                ("limits", keyword),
                RemovalStats,
            ),
            remove_all_spots: (
                ("input_path", positional),
                ("output_path", positional),
                ("force", keyword),
                ("limits", keyword),
                BatchRemovalResult,
            ),
            rename_spot: (
                ("input_path", positional),
                ("output_path", positional),
                ("source", positional),
                ("destination", positional),
                ("force", keyword),
                ("limits", keyword),
                RenameResult,
            ),
            set_alternate_cmyk: (
                ("input_path", positional),
                ("output_path", positional),
                ("spot", positional),
                ("cmyk", positional),
                ("force", keyword),
                ("limits", keyword),
                AlternateResult,
            ),
            convert_spot_to_cmyk: (
                ("input_path", positional),
                ("output_path", positional),
                ("spot", positional),
                ("cmyk", positional),
                ("force", keyword),
                ("limits", keyword),
                ConversionResult,
            ),
        }
        for entrypoint, contract in contracts.items():
            with self.subTest(entrypoint=entrypoint.__name__):
                signature = inspect.signature(entrypoint)
                expected_parameters = contract[:-1]
                actual_parameters = tuple(
                    (name, parameter.kind) for name, parameter in signature.parameters.items()
                )
                self.assertEqual(actual_parameters, expected_parameters)
                hints = get_type_hints(entrypoint)
                self.assertIs(hints["return"], contract[-1])
                self.assertIs(
                    signature.parameters["limits"].default,
                    DEFAULT_PROCESSING_LIMITS,
                )
                if "force" in signature.parameters:
                    self.assertIs(signature.parameters["force"].default, False)
                for path_name in ("path", "input_path", "output_path"):
                    if path_name in signature.parameters:
                        self.assertEqual(hints[path_name], str | PathLike[str])

    def test_result_fields_types_and_mutability_are_explicit(self) -> None:
        contracts = {
            InspectionReport: (
                {
                    "colorants": dict[str, SpotSummary],
                    "spots": dict[str, SpotSummary],
                    "definitions": dict[str, ColorSpaceDefinition],
                    "dependencies": tuple[NameDependency, ...],
                },
                False,
            ),
            SpotSummary: (
                {
                    "name": str,
                    "kinds": set[SpotKind],
                    "roles": set[ColorantRole],
                    "definition_ids": set[str],
                    "locations": set[str],
                    "pages": set[int],
                    "paint_operations": int,
                    "contexts": set[str],
                },
                False,
            ),
            RemovalStats: (
                {
                    "pages_changed": set[int],
                    "forms_changed": int,
                    "text_blocks": int,
                    "text_show_operations": int,
                    "fills_removed": int,
                    "strokes_removed": int,
                    "resources_removed": int,
                },
                False,
            ),
            BatchRemovalResult: (
                {"spots": tuple[str, ...], "stats": RemovalStats},
                True,
            ),
            RenameResult: (
                {
                    "source": str,
                    "destination": str,
                    "definitions_renamed": int,
                    "references_renamed": int,
                },
                True,
            ),
            AlternateResult: (
                {
                    "spot": str,
                    "cmyk_percentages": tuple[float, float, float, float],
                    "definitions_changed": int,
                },
                True,
            ),
            ConversionResult: (
                {
                    "spot": str,
                    "cmyk_percentages": tuple[float, float, float, float],
                    "definitions_removed": int,
                    "resources_removed": int,
                    "page_content_sequences_changed": int,
                    "forms_changed": int,
                    "color_operators_rewritten": int,
                    "pages_affected": tuple[int, ...],
                },
                True,
            ),
        }
        for result_type, (expected_hints, frozen) in contracts.items():
            with self.subTest(result=result_type.__name__):
                self.assertEqual(
                    tuple(item.name for item in fields(result_type)),
                    tuple(expected_hints),
                )
                self.assertEqual(get_type_hints(result_type), expected_hints)
                self.assertIs(result_type.__dataclass_params__.frozen, frozen)

    def test_controlled_error_hierarchy_is_exact(self) -> None:
        self.assertEqual(SpotPdfError.__bases__, (Exception,))
        self.assertEqual(InvalidPdfError.__bases__, (SpotPdfError,))
        self.assertEqual(NestingLimitExceededError.__bases__, (InvalidPdfError,))
        self.assertEqual(UnsupportedSpotUseError.__bases__, (SpotPdfError,))
        self.assertEqual(ProcessingBudgetExceeded.__bases__, (SpotPdfError,))

    def test_all_entrypoints_accept_string_and_path_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.pdf"
            build_demo_pdf(source)

            for use_strings in (False, True):
                label = "string" if use_strings else "path"
                as_argument = str if use_strings else lambda value: value
                source_argument = as_argument(source)

                with self.subTest(argument=label, entrypoint="inspect_pdf"):
                    self.assertIsInstance(inspect_pdf(source_argument), InspectionReport)
                with self.subTest(argument=label, entrypoint="check_spot"):
                    self.assertTrue(check_spot(source_argument, "Varnish"))

                remove_output = root / f"remove-{label}.pdf"
                with self.subTest(argument=label, entrypoint="remove_spot"):
                    self.assertIsInstance(
                        remove_spot(
                            source_argument,
                            as_argument(remove_output),
                            "Varnish",
                        ),
                        RemovalStats,
                    )

                all_output = root / f"remove-all-{label}.pdf"
                with self.subTest(argument=label, entrypoint="remove_all_spots"):
                    self.assertIsInstance(
                        remove_all_spots(source_argument, as_argument(all_output)),
                        BatchRemovalResult,
                    )

                rename_output = root / f"rename-{label}.pdf"
                with self.subTest(argument=label, entrypoint="rename_spot"):
                    self.assertIsInstance(
                        rename_spot(
                            source_argument,
                            as_argument(rename_output),
                            "Varnish",
                            "Varnish Renamed",
                        ),
                        RenameResult,
                    )

                alternate_output = root / f"alternate-{label}.pdf"
                with self.subTest(argument=label, entrypoint="set_alternate_cmyk"):
                    self.assertIsInstance(
                        set_alternate_cmyk(
                            source_argument,
                            as_argument(alternate_output),
                            "Varnish",
                            (100, 0, 0, 0),
                        ),
                        AlternateResult,
                    )

                convert_output = root / f"convert-{label}.pdf"
                with self.subTest(argument=label, entrypoint="convert_spot_to_cmyk"):
                    self.assertIsInstance(
                        convert_spot_to_cmyk(
                            source_argument,
                            as_argument(convert_output),
                            "Varnish",
                            (0, 62, 0, 0),
                        ),
                        ConversionResult,
                    )

    def test_source_package_declares_inline_typing(self) -> None:
        marker = Path(spotpdf.__file__).with_name("py.typed")
        self.assertTrue(marker.is_file())

    def test_readme_routes_to_the_version_bound_python_api_guide(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
        version = project["project"]["version"]
        readme_path = repository / "README.md"
        readme = readme_path.read_text(encoding="utf-8")

        # The guide first ships in v0.7.0. Until that version bump, main is the
        # only working link and the release validator must keep rejecting it.
        expected_ref = "main" if version == "0.6.0" else f"v{version}"
        expected_url = (
            f"https://github.com/Hyperrick/spotpdf/blob/{expected_ref}/docs/python-api.md"
        )
        self.assertEqual(readme.count(expected_url), 2)
        guide = (repository / "docs" / "python-api.md").read_text(encoding="utf-8")
        normalized_readme = " ".join(
            line.removeprefix("> ").strip() for line in readme.splitlines()
        )
        normalized_guide = " ".join(line.removeprefix("> ").strip() for line in guide.splitlines())
        readme_transition = (
            "first ships in v0.7.0; it is not present in the currently installed v0.6.0 release"
        )
        guide_transition = "first shipping in v0.7.0. Stable v0.6.0"

        if expected_ref == "main":
            self.assertIn(readme_transition, normalized_readme)
            self.assertIn(guide_transition, normalized_guide)
            with self.assertRaisesRegex(
                ReleaseReadmeError,
                r"must use v0\.6\.0.*docs/python-api\.md",
            ):
                validate_release_readme(readme_path, readme, "v0.6.0")
        else:
            self.assertNotIn(readme_transition, normalized_readme)
            self.assertNotIn(guide_transition, normalized_guide)
            self.assertEqual(
                validate_release_readme(readme_path, readme, f"v{version}"),
                "pypi",
            )


if __name__ == "__main__":
    unittest.main()
