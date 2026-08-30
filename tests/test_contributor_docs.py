from __future__ import annotations

import unittest
from pathlib import Path


class ContributorDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[1]
        path = cls.repository / "CONTRIBUTING.md"
        cls.contributing = path.read_text(encoding="utf-8")
        cls.normalized_contributing = " ".join(cls.contributing.split())

    def test_poppler_install_and_verification_commands_are_documented(self) -> None:
        required_snippets = (
            "brew install poppler",
            "sudo apt-get install --yes poppler-utils",
            "command -v pdftoppm",
            "Get-Command pdftoppm -ErrorAction Stop",
            "pdftoppm -v",
        )
        for snippet in required_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.contributing)

        documentation_gate = self.contributing.index("scripts/create_docs_images.py")
        for verification_command in (
            "command -v pdftoppm",
            "Get-Command pdftoppm -ErrorAction Stop",
        ):
            with self.subTest(verification_command=verification_command):
                self.assertLess(self.contributing.index(verification_command), documentation_gate)

    def test_render_tool_scopes_remain_distinct(self) -> None:
        expected_statements = (
            "Poppler alone covers the documentation-image gate and rename render comparison.",
            "The convert render/plate comparison also requires qpdf and Ghostscript; "
            "pull-request CI runs it in the Linux Python 3.13 test job.",
            "The maintainer-only public corpus gate uses the same three tools and "
            "runs for releases.",
        )
        for statement in expected_statements:
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized_contributing)

    def test_public_corpus_setup_link_resolves_to_run_locally_section(self) -> None:
        self.assertIn("docs/public-corpus.md#run-locally", self.contributing)
        public_corpus = (self.repository / "docs" / "public-corpus.md").read_text(encoding="utf-8")
        self.assertIn("## Run locally", public_corpus.splitlines())


if __name__ == "__main__":
    unittest.main()
