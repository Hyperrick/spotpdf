from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.release_readme import ReleaseReadmeError, validate_release_readme


class ReleaseReadmeTests(unittest.TestCase):
    tag = "v1.2.3"

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.path = root / "README.md"
        docs = root / "docs"
        docs.mkdir()
        (docs / "usage.md").write_text("Usage\n", encoding="utf-8")
        (docs / "preview.png").write_bytes(b"preview")
        (root / "CONTRIBUTING.md").write_text("Contributing\n", encoding="utf-8")
        (root / "SUPPORT.md").write_text("Support\n", encoding="utf-8")
        (docs / "releasing.md").write_text("Releasing\n", encoding="utf-8")

    def _commands(self, channel: str = "git-tag") -> str:
        if channel == "git-tag":
            target = "git+https://github.com/Hyperrick/spotpdf.git@v1.2.3"
        else:
            target = "spotpdf==1.2.3"
        return (
            "```bash\n"
            f"python -m pip install {target}\n"
            f"uv tool install {target}\n"
            f"pipx install {target}\n"
            "```\n"
        )

    def test_accepts_git_tag_and_pypi_install_channels(self) -> None:
        for channel in ("git-tag", "pypi"):
            with self.subTest(channel=channel):
                text = self._commands(channel) + "[Docs](https://example.com/docs)\n"
                self.assertEqual(
                    validate_release_readme(self.path, text, self.tag),
                    channel,
                )

    def test_rejects_mixed_or_duplicate_install_channels(self) -> None:
        mixed = "```bash\n" + (
            "python -m pip install spotpdf==1.2.3\n"
            "uv tool install spotpdf==1.2.3\n"
            "pipx install git+https://github.com/Hyperrick/spotpdf.git@v1.2.3\n"
            "```\n"
        )
        with self.assertRaisesRegex(ReleaseReadmeError, "mixed channels"):
            validate_release_readme(self.path, mixed, self.tag)

        duplicate = self._commands() + (
            "```bash\nuv tool install git+https://github.com/Hyperrick/spotpdf.git@v1.2.3\n```\n"
        )
        with self.assertRaisesRegex(ReleaseReadmeError, "does not uniquely use"):
            validate_release_readme(self.path, duplicate, self.tag)

    def test_pip_install_command_must_be_current_and_unique(self) -> None:
        command = "python -m pip install spotpdf==1.2.3\n"
        invalid_texts = {
            "missing": self._commands("pypi").replace(command, ""),
            "stale": self._commands("pypi").replace("1.2.3", "1.2.2", 1),
            "duplicate": self._commands("pypi") + f"```bash\n{command}```\n",
        }
        for case, text in invalid_texts.items():
            with (
                self.subTest(case=case),
                self.assertRaisesRegex(ReleaseReadmeError, "install command"),
            ):
                validate_release_readme(self.path, text, self.tag)

    def test_rejects_repository_relative_markdown_and_html_targets(self) -> None:
        targets = (
            "[Docs](docs/usage.md)",
            "![Preview](/docs/preview.png)",
            "[Empty]()",
            "![Empty]()",
            '<a href="?download=1">Download</a>',
            '<img src="//cdn.example.com/preview.png">',
            '<a href="">Empty</a>',
            "<a href>Empty</a>",
        )
        for target in targets:
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(
                    ReleaseReadmeError,
                    "repository-relative",
                ),
            ):
                validate_release_readme(self.path, self._commands() + target, self.tag)

    def test_ignores_targets_in_code_and_allows_fragments(self) -> None:
        text = self._commands() + (
            "[Section](#section)\n"
            "`[Inline](relative.md)`\n"
            "```markdown\n[Example](relative.md)\n```\n"
        )
        self.assertEqual(validate_release_readme(self.path, text, self.tag), "git-tag")

    def test_hidden_install_commands_do_not_satisfy_release_gate(self) -> None:
        text = "<!--\n" + self._commands() + "-->\n"
        with self.assertRaisesRegex(ReleaseReadmeError, "install command"):
            validate_release_readme(self.path, text, self.tag)

    def test_project_content_urls_must_use_release_tag(self) -> None:
        patterns = (
            "https://github.com/Hyperrick/spotpdf/blob/main/docs/usage.md",
            "https://github.com/Hyperrick/spotpdf/raw/v1.2.2/docs/preview.png",
            "https://raw.githubusercontent.com/Hyperrick/spotpdf/main/docs/preview.png",
            "https://github.com/hyperrick/spotpdf/tree/main/docs",
            "https://github.com./Hyperrick/spotpdf/blob/main/docs/usage.md",
            "https://www.github.com/Hyperrick/spotpdf/blob/main/docs/usage.md",
            "https://github.com/Hyperrick/spotpdf/Blob/main/docs/usage.md",
        )
        for target in patterns:
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(
                    ReleaseReadmeError,
                    "must use v1.2.3",
                ),
            ):
                validate_release_readme(
                    self.path,
                    self._commands() + f"[Target]({target})\n",
                    self.tag,
                )

    def test_accepts_full_immutable_commit_links(self) -> None:
        commit = "a" * 40
        text = self._commands() + (
            f"[Docs](https://github.com/Hyperrick/spotpdf/blob/{commit}/docs/usage.md)\n"
            f"![Preview](https://raw.githubusercontent.com/Hyperrick/spotpdf/{commit}/docs/preview.png)\n"
        )
        self.assertEqual(validate_release_readme(self.path, text, self.tag), "git-tag")
        with self.assertRaises(ReleaseReadmeError):
            validate_release_readme(self.path, text.replace(commit, commit[:7]), self.tag)

    def test_accepts_tag_bound_project_links_and_images(self) -> None:
        text = self._commands() + (
            "[Docs](https://github.com/Hyperrick/spotpdf/blob/v1.2.3/docs/usage.md)\n"
            "![Preview](https://raw.githubusercontent.com/Hyperrick/spotpdf/"
            "v1.2.3/docs/preview.png)\n"
            "[Docs tree](https://github.com/Hyperrick/spotpdf/tree/v1.2.3/docs)\n"
        )
        self.assertEqual(validate_release_readme(self.path, text, self.tag), "git-tag")

    def test_allows_live_support_and_contribution_policies(self) -> None:
        text = self._commands() + (
            "[Support](https://github.com/Hyperrick/spotpdf/blob/main/SUPPORT.md)\n"
            "[Contributing](https://github.com/Hyperrick/spotpdf/blob/main/CONTRIBUTING.md)\n"
            "[Releasing](https://github.com/Hyperrick/spotpdf/blob/main/docs/releasing.md)\n"
        )
        self.assertEqual(validate_release_readme(self.path, text, self.tag), "git-tag")

    def test_project_content_requires_canonical_https(self) -> None:
        targets = (
            "http://github.com/Hyperrick/spotpdf/blob/v1.2.3/docs/usage.md",
            "https://github.com./Hyperrick/spotpdf/blob/v1.2.3/docs/usage.md",
            "https://www.github.com/Hyperrick/spotpdf/blob/v1.2.3/docs/usage.md",
        )
        for target in targets:
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(
                    ReleaseReadmeError,
                    "canonical HTTPS",
                ),
            ):
                validate_release_readme(
                    self.path,
                    self._commands() + f"[Docs]({target})\n",
                    self.tag,
                )

    def test_rejects_missing_or_misrouted_project_content(self) -> None:
        targets = (
            "[Missing](https://github.com/Hyperrick/spotpdf/blob/v1.2.3/docs/missing.md)",
            "![Blob](https://github.com/Hyperrick/spotpdf/blob/v1.2.3/docs/preview.png)",
            "[Raw](https://raw.githubusercontent.com/Hyperrick/spotpdf/v1.2.3/docs/usage.md)",
            "[No path](https://github.com/Hyperrick/spotpdf/blob/v1.2.3)",
            "[Absolute](https://github.com/Hyperrick/spotpdf/blob/v1.2.3/%2Fetc/passwd)",
        )
        for target in targets:
            with self.subTest(target=target), self.assertRaises(ReleaseReadmeError):
                validate_release_readme(self.path, self._commands() + target, self.tag)

    def test_rejects_html_srcset(self) -> None:
        text = self._commands() + '<img src="https://example.com/a.png" srcset="a.png 2x">\n'
        with self.assertRaisesRegex(ReleaseReadmeError, "srcset is not supported"):
            validate_release_readme(self.path, text, self.tag)

    def test_requires_exact_stable_tag(self) -> None:
        with self.assertRaisesRegex(ReleaseReadmeError, "must match vX.Y.Z"):
            validate_release_readme(self.path, self._commands(), "v1.2.3-rc1")


if __name__ == "__main__":
    unittest.main()
