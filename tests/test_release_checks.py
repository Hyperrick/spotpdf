from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.check_release import (
    ReleaseCheckError,
    prepare_release_assets,
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
            f"## [{changelog_version}] - 2026-08-30\n\n"
            "Release notes.\n\n"
            "## [0.2.1] - 2026-08-29\n\n"
            "Previous release.\n\n"
            f"[Unreleased]: https://github.com/Hyperrick/spotpdf/compare/v{changelog_version}...HEAD\n"
            f"[{changelog_version}]: https://github.com/Hyperrick/spotpdf/compare/"
            f"v0.2.1...v{changelog_version}\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            "uv tool install "
            f"git+https://github.com/Hyperrick/spotpdf.git@{readme_tag}\n"
            "pipx install "
            f"git+https://github.com/Hyperrick/spotpdf.git@{readme_tag}\n",
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
        self._write_metadata(changelog_version="0.2.1")
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
        with self.assertRaisesRegex(ReleaseCheckError, "install commands"):
            validate_release_metadata(self.root, "v0.3.0")


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
