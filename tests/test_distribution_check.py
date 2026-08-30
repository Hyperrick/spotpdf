from __future__ import annotations

import io
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.check_distribution import check_distributions

SUPPORT_URL = "https://github.com/Hyperrick/spotpdf/blob/main/SUPPORT.md"
SECURITY_URL = "https://github.com/Hyperrick/spotpdf/security/policy"


def core_metadata(*project_urls: tuple[str, str]) -> bytes:
    headers = [
        "Metadata-Version: 2.4",
        "Name: spotpdf",
        "Version: 0.6.0",
        *(f"Project-URL: {label}, {url}" for label, url in project_urls),
        "",
        "Package description.",
    ]
    return "\n".join(headers).encode()


class DistributionCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def _write_archives(self, wheel_metadata: bytes, sdist_metadata: bytes) -> None:
        wheel = self.directory / "spotpdf-0.6.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as package:
            package.writestr("spotpdf/__init__.py", b"")
            package.writestr("spotpdf-0.6.0.dist-info/METADATA", wheel_metadata)

        source = self.directory / "spotpdf-0.6.0.tar.gz"
        with tarfile.open(source, "w:gz") as package:
            for name, data in (
                ("spotpdf-0.6.0/PKG-INFO", sdist_metadata),
                ("spotpdf-0.6.0/src/spotpdf/__init__.py", b""),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                package.addfile(info, io.BytesIO(data))

    def test_accepts_exact_support_and_security_urls_in_both_archives(self) -> None:
        metadata = core_metadata(
            ("Documentation", "https://github.com/Hyperrick/spotpdf#readme"),
            ("Security", SECURITY_URL),
            ("Support", SUPPORT_URL),
        )
        self._write_archives(metadata, metadata)

        check_distributions(self.directory)

    def test_requires_each_url_in_the_wheel_and_source_archive(self) -> None:
        valid = core_metadata(("Security", SECURITY_URL), ("Support", SUPPORT_URL))
        invalid_cases = (
            (core_metadata(("Security", SECURITY_URL)), valid, ".whl", "Support"),
            (valid, core_metadata(("Support", SUPPORT_URL)), ".tar.gz", "Security"),
            (
                core_metadata(
                    ("Security", SECURITY_URL),
                    ("Support", "https://example.invalid/support"),
                ),
                valid,
                ".whl",
                "Support",
            ),
        )
        for wheel, source, archive_suffix, label in invalid_cases:
            with self.subTest(archive=archive_suffix, label=label):
                self._write_archives(wheel, source)
                with self.assertRaises(SystemExit) as raised:
                    check_distributions(self.directory)
                message = str(raised.exception)
                self.assertIn(archive_suffix, message)
                self.assertIn(f"Project-URL {label!r}", message)

    def test_rejects_duplicate_canonical_project_url(self) -> None:
        duplicated = core_metadata(
            ("Security", SECURITY_URL),
            ("Support", SUPPORT_URL),
            ("Support", SUPPORT_URL),
        )
        valid = core_metadata(("Security", SECURITY_URL), ("Support", SUPPORT_URL))
        self._write_archives(duplicated, valid)

        with self.assertRaisesRegex(SystemExit, "must occur exactly once"):
            check_distributions(self.directory)

    def test_rejects_normalized_project_url_label_collisions(self) -> None:
        colliding = core_metadata(
            ("Security", SECURITY_URL),
            ("Support", SUPPORT_URL),
            ("SUP-port", "https://example.invalid/other-support"),
        )
        valid = core_metadata(("Security", SECURITY_URL), ("Support", SUPPORT_URL))
        self._write_archives(colliding, valid)

        with self.assertRaisesRegex(SystemExit, "labels collide after normalization"):
            check_distributions(self.directory)

    def test_rejects_non_regular_wheel_metadata(self) -> None:
        valid = core_metadata(("Security", SECURITY_URL), ("Support", SUPPORT_URL))
        for entry_type in ("directory", "symlink"):
            with self.subTest(entry_type=entry_type):
                self._write_archives(valid, valid)
                wheel = self.directory / "spotpdf-0.6.0-py3-none-any.whl"
                name = "spotpdf-0.6.0.dist-info/METADATA"
                if entry_type == "directory":
                    name += "/"
                info = zipfile.ZipInfo(name)
                if entry_type == "symlink":
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                with zipfile.ZipFile(wheel, "w") as package:
                    package.writestr(info, valid)

                with self.assertRaisesRegex(SystemExit, "not an unencrypted regular file"):
                    check_distributions(self.directory)


if __name__ == "__main__":
    unittest.main()
