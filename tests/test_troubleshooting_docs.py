from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from scripts.release_readme import ReleaseReadmeError, validate_release_readme


class TroubleshootingDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[1]
        cls.guide = (cls.repository / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
        cls.normalized_guide = " ".join(cls.guide.split())
        cls.readme = (cls.repository / "README.md").read_text(encoding="utf-8")
        cls.support = (cls.repository / "SUPPORT.md").read_text(encoding="utf-8")
        cls.contributing = (cls.repository / "CONTRIBUTING.md").read_text(encoding="utf-8")
        cls.public_corpus = (cls.repository / "docs" / "public-corpus.md").read_text(
            encoding="utf-8"
        )

    def test_readme_and_support_route_users_to_the_guide(self) -> None:
        project = tomllib.loads((self.repository / "pyproject.toml").read_text(encoding="utf-8"))
        version = project["project"]["version"]
        # The guide first ships in v0.7.0. Before that bump, only main contains it;
        # the release validator must keep rejecting that temporary live ref.
        expected_ref = "main" if version == "0.6.0" else f"v{version}"
        expected_url = (
            f"https://github.com/Hyperrick/spotpdf/blob/{expected_ref}/docs/troubleshooting.md"
        )
        self.assertIn(f"[troubleshooting guide]({expected_url})", self.readme)
        self.assertIn("[troubleshooting guide](docs/troubleshooting.md)", self.support)

        if expected_ref == "main":
            with self.assertRaisesRegex(
                ReleaseReadmeError,
                r"must use v0\.6\.0.*docs/troubleshooting\.md",
            ):
                validate_release_readme(
                    self.repository / "README.md",
                    self.readme,
                    "v0.6.0",
                )
        else:
            self.assertEqual(
                validate_release_readme(
                    self.repository / "README.md",
                    self.readme,
                    f"v{version}",
                ),
                "pypi",
            )

    def test_required_scenarios_have_dedicated_sections(self) -> None:
        expected_headings = {
            "## Output already exists",
            "## A spot name is absent or does not match",
            "## The PDF is signed, encrypted, or modification-restricted",
            "## `unsupported_spot_use`",
            "## A processing budget is exceeded",
            "## `pdftoppm`, qpdf, or Ghostscript is missing",
        }
        self.assertTrue(expected_headings.issubset(set(self.guide.splitlines())))

    def test_output_and_exact_name_actions_are_documented(self) -> None:
        required_prose = (
            "output already exists (use --force)",
            "`--force` changes only the destination-collision policy.",
            "PDF names are exact and case-sensitive.",
        )
        for snippet in required_prose:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.normalized_guide)

        required_commands = (
            "spotpdf list input.pdf",
            'spotpdf check input.pdf --spot "Exact Name"',
        )
        for snippet in required_commands:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.guide)

    def test_document_restrictions_and_unsupported_semantics_have_no_bypass(self) -> None:
        required_snippets = (
            "signed PDFs are not modified",
            "encrypted PDFs are not supported",
            "the PDF permissions do not allow content modification",
            '`error.code: "unsupported_spot_use"`,',
            "DeviceN/NChannel",
            "Do not patch PDF objects or delete resource entries as a workaround.",
        )
        for snippet in required_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.normalized_guide)

        self.assertIn(
            "`--force` and higher processing budgets do not make unsupported semantics safe.",
            self.normalized_guide,
        )

    def test_budget_and_optional_tool_boundaries_are_documented(self) -> None:
        required_prose = (
            "processing budget exceeded:",
            '`error.code: "budget_exceeded"`',
            "application counters, not CPU/RAM isolation",
            "`pdftoppm`, qpdf, or Ghostscript is missing",
            "optional development and release tools",
            "do not invoke them",
        )
        for snippet in required_prose:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.normalized_guide)

        required_exact = (
            "--max-pages 20000",
            "../CONTRIBUTING.md#development-setup",
            "public-corpus.md#run-locally",
        )
        for snippet in required_exact:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.guide)

        self.assertIn("## Development setup", self.contributing.splitlines())
        self.assertIn("## Run locally", self.public_corpus.splitlines())


if __name__ == "__main__":
    unittest.main()
