from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.check_release import (
    ReleaseCheckError,
    prepare_release_assets,
    render_release_notes,
    validate_release_metadata,
    verify_release_assets,
)


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self._write_metadata()

    def _write_metadata(
        self,
        *,
        project_version: str = "0.3.0",
        locked_version: str = "0.3.0",
        changelog_version: str = "0.3.0",
        readme_tag: str = "v0.3.0",
    ) -> None:
        (self.root / "pyproject.toml").write_text(
            f'[project]\nname = "spotpdf"\nversion = "{project_version}"\n',
            encoding="utf-8",
        )
        (self.root / "uv.lock").write_text(
            f'[[package]]\nname = "spotpdf"\nversion = "{locked_version}"\n',
            encoding="utf-8",
        )
        (self.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            f"## [{changelog_version}] - 2026-08-30\n\n"
            "### Added\n\n"
            "Release notes.\n\n"
            "## [0.2.1] - 2026-08-29\n\n"
            "### Added\n\n"
            "Previous release.\n\n"
            f"[Unreleased]: https://github.com/Hyperrick/spotpdf/compare/v{changelog_version}...HEAD\n"
            f"[{changelog_version}]: https://github.com/Hyperrick/spotpdf/compare/"
            f"v0.2.1...v{changelog_version}\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            "```bash\n"
            "uv tool install "
            f"git+https://github.com/Hyperrick/spotpdf.git@{readme_tag}\n"
            "pipx install "
            f"git+https://github.com/Hyperrick/spotpdf.git@{readme_tag}\n"
            "```\n"
            f"Stable {readme_tag} contains the documented commands.\n"
            f'{{"spotpdf_version":"{project_version}"}}\n'
            "For development, clone the repository.\n",
            encoding="utf-8",
        )
        docs = self.root / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "json-output.md").write_text(
            f'# JSON output\n\n{{"spotpdf_version":"{project_version}"}}\n',
            encoding="utf-8",
        )
        (docs / "processing-budgets.md").write_text(
            "# Processing budgets\n\nPublished budget contract.\n",
            encoding="utf-8",
        )
        (self.root / "SECURITY.md").write_text(
            "# Security\n\nPublished security contract.\n",
            encoding="utf-8",
        )
        template = self.root / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(
            f'placeholder: "spotpdf {project_version}"\n',
            encoding="utf-8",
        )

    def test_valid_metadata_returns_version(self) -> None:
        self.assertEqual(validate_release_metadata(self.root, "v0.3.0"), "0.3.0")

    def test_tag_must_be_exact_stable_semver(self) -> None:
        for tag in (
            "0.3.0",
            "v0.3",
            "v0.3.0-rc1",
            "v01.2.3",
            "v0.3.0/other",
            "v1.2.3\nextra",
        ):
            with self.subTest(tag=tag), self.assertRaises(ReleaseCheckError):
                validate_release_metadata(self.root, tag)

    def test_tag_must_match_project_version(self) -> None:
        with self.assertRaisesRegex(ReleaseCheckError, "does not match project version"):
            validate_release_metadata(self.root, "v0.3.1")

    def test_lockfile_must_match_project_version(self) -> None:
        self._write_metadata(locked_version="0.2.1")
        with self.assertRaisesRegex(ReleaseCheckError, "uv.lock project version"):
            validate_release_metadata(self.root, "v0.3.0")

    def test_changelog_must_have_dated_release_and_updated_comparison(self) -> None:
        self._write_metadata(changelog_version="0.2.2")
        with self.assertRaisesRegex(ReleaseCheckError, "missing dated 0.3.0 section"):
            validate_release_metadata(self.root, "v0.3.0")

        self._write_metadata()
        changelog = self.root / "CHANGELOG.md"
        changelog.write_text(
            changelog.read_text(encoding="utf-8").replace("2026-08-30", "2026-99-99"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "invalid 0.3.0 release date"):
            validate_release_metadata(self.root, "v0.3.0")

        self._write_metadata()
        changelog.write_text(
            changelog.read_text(encoding="utf-8").replace("2026-08-29", "2026-99-99"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "invalid 0.2.1 release date"):
            validate_release_metadata(self.root, "v0.3.0")

        self._write_metadata()
        changelog.write_text(
            changelog.read_text(encoding="utf-8").replace(
                "compare/v0.2.1...v0.3.0",
                "compare/v0.2.0...v0.3.0",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "release comparison"):
            validate_release_metadata(self.root, "v0.3.0")

    def test_readme_must_have_both_stable_install_commands(self) -> None:
        self._write_metadata(readme_tag="v0.2.1")
        with self.assertRaisesRegex(ReleaseCheckError, "install command"):
            validate_release_metadata(self.root, "v0.3.0")

        self._write_metadata()
        readme = self.root / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8")
            + "```bash\nuv tool install "
            + "git+https://github.com/Hyperrick/spotpdf.git@v0.2.1\n```\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "does not uniquely use"):
            validate_release_metadata(self.root, "v0.3.0")

    def test_readme_rejects_stale_stable_prose_despite_current_commands(self) -> None:
        claims = (
            "Stable v0.2.1 contains an older command set.\n",
            "Stable **v0.2.1** contains an older command set.\n",
            "Stable *v0.2.1* contains an older command set.\n",
            "Stable __v0.2.1__ contains an older command set.\n",
            "Stable `v0.2.1` contains an older command set.\n",
            "Stable [v0.2.1](https://example.com/release) contains an older command set.\n",
            "Stable\nv0.2.1 contains an older command set.\n",
        )
        for claim in claims:
            with self.subTest(claim=claim):
                self._write_metadata()
                readme = self.root / "README.md"
                readme.write_text(
                    readme.read_text(encoding="utf-8") + claim,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ReleaseCheckError, "stable release prose"):
                    validate_release_metadata(self.root, "v0.3.0")

    def test_readme_rejects_nonstable_version_suffixes(self) -> None:
        for token in (
            "v0.3.0-rc1",
            "v0.3.0-rc1+build.1",
            "v0.3.0.1",
            "v0.3.0+build.1",
            "v0.3.0/rc1",
        ):
            with self.subTest(token=token):
                self._write_metadata()
                readme = self.root / "README.md"
                readme.write_text(
                    readme.read_text(encoding="utf-8")
                    + f"Stable {token} contains an unreleased interface.\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ReleaseCheckError, "non-stable version tokens"):
                    validate_release_metadata(self.root, "v0.3.0")

    def test_readme_rejects_development_only_release_claims(self) -> None:
        claims = (
            "The current development branch additionally contains convert.\n",
            "These controls are not included in the stable v0.3.0 release.\n",
            "The convert command is available only on the development branch.\n",
            "The convert command is available only on the development\nbranch.\n",
        )
        for claim in claims:
            with self.subTest(claim=claim):
                self._write_metadata()
                readme = self.root / "README.md"
                readme.write_text(
                    readme.read_text(encoding="utf-8") + claim,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ReleaseCheckError, "development-only"):
                    validate_release_metadata(self.root, "v0.3.0")

    def test_readme_development_claim_filter_avoids_unrelated_stable_wording(self) -> None:
        readme = self.root / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8")
            + "A cache entry is not included in stable ordering.\n"
            + "Debug symbols are not included in the stable release wheel.\n",
            encoding="utf-8",
        )
        self.assertEqual(validate_release_metadata(self.root, "v0.3.0"), "0.3.0")

    def test_readme_requires_stable_prose_but_allows_development_setup(self) -> None:
        readme = self.root / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(
            text.replace("Stable v0.3.0 contains the documented commands.\n", ""),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "missing stable release prose"):
            validate_release_metadata(self.root, "v0.3.0")

        valid_claims = (
            "Stable *v0.3.0* contains the documented commands.\n",
            "Stable __v0.3.0__ contains the documented commands.\n",
            "Stable [v0.3.0](https://example.com/release) contains the commands.\n",
            "Stable\nv0.3.0 contains the documented commands.\n",
        )
        for claim in valid_claims:
            with self.subTest(claim=claim):
                self._write_metadata()
                readme.write_text(
                    readme.read_text(encoding="utf-8").replace(
                        "Stable v0.3.0 contains the documented commands.\n",
                        claim,
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(validate_release_metadata(self.root, "v0.3.0"), "0.3.0")

    def test_bug_report_placeholder_must_match_release(self) -> None:
        template = self.root / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
        template.write_text('placeholder: "spotpdf 0.2.1"\n', encoding="utf-8")
        with self.assertRaisesRegex(ReleaseCheckError, "bug-report version placeholder"):
            validate_release_metadata(self.root, "v0.3.0")

    def test_release_documents_reject_development_only_claims(self) -> None:
        claims = (
            ("docs/json-output.md", "This requires the next release.\n"),
            ("docs/processing-budgets.md", "Available on the current development branch.\n"),
            ("SECURITY.md", "The development CLI's JSON mode escapes names.\n"),
        )
        for relative_path, claim in claims:
            with self.subTest(path=relative_path):
                self._write_metadata()
                path = self.root / relative_path
                path.write_text(path.read_text(encoding="utf-8") + claim, encoding="utf-8")
                with self.assertRaisesRegex(ReleaseCheckError, "development-only"):
                    validate_release_metadata(self.root, "v0.3.0")

    def test_release_documents_bind_json_examples_to_package_version(self) -> None:
        mutations = (
            lambda text: text.replace(
                '"spotpdf_version":"0.3.0"',
                '"spotpdf_version":"0.2.1"',
            ),
            lambda text: text + '{"spotpdf_version":"development"}\n',
        )
        for relative_path in ("README.md", "docs/json-output.md"):
            for mutation in mutations:
                with self.subTest(path=relative_path, mutation=mutation):
                    self._write_metadata()
                    path = self.root / relative_path
                    path.write_text(
                        mutation(path.read_text(encoding="utf-8")),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ReleaseCheckError, "spotpdf_version examples"):
                        validate_release_metadata(self.root, "v0.3.0")

    def test_release_notes_are_exactly_the_current_dated_section(self) -> None:
        self.assertEqual(
            render_release_notes(self.root, "0.3.0"),
            "### Added\n\n"
            "Release notes.\n\n"
            "[Full changes: v0.2.1...v0.3.0](https://github.com/Hyperrick/"
            "spotpdf/compare/v0.2.1...v0.3.0)\n",
        )

    def test_release_notes_reject_duplicate_or_empty_sections(self) -> None:
        changelog = self.root / "CHANGELOG.md"
        original = changelog.read_text(encoding="utf-8")
        duplicate = "## [0.3.0] - 2026-08-30\n\n### Fixed\n\nDuplicate.\n\n"
        changelog.write_text(
            original.replace("## [Unreleased]\n\n", f"## [Unreleased]\n\n{duplicate}")
        )
        with self.assertRaisesRegex(ReleaseCheckError, "duplicate dated 0.3.0"):
            render_release_notes(self.root, "0.3.0")

        self._write_metadata()
        changelog.write_text(
            changelog.read_text(encoding="utf-8").replace(
                "### Added\n\nRelease notes.\n\n",
                "",
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "has no release notes"):
            render_release_notes(self.root, "0.3.0")

    def test_release_notes_require_empty_leading_unreleased_section(self) -> None:
        changelog = self.root / "CHANGELOG.md"
        text = changelog.read_text(encoding="utf-8")
        changelog.write_text(
            text.replace(
                "## [Unreleased]\n\n",
                "## [Unreleased]\n\n### Fixed\n\nForgotten note.\n\n",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "must be empty"):
            render_release_notes(self.root, "0.3.0")

        self._write_metadata()
        text = changelog.read_text(encoding="utf-8").replace("## [Unreleased]\n\n", "")
        changelog.write_text(
            text.replace(
                "## [0.2.1] - 2026-08-29",
                "## [Unreleased]\n\n## [0.2.1] - 2026-08-29",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "must immediately precede"):
            render_release_notes(self.root, "0.3.0")

    def test_release_notes_reject_malformed_level_two_sections(self) -> None:
        for indentation in ("", " ", "   "):
            with self.subTest(indentation=repr(indentation)):
                self._write_metadata()
                changelog = self.root / "CHANGELOG.md"
                changelog.write_text(
                    changelog.read_text(encoding="utf-8").replace(
                        "## [0.2.1] - 2026-08-29",
                        f"{indentation}## [0.2.2] - TBD\n\n"
                        "### Added\n\nMalformed release.\n\n"
                        "## [0.2.1] - 2026-08-29",
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ReleaseCheckError, "malformed or unexpected"):
                    render_release_notes(self.root, "0.3.0")

    def test_release_notes_allow_level_two_text_inside_fenced_code(self) -> None:
        changelog = self.root / "CHANGELOG.md"
        changelog.write_text(
            changelog.read_text(encoding="utf-8").replace(
                "Release notes.\n\n",
                "Release notes.\n\n```text\n## Debug helper\n```\n\n",
            ),
            encoding="utf-8",
        )
        notes = render_release_notes(self.root, "0.3.0")
        self.assertIn("```text\n## Debug helper\n```", notes)

    def test_unclosed_fenced_code_is_rejected_in_changelog_and_readme(self) -> None:
        changelog = self.root / "CHANGELOG.md"
        changelog.write_text(
            changelog.read_text(encoding="utf-8").replace(
                "## [0.2.1] - 2026-08-29",
                "```text\n## Debug helper\n## [0.2.1] - 2026-08-29",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "unclosed Markdown fenced"):
            render_release_notes(self.root, "0.3.0")

        self._write_metadata()
        readme = self.root / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "```text\nStable v0.2.1\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "unclosed Markdown fenced"):
            validate_release_metadata(self.root, "v0.3.0")

    def test_release_notes_require_a_real_heading_outside_fenced_code(self) -> None:
        changelog = self.root / "CHANGELOG.md"
        changelog.write_text(
            changelog.read_text(encoding="utf-8").replace(
                "### Added\n\nRelease notes.\n\n",
                "```markdown\n### Added\n```\n\n",
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "has no release notes"):
            render_release_notes(self.root, "0.3.0")

    def test_fenced_reference_cannot_replace_real_changelog_reference(self) -> None:
        cases = (
            (
                "[0.3.0]: https://github.com/Hyperrick/spotpdf/compare/v0.2.1...v0.3.0\n",
                "release comparison",
            ),
            (
                "[Unreleased]: https://github.com/Hyperrick/spotpdf/compare/v0.3.0...HEAD\n",
                "Unreleased comparison",
            ),
        )
        for reference, message in cases:
            with self.subTest(reference=reference):
                self._write_metadata()
                changelog = self.root / "CHANGELOG.md"
                text = changelog.read_text(encoding="utf-8").replace(reference, "")
                changelog.write_text(
                    text.replace(
                        "Release notes.\n\n",
                        f"Release notes.\n\n```text\n{reference}```\n\n",
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ReleaseCheckError, message):
                    render_release_notes(self.root, "0.3.0")

    def test_invalid_backtick_fence_info_does_not_hide_a_heading(self) -> None:
        changelog = self.root / "CHANGELOG.md"
        changelog.write_text(
            changelog.read_text(encoding="utf-8").replace(
                "## [0.2.1] - 2026-08-29",
                "```text`invalid\n## [0.2.2] - TBD\nVisible malformed release.\n\n"
                "## [0.2.1] - 2026-08-29",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "malformed or unexpected"):
            render_release_notes(self.root, "0.3.0")


class ReleaseAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name)
        self.wheel = self.dist / "spotpdf-0.3.0-py3-none-any.whl"
        self.sdist = self.dist / "spotpdf-0.3.0.tar.gz"
        self.wheel.write_bytes(b"wheel")
        self.sdist.write_bytes(b"source")

    def test_prepare_writes_and_verifies_exact_checksums(self) -> None:
        checksum_path = prepare_release_assets(self.dist, "0.3.0")
        expected = (
            f"{hashlib.sha256(b'wheel').hexdigest()}  {self.wheel.name}\n"
            f"{hashlib.sha256(b'source').hexdigest()}  {self.sdist.name}\n"
        )
        self.assertEqual(checksum_path.read_text(encoding="ascii"), expected)
        verify_release_assets(self.dist, "0.3.0")

    def test_prepare_allows_uv_build_marker_but_verify_does_not_publish_it(self) -> None:
        marker = self.dist / ".gitignore"
        marker.write_bytes(b"*")
        prepare_release_assets(self.dist, "0.3.0")
        marker.unlink()
        verify_release_assets(self.dist, "0.3.0")

    def test_prepare_rejects_modified_uv_build_marker(self) -> None:
        (self.dist / ".gitignore").write_text("keep-secrets.txt\n", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseCheckError, "unexpected uv build marker"):
            prepare_release_assets(self.dist, "0.3.0")

    def test_prepare_rejects_missing_extra_empty_and_wrong_version_assets(self) -> None:
        cases = ("missing", "extra", "empty", "wrong-version")
        for case in cases:
            with self.subTest(case=case):
                self.wheel.write_bytes(b"wheel")
                self.sdist.write_bytes(b"source")
                extra = self.dist / "unexpected.txt"
                wrong = self.dist / "spotpdf-0.2.1.tar.gz"
                extra.unlink(missing_ok=True)
                wrong.unlink(missing_ok=True)
                if case == "missing":
                    self.sdist.unlink()
                elif case == "extra":
                    extra.write_text("private", encoding="utf-8")
                elif case == "empty":
                    self.wheel.write_bytes(b"")
                else:
                    self.sdist.rename(wrong)
                with self.assertRaises(ReleaseCheckError):
                    prepare_release_assets(self.dist, "0.3.0")

    def test_verify_rejects_tampering_and_unexpected_checksum_entries(self) -> None:
        checksum_path = prepare_release_assets(self.dist, "0.3.0")
        self.wheel.write_bytes(b"tampered")
        with self.assertRaisesRegex(ReleaseCheckError, "does not exactly match"):
            verify_release_assets(self.dist, "0.3.0")

        self.wheel.write_bytes(b"wheel")
        prepare_release_assets(self.dist, "0.3.0")
        checksum_path.write_text(
            checksum_path.read_text(encoding="ascii") + f"{'0' * 64}  extra.txt\n",
            encoding="ascii",
        )
        with self.assertRaisesRegex(ReleaseCheckError, "exactly one line"):
            verify_release_assets(self.dist, "0.3.0")

    def test_symlink_assets_are_rejected(self) -> None:
        self.wheel.unlink()
        try:
            self.wheel.symlink_to(self.sdist)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with self.assertRaisesRegex(ReleaseCheckError, "regular file"):
            prepare_release_assets(self.dist, "0.3.0")


if __name__ == "__main__":
    unittest.main()
