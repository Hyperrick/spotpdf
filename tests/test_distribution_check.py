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

    def _write_archives(
        self,
        wheel_metadata: bytes,
        sdist_metadata: bytes,
        *,
        wheel_marker: bool = True,
        source_marker: bool = True,
        source_marker_path: str = "spotpdf-0.6.0/src/spotpdf/py.typed",
        source_metadata_path: str = "spotpdf-0.6.0/PKG-INFO",
        extra_source_members: tuple[tuple[str, bytes], ...] = (),
    ) -> None:
        wheel = self.directory / "spotpdf-0.6.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as package:
            package.writestr("spotpdf/__init__.py", b"")
            if wheel_marker:
                package.writestr("spotpdf/py.typed", b"")
            package.writestr("spotpdf-0.6.0.dist-info/METADATA", wheel_metadata)

        source = self.directory / "spotpdf-0.6.0.tar.gz"
        with tarfile.open(source, "w:gz") as package:
            members = [
                (source_metadata_path, sdist_metadata),
                ("spotpdf-0.6.0/src/spotpdf/__init__.py", b""),
            ]
            if source_marker:
                members.append((source_marker_path, b""))
            members.extend(extra_source_members)
            for name, data in members:
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

    def test_requires_typing_marker_in_wheel_and_source_archive(self) -> None:
        valid = core_metadata(("Security", SECURITY_URL), ("Support", SUPPORT_URL))
        cases = (
            (False, True, ".whl"),
            (True, False, ".tar.gz"),
        )
        for wheel_marker, source_marker, archive_suffix in cases:
            with self.subTest(archive=archive_suffix):
                self._write_archives(
                    valid,
                    valid,
                    wheel_marker=wheel_marker,
                    source_marker=source_marker,
                )
                with self.assertRaises(SystemExit) as raised:
                    check_distributions(self.directory)
                message = str(raised.exception)
                self.assertIn(archive_suffix, message)
                self.assertIn("spotpdf/py.typed", message)

    def test_rejects_non_regular_typing_markers(self) -> None:
        valid = core_metadata(("Security", SECURITY_URL), ("Support", SUPPORT_URL))
        for archive_kind in ("wheel", "source"):
            with self.subTest(archive=archive_kind):
                self._write_archives(valid, valid)
                if archive_kind == "wheel":
                    wheel = self.directory / "spotpdf-0.6.0-py3-none-any.whl"
                    marker = zipfile.ZipInfo("spotpdf/py.typed")
                    marker.create_system = 3
                    marker.external_attr = (stat.S_IFLNK | 0o777) << 16
                    with zipfile.ZipFile(wheel, "w") as package:
                        package.writestr("spotpdf/__init__.py", b"")
                        package.writestr(marker, b"target")
                        package.writestr("spotpdf-0.6.0.dist-info/METADATA", valid)
                else:
                    source = self.directory / "spotpdf-0.6.0.tar.gz"
                    with tarfile.open(source, "w:gz") as package:
                        for name, data in (
                            ("spotpdf-0.6.0/PKG-INFO", valid),
                            ("spotpdf-0.6.0/src/spotpdf/__init__.py", b""),
                        ):
                            info = tarfile.TarInfo(name)
                            info.size = len(data)
                            package.addfile(info, io.BytesIO(data))
                        marker = tarfile.TarInfo("spotpdf-0.6.0/src/spotpdf/py.typed")
                        marker.type = tarfile.SYMTYPE
                        marker.linkname = "target"
                        package.addfile(marker)

                with self.assertRaisesRegex(SystemExit, "py[.]typed is not"):
                    check_distributions(self.directory)

    def test_source_marker_must_match_the_archive_root(self) -> None:
        valid = core_metadata(("Security", SECURITY_URL), ("Support", SUPPORT_URL))
        self._write_archives(
            valid,
            valid,
            source_marker_path="spotpdf-9.9.9/src/spotpdf/py.typed",
        )

        with self.assertRaisesRegex(SystemExit, "outside canonical root"):
            check_distributions(self.directory)

    def test_source_metadata_and_all_members_must_share_the_archive_root(self) -> None:
        valid = core_metadata(("Security", SECURITY_URL), ("Support", SUPPORT_URL))
        cases = (
            {
                "source_metadata_path": "different-root/PKG-INFO",
            },
            {
                "extra_source_members": (("different-root/README.md", b"foreign"),),
            },
        )
        for options in cases:
            with self.subTest(options=options):
                self._write_archives(valid, valid, **options)
                with self.assertRaisesRegex(SystemExit, "outside canonical root"):
                    check_distributions(self.directory)

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
