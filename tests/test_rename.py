from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.cli import build_parser, main
from spotpdf.document import inspect_pdf
from spotpdf.inventory_graph import walk_reachable
from spotpdf.model import NameDependencyKind, SpotKind, SpotPdfError
from spotpdf.objects import object_key
from spotpdf.rename import rename_spot


class RenameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_renames_every_definition_without_touching_content_or_preview(self) -> None:
        source = self._make_basic_pdf(source_name="Old", duplicate_source=True)
        output = self.root / "renamed.pdf"
        preview_before = self._preview_snapshots(source, "Old")
        content_before = self._page_content(source)

        result = rename_spot(source, output, "Old", "New")

        report = inspect_pdf(output)
        self.assertEqual(result.source, "Old")
        self.assertEqual(result.destination, "New")
        self.assertEqual(result.definitions_renamed, 2)
        self.assertNotIn("Old", report.colorants)
        self.assertIn("New", report.spots)
        self.assertIn("Other", report.spots)
        self.assertEqual(self._preview_snapshots(output, "New"), preview_before)
        self.assertEqual(self._page_content(output), content_before)
        with pikepdf.open(output) as pdf:
            # A color-space resource alias is not a plate name and must not be renamed.
            self.assertIn(pikepdf.Name.Old, pdf.pages[0].Resources.ColorSpace)

    def test_shared_indirect_definition_is_renamed_once_in_every_context(self) -> None:
        source = self.root / "shared.pdf"
        with pikepdf.Pdf.new() as pdf:
            shared = pdf.make_indirect(self._separation("Old"))
            for index in range(2):
                page = pdf.add_blank_page(page_size=(100, 100))
                spaces = pikepdf.Dictionary()
                spaces[pikepdf.Name(f"/Ink{index}")] = shared
                page.Resources = pikepdf.Dictionary(ColorSpace=spaces)
                page.Contents = pdf.make_stream(f"/Ink{index} cs 0.5 scn 0 0 10 10 re f\n".encode())
            pdf.save(source)

        result = rename_spot(source, self.root / "shared-output.pdf", "Old", "New")
        report = inspect_pdf(self.root / "shared-output.pdf")
        definitions = [
            definition
            for definition in report.definitions.values()
            if definition.kind is SpotKind.SEPARATION and definition.components[0].name == "New"
        ]

        self.assertEqual(result.definitions_renamed, 1)
        self.assertEqual(len(definitions), 1)
        self.assertTrue(any(location.startswith("page 1") for location in definitions[0].locations))
        self.assertTrue(any(location.startswith("page 2") for location in definitions[0].locations))

    def test_special_pdf_names_round_trip_exactly(self) -> None:
        cases = (
            ("Old Name", "New Name"),
            ("Hash#Old", "Hash#New"),
            ("Slash/Old", "Slash/New"),
            ("Grün", "PANTONE 日本語"),
        )
        for index, (source_name, destination_name) in enumerate(cases):
            with self.subTest(source=source_name, destination=destination_name):
                source = self._make_basic_pdf(source_name=source_name, suffix=str(index))
                output = self.root / f"encoded-{index}.pdf"

                rename_spot(source, output, source_name, destination_name)

                report = inspect_pdf(output)
                self.assertNotIn(source_name, report.colorants)
                self.assertIn(destination_name, report.spots)
                self.assertEqual(
                    {name for name in self._separation_names(output) if name != "Other"},
                    {destination_name},
                )

    def test_lowercase_process_like_names_and_case_distinct_targets_are_allowed(self) -> None:
        lowercase = self._make_basic_pdf(source_name="black", suffix="lowercase")
        lowercase_output = self.root / "lowercase-output.pdf"

        rename_spot(lowercase, lowercase_output, "black", "cyan")

        self.assertIn("cyan", inspect_pdf(lowercase_output).spots)
        case_distinct = self._make_basic_pdf(
            source_name="Old",
            destination_name="NEW",
            suffix="case-distinct",
        )
        case_output = self.root / "case-distinct-output.pdf"

        rename_spot(case_distinct, case_output, "Old", "New")

        report = inspect_pdf(case_output)
        self.assertIn("NEW", report.spots)
        self.assertIn("New", report.spots)

    def test_nchannel_dependencies_are_renamed_together(self) -> None:
        source = self._make_nchannel_pdf()
        output = self.root / "nchannel-output.pdf"

        result = rename_spot(source, output, "Old", "New")

        report = inspect_pdf(output)
        self.assertGreater(result.references_renamed, 0)
        self.assertNotIn("Old", report.colorants)
        self.assertFalse(any(item.name == "Old" for item in report.dependencies))
        kinds = {item.kind for item in report.dependencies if item.name == "New"}
        self.assertEqual(
            kinds,
            {
                NameDependencyKind.INDIVIDUAL_COLORANT,
                NameDependencyKind.SOLIDITY,
                NameDependencyKind.DOT_GAIN,
                NameDependencyKind.PRINTING_ORDER,
            },
        )
        with pikepdf.open(output) as pdf:
            devicen = pdf.pages[0].Resources.ColorSpace.Mixed
            attributes = devicen[4]
            self.assertEqual([str(item) for item in devicen[1]], ["/CustomProcess", "/New"])
            self.assertIn(pikepdf.Name.New, attributes.Colorants)
            self.assertNotIn(pikepdf.Name.Old, attributes.Colorants)
            self.assertEqual(str(attributes.Colorants.New[1]), "/New")
            self.assertIn(pikepdf.Name.New, attributes.MixingHints.Solidities)
            self.assertIn(pikepdf.Name.New, attributes.MixingHints.DotGain)
            self.assertEqual(
                [str(item) for item in attributes.MixingHints.PrintingOrder],
                ["/New", "/CustomProcess"],
            )

    def test_shared_nchannel_attributes_keep_one_nested_definition(self) -> None:
        source = self.root / "shared-attributes.pdf"
        with pikepdf.Pdf.new() as pdf:
            attributes = pdf.make_indirect(
                pikepdf.Dictionary(
                    Subtype=pikepdf.Name.NChannel,
                    Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                )
            )
            for index in range(2):
                page = pdf.add_blank_page(page_size=(100, 100))
                devicen = pikepdf.Array(
                    [
                        pikepdf.Name.DeviceN,
                        pikepdf.Array([pikepdf.Name.Old]),
                        pikepdf.Name.DeviceCMYK,
                        self._cmyk_function(),
                        attributes,
                    ]
                )
                spaces = pikepdf.Dictionary()
                spaces[pikepdf.Name(f"/Mixed{index}")] = devicen
                page.Resources = pikepdf.Dictionary(ColorSpace=spaces)
                page.Contents = pdf.make_stream(b"")
            pdf.save(source, min_version="1.6")

        rename_spot(source, self.root / "shared-attributes-output.pdf", "Old", "New")
        report = inspect_pdf(self.root / "shared-attributes-output.pdf")
        nested = [
            item
            for item in report.definitions.values()
            if item.kind is SpotKind.SEPARATION and item.components[0].name == "New"
        ]

        self.assertNotIn("Old", report.colorants)
        self.assertEqual(len(nested), 1)
        self.assertTrue(any(location.startswith("page 1") for location in nested[0].locations))
        self.assertTrue(any(location.startswith("page 2") for location in nested[0].locations))

    def test_reserved_and_process_names_are_rejected_atomically(self) -> None:
        forbidden = ("All", "None", "Cyan", "Magenta", "Yellow", "Black")
        for index, name in enumerate(forbidden):
            with self.subTest(source=name):
                source = self._make_basic_pdf(source_name=name, suffix=f"source-{index}")
                self._assert_failure_preserves_output(source, name, "New")
            with self.subTest(destination=name):
                source = self._make_basic_pdf(source_name="Old", suffix=f"dest-{index}")
                self._assert_failure_preserves_output(source, "Old", name)

    def test_process_role_and_mixed_role_sources_are_rejected(self) -> None:
        process_only = self._make_process_role_pdf(include_standalone=False)
        mixed_role = self._make_process_role_pdf(include_standalone=True)

        self._assert_failure_preserves_output(process_only, "CustomProcess", "New")
        self._assert_failure_preserves_output(mixed_role, "CustomProcess", "New")

    def test_destination_collisions_and_same_name_are_rejected(self) -> None:
        collision = self._make_basic_pdf(source_name="Old", destination_name="New")
        dependency_collision = self._make_dependency_collision_pdf()

        self._assert_failure_preserves_output(collision, "Old", "New")
        self._assert_failure_preserves_output(collision, "Old", "Old")
        self._assert_failure_preserves_output(dependency_collision, "Old", "New")

    def test_missing_devicen_only_and_malformed_sources_are_rejected(self) -> None:
        valid = self._make_basic_pdf(source_name="Old")
        devicen_only = self._make_devicen_only_pdf()
        malformed = self._make_malformed_separation_pdf()

        self._assert_failure_preserves_output(valid, "Missing", "New")
        self._assert_failure_preserves_output(devicen_only, "Old", "New")
        self._assert_failure_preserves_output(malformed, "Old", "New")

    def test_signature_encryption_and_permissions_never_publish_output(self) -> None:
        signed = self._make_basic_pdf(source_name="Old", signature=True, suffix="signed")
        encrypted = self._make_encrypted_pdf(restricted=False)
        restricted = self._make_encrypted_pdf(restricted=True)

        self._assert_failure_preserves_output(signed, "Old", "New")
        self._assert_failure_preserves_output(encrypted, "Old", "New")
        self._assert_failure_preserves_output(restricted, "Old", "New")

    def test_output_guards_and_force_success(self) -> None:
        source = self._make_basic_pdf(source_name="Old")
        output = self.root / "existing.pdf"
        output.write_bytes(b"keep-existing")

        with self.assertRaises(SpotPdfError):
            rename_spot(source, output, "Old", "New")
        self.assertEqual(output.read_bytes(), b"keep-existing")

        rename_spot(source, output, "Old", "New", force=True)
        self.assertIn("New", inspect_pdf(output).spots)

        with self.assertRaises(SpotPdfError):
            rename_spot(source, source, "Old", "New", force=True)
        self.assertIn("Old", inspect_pdf(source).spots)

    def test_forced_output_symlinks_are_rejected(self) -> None:
        source = self._make_basic_pdf(source_name="Old")
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                target = self.root / f"target-{dangling}.txt"
                output = self.root / f"link-{dangling}.pdf"
                if not dangling:
                    target.write_bytes(b"keep-target")
                try:
                    output.symlink_to(target)
                except OSError as error:
                    self.skipTest(f"symbolic links are unavailable: {error}")

                with self.assertRaises(SpotPdfError):
                    rename_spot(source, output, "Old", "New", force=True)

                self.assertTrue(output.is_symlink())
                if dangling:
                    self.assertFalse(target.exists())
                else:
                    self.assertEqual(target.read_bytes(), b"keep-target")

    def test_cli_parser_and_command_support_rename(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "rename",
                "input.pdf",
                "--spot",
                "Old Name",
                "--to",
                "New/Name",
                "-o",
                "output.pdf",
                "--force",
            ]
        )
        self.assertEqual(args.command, "rename")
        self.assertEqual(args.spot, "Old Name")
        self.assertEqual(args.destination, "New/Name")
        self.assertTrue(args.force)

        source = self._make_basic_pdf(source_name="Old Name", suffix="cli")
        output = self.root / "cli-output.pdf"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "rename",
                    str(source),
                    "--spot",
                    "Old Name",
                    "--to",
                    "New/Name",
                    "-o",
                    str(output),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("Old Name", stdout.getvalue())
        self.assertIn("New/Name", stdout.getvalue())
        self.assertIn("New/Name", inspect_pdf(output).spots)

    def _assert_failure_preserves_output(
        self,
        source: Path,
        source_name: str,
        destination_name: str,
    ) -> None:
        output = self.root / f"forced-{len(list(self.root.glob('forced-*.pdf')))}.pdf"
        output.write_bytes(b"keep-existing")

        with self.assertRaises(SpotPdfError):
            rename_spot(source, output, source_name, destination_name, force=True)

        self.assertEqual(output.read_bytes(), b"keep-existing")
        self.assertEqual(list(self.root.glob(f".{output.stem}-*.tmp.pdf")), [])

    def _make_basic_pdf(
        self,
        *,
        source_name: str,
        destination_name: str | None = None,
        duplicate_source: bool = False,
        signature: bool = False,
        suffix: str = "",
    ) -> Path:
        path = self.root / f"basic-{suffix or len(list(self.root.glob('basic-*.pdf')))}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            spaces = pikepdf.Dictionary()
            spaces[pikepdf.Name.Old] = pdf.make_indirect(self._separation(source_name))
            spaces[pikepdf.Name.Second] = self._separation(
                source_name if duplicate_source else "Other"
            )
            spaces[pikepdf.Name.Other] = self._separation("Other")
            if destination_name is not None:
                spaces[pikepdf.Name.Destination] = self._separation(destination_name)
            page.Resources = pikepdf.Dictionary(ColorSpace=spaces)
            page.Contents = pdf.make_stream(
                b"/Old cs 0.5 scn 0 0 40 40 re f\n/Other CS 0.7 SCN 5 w 0 0 40 40 re S\n"
            )
            if signature:
                field = pdf.make_indirect(
                    pikepdf.Dictionary(FT=pikepdf.Name.Sig, T=pikepdf.String("Signature1"))
                )
                pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([field]))
            pdf.save(path)
        return path

    def _make_nchannel_pdf(self) -> Path:
        path = self.root / "nchannel.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
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
                Colorants=pikepdf.Dictionary(Old=self._separation("Old")),
                MixingHints=pikepdf.Dictionary(
                    Solidities=pikepdf.Dictionary(Old=0.8, Default=0.0),
                    DotGain=pikepdf.Dictionary(Old=self._scalar_function()),
                    PrintingOrder=pikepdf.Array([pikepdf.Name.Old, pikepdf.Name.CustomProcess]),
                ),
            )
            devicen = pikepdf.Array(
                [
                    pikepdf.Name.DeviceN,
                    pikepdf.Array([pikepdf.Name.CustomProcess, pikepdf.Name.Old]),
                    pikepdf.Name.DeviceCMYK,
                    self._calculator_function(pdf, inputs=2, outputs=4),
                    attributes,
                ]
            )
            page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Mixed=devicen))
            page.Contents = pdf.make_stream(b"/Mixed cs 0.2 0.8 scn 0 0 20 20 re f\n")
            pdf.save(path, min_version="1.6")
        return path

    def _make_process_role_pdf(self, *, include_standalone: bool) -> Path:
        path = self.root / f"process-role-{include_standalone}.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            spaces = pikepdf.Dictionary(
                Mixed=pikepdf.Array(
                    [
                        pikepdf.Name.DeviceN,
                        pikepdf.Array([pikepdf.Name.CustomProcess]),
                        pikepdf.Name.DeviceGray,
                        self._scalar_function(),
                        pikepdf.Dictionary(
                            Subtype=pikepdf.Name.NChannel,
                            Process=pikepdf.Dictionary(
                                ColorSpace=pikepdf.Name.DeviceGray,
                                Components=pikepdf.Array([pikepdf.Name.CustomProcess]),
                            ),
                            Colorants=pikepdf.Dictionary(
                                CustomProcess=self._separation("CustomProcess")
                            ),
                        ),
                    ]
                )
            )
            if include_standalone:
                spaces[pikepdf.Name.Standalone] = self._separation("CustomProcess")
            page.Resources = pikepdf.Dictionary(ColorSpace=spaces)
            page.Contents = pdf.make_stream(b"")
            pdf.save(path, min_version="1.6")
        return path

    def _make_dependency_collision_pdf(self) -> Path:
        path = self.root / "dependency-collision.pdf"
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
                        Colorants=pikepdf.Dictionary(Other=self._separation("Other")),
                        MixingHints=pikepdf.Dictionary(
                            Solidities=pikepdf.Dictionary(New=0.8),
                            PrintingOrder=pikepdf.Array([pikepdf.Name.Other, pikepdf.Name.New]),
                        ),
                    ),
                ]
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Ink=self._separation("Old"),
                    Unrelated=unrelated,
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(path, min_version="1.6")
        return path

    def _make_devicen_only_pdf(self) -> Path:
        path = self.root / "devicen-only.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Mixed=pikepdf.Array(
                        [
                            pikepdf.Name.DeviceN,
                            pikepdf.Array([pikepdf.Name.Old]),
                            pikepdf.Name.DeviceCMYK,
                            self._cmyk_function(),
                        ]
                    )
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(path)
        return path

    def _make_malformed_separation_pdf(self) -> Path:
        path = self.root / "malformed-separation.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Ink=pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name.Old])
                )
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(path)
        return path

    def _make_encrypted_pdf(self, *, restricted: bool) -> Path:
        path = self.root / f"encrypted-{restricted}.pdf"
        allow = pikepdf.Permissions(modify_other=not restricted)
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(Ink=self._separation("Old"))
            )
            page.Contents = pdf.make_stream(b"")
            pdf.save(
                path,
                encryption=pikepdf.Encryption(owner="owner-secret", user="", allow=allow),
            )
        return path

    @staticmethod
    def _page_content(path: Path) -> tuple[bytes, ...]:
        with pikepdf.open(path) as pdf:
            return tuple(page.Contents.read_bytes() for page in pdf.pages)

    @staticmethod
    def _preview_snapshots(path: Path, name: str) -> tuple[tuple[bytes, bytes], ...]:
        snapshots: list[tuple[bytes, bytes]] = []
        seen = set()
        with pikepdf.open(path) as pdf:
            for visit in walk_reachable(pdf):
                value = visit.value
                if (
                    not isinstance(value, pikepdf.Array)
                    or len(value) < 4
                    or value[0] != pikepdf.Name.Separation
                    or str(value[1]).lstrip("/") != name
                    or object_key(value) in seen
                ):
                    continue
                seen.add(object_key(value))
                snapshots.append((value[2].unparse(resolved=True), value[3].unparse(resolved=True)))
        return tuple(sorted(snapshots))

    @staticmethod
    def _separation_names(path: Path) -> tuple[str, ...]:
        names: set[str] = set()
        with pikepdf.open(path) as pdf:
            for visit in walk_reachable(pdf):
                value = visit.value
                if (
                    isinstance(value, pikepdf.Array)
                    and len(value) >= 2
                    and value[0] == pikepdf.Name.Separation
                ):
                    names.add(str(value[1]).lstrip("/"))
        return tuple(sorted(names))

    @staticmethod
    def _separation(name: str) -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name(f"/{name}"),
                pikepdf.Name.DeviceCMYK,
                RenameTests._cmyk_function(),
            ]
        )

    @staticmethod
    def _cmyk_function() -> pikepdf.Dictionary:
        return pikepdf.Dictionary(
            FunctionType=2,
            Domain=pikepdf.Array([0, 1]),
            C0=pikepdf.Array([0, 0, 0, 0]),
            C1=pikepdf.Array([0, 0.8, 1, 0]),
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
        function = pdf.make_stream(b"{ " + (b"pop " * inputs) + (b"0 " * outputs) + b"}")
        function.FunctionType = 4
        function.Domain = pikepdf.Array([value for _ in range(inputs) for value in (0, 1)])
        function.Range = pikepdf.Array([value for _ in range(outputs) for value in (0, 1)])
        return function


if __name__ == "__main__":
    unittest.main()
