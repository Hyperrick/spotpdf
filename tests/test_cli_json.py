from __future__ import annotations

import unittest

import pikepdf

from tests.cli_json_helpers import JsonCliTestCase
from tests.conversion_fixtures import make_basic_conversion_pdf


class JsonCliTests(JsonCliTestCase):
    def test_list_json_is_complete_sorted_and_canonical(self) -> None:
        completed = self._run("--format", "json", "list", self.source)

        payload = self._success(completed, command="list")
        result = payload["result"]
        self.assertEqual(result["input"], str(self.source))
        self.assertEqual(result["colorant_count"], 3)
        self.assertEqual(
            [item["name"] for item in result["colorants"]],
            ["CutContour", "Personalization", "Varnish"],
        )
        self.assertEqual(
            set(result["colorants"][0]),
            {"name", "roles", "kinds", "pages", "paint_operations", "contexts"},
        )
        varnish = result["colorants"][2]
        self.assertEqual(varnish["roles"], ["spot"])
        self.assertEqual(varnish["kinds"], ["Separation"])
        self.assertEqual(varnish["pages"], [1])
        self.assertEqual(varnish["paint_operations"], 1)
        self.assertEqual(varnish["contexts"], ["painted"])

    def test_check_json_preserves_predicate_exit_semantics(self) -> None:
        present = self._run("check", self.source, "--spot", "Varnish", "--format", "json")
        absent = self._run("--format=json", "check", self.source, "--spot", "Missing")

        present_payload = self._success(present, command="check", exit_code=2)
        absent_payload = self._success(absent, command="check", exit_code=0)
        self.assertEqual(
            present_payload["result"],
            {"input": str(self.source), "spot": "Varnish", "present": True},
        )
        self.assertEqual(
            absent_payload["result"],
            {"input": str(self.source), "spot": "Missing", "present": False},
        )

    def test_every_mutation_command_has_an_exact_result_contract(self) -> None:
        remove_output = self.root / "remove ä.pdf"
        remove_all_output = self.root / "remove-all.pdf"
        rename_output = self.root / "renamed ö.pdf"
        alternate_output = self.root / "alternate.pdf"
        convert_source = make_basic_conversion_pdf(self.root / "convert-source.pdf")
        convert_output = self.root / "converted.pdf"

        remove = self._success(
            self._run(
                "--format",
                "json",
                "remove",
                self.source,
                "--spot",
                "Varnish",
                "-o",
                remove_output,
            ),
            command="remove",
        )["result"]
        remove_all = self._success(
            self._run(
                "--format",
                "json",
                "remove",
                self.source,
                "--all",
                "-o",
                remove_all_output,
            ),
            command="remove",
        )["result"]
        rename = self._success(
            self._run(
                "--format",
                "json",
                "rename",
                self.source,
                "--spot",
                "Varnish",
                "--to",
                "Varnish Ü",
                "-o",
                rename_output,
            ),
            command="rename",
        )["result"]
        alternate = self._success(
            self._run(
                "--format",
                "json",
                "set-alternate",
                self.source,
                "--spot",
                "Varnish",
                "--cmyk",
                "0,80,100,0",
                "-o",
                alternate_output,
            ),
            command="set-alternate",
        )["result"]
        convert = self._success(
            self._run(
                "--format",
                "json",
                "convert",
                convert_source,
                "--spot",
                "DemoSpot",
                "--to-cmyk",
                "0,80,100,0",
                "-o",
                convert_output,
            ),
            command="convert",
        )["result"]

        self.assertEqual(remove["selection"], {"mode": "spot", "spot": "Varnish"})
        self.assertEqual(remove["input"], str(self.source))
        self.assertEqual(remove["output"], str(remove_output))
        self.assertEqual(
            remove["stats"],
            self._stats(fills_removed=1, resources_removed=1),
        )
        self.assertEqual(remove_all["selection"], {"mode": "all"})
        self.assertEqual(remove_all["input"], str(self.source))
        self.assertEqual(remove_all["output"], str(remove_all_output))
        self.assertEqual(
            remove_all["spots_removed"],
            ["CutContour", "Personalization", "Varnish"],
        )
        self.assertEqual(
            remove_all["stats"],
            self._stats(
                fills_removed=1,
                strokes_removed=1,
                text_blocks=2,
                text_show_operations=2,
                resources_removed=3,
            ),
        )
        self.assertEqual(rename["source"], "Varnish")
        self.assertEqual(rename["destination"], "Varnish Ü")
        self.assertEqual(rename["input"], str(self.source))
        self.assertEqual(rename["output"], str(rename_output))
        self.assertEqual(rename["definitions_renamed"], 1)
        self.assertEqual(rename["references_renamed"], 0)
        self.assertEqual(alternate["spot"], "Varnish")
        self.assertEqual(alternate["input"], str(self.source))
        self.assertEqual(alternate["output"], str(alternate_output))
        self.assertEqual(alternate["cmyk_percentages"], [0.0, 80.0, 100.0, 0.0])
        self.assertEqual(alternate["definitions_changed"], 1)
        self.assertEqual(
            {
                key: convert[key]
                for key in (
                    "spot",
                    "cmyk_percentages",
                    "definitions_removed",
                    "resources_removed",
                    "page_content_sequences_changed",
                    "forms_changed",
                    "color_operators_rewritten",
                    "pages_affected",
                )
            },
            {
                "spot": "DemoSpot",
                "cmyk_percentages": [0.0, 80.0, 100.0, 0.0],
                "definitions_removed": 1,
                "resources_removed": 1,
                "page_content_sequences_changed": 1,
                "forms_changed": 0,
                "color_operators_rewritten": 4,
                "pages_affected": [1],
            },
        )
        self.assertEqual(convert["input"], str(convert_source))
        self.assertEqual(convert["output"], str(convert_output))
        for output in (
            remove_output,
            remove_all_output,
            rename_output,
            alternate_output,
            convert_output,
        ):
            self.assertTrue(output.is_file())

    def test_empty_inventory_and_remove_all_keep_one_result_shape(self) -> None:
        source = self.root / "plain.pdf"
        output = self.root / "plain-copy.pdf"
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary()
            page.Contents = pdf.make_stream(b"0 g 0 0 10 10 re f\n")
            pdf.save(source)

        listed = self._success(self._run("--format", "json", "list", source), command="list")[
            "result"
        ]
        removed = self._success(
            self._run("--format", "json", "remove", source, "--all", "-o", output),
            command="remove",
        )["result"]

        self.assertEqual(listed["colorant_count"], 0)
        self.assertEqual(listed["colorants"], [])
        self.assertEqual(removed["spots_removed"], [])
        self.assertEqual(removed["stats"], self._stats(changed=False, pages_changed=[]))
        self.assertEqual(source.read_bytes(), output.read_bytes())

    def test_untrusted_unicode_and_controls_round_trip_as_one_safe_record(self) -> None:
        source = self.root / "controls.pdf"
        raw_name = 'Café\tX\nY\u0085"\\'
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Contents = pdf.make_stream(b"")
            page.SeparationInfo = pikepdf.Dictionary(
                Pages=pikepdf.Array([page.obj]),
                DeviceColorant=pikepdf.String(raw_name),
            )
            pdf.save(source, min_version="1.3")

        completed = self._run("--format", "json", "list", source)
        payload = self._success(completed, command="list")

        self.assertEqual(payload["result"]["colorants"][0]["name"], raw_name)
        self.assertEqual(completed.stdout.count("\n"), 1)
        for escaped in (r"\u00e9", r"\t", r"\n", r"\u0085", r"\"", r"\\"):
            self.assertIn(escaped, completed.stdout)

    def test_json_order_is_stable_for_casefold_and_unicode_ties(self) -> None:
        source = self.root / "sorting.pdf"
        names = ("alpha", "Alpha", "É", "E\u0301")
        with pikepdf.Pdf.new() as pdf:
            for name in names:
                page = pdf.add_blank_page(page_size=(100, 100))
                page.Contents = pdf.make_stream(b"")
                page.SeparationInfo = pikepdf.Dictionary(
                    Pages=pikepdf.Array([page.obj]),
                    DeviceColorant=pikepdf.String(name),
                )
            pdf.save(source, min_version="1.3")

        first = self._run("--format", "json", "list", source)
        second = self._run("--format", "json", "list", source)
        payload = self._success(first, command="list")
        self._success(second, command="list")

        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            [item["name"] for item in payload["result"]["colorants"]],
            sorted(names, key=lambda name: (name.casefold(), name)),
        )

    def test_text_default_is_unchanged_and_usage_is_no_longer_exit_two(self) -> None:
        implicit = self._run("list", self.source)
        explicit = self._run("--format", "text", "list", self.source)
        usage = self._run("list")
        help_result = self._run("--help")
        version = self._run("--version")
        json_help = self._run("--format", "json", "--help")
        json_version = self._run("--format", "json", "--version")

        self.assertEqual(
            (implicit.returncode, implicit.stdout, implicit.stderr),
            (explicit.returncode, explicit.stdout, explicit.stderr),
        )
        self.assertEqual(usage.returncode, 64)
        self.assertEqual(usage.stdout, "")
        self.assertIn("spotpdf list: error:", usage.stderr)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--format {text,json}", help_result.stdout)
        self.assertEqual(version.returncode, 0)
        self.assertRegex(version.stdout, r"^spotpdf \S+\n$")
        self.assertEqual(json_help.returncode, 0)
        self.assertTrue(json_help.stdout.startswith("usage:"))
        self.assertEqual(json_help.stderr, "")
        self.assertEqual(json_version.returncode, 0)
        self.assertRegex(json_version.stdout, r"^spotpdf \S+\n$")
        self.assertEqual(json_version.stderr, "")


if __name__ == "__main__":
    unittest.main()
