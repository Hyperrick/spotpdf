from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.check_pypi_readme import ReadmeCheckError, check_pypi_readmes


class PyPIReadmeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _metadata(
        self,
        description: str = "# spotpdf\n\nSafe PDF spot-color tooling.\n",
        *,
        content_type: str | None = "text/markdown",
    ) -> bytes:
        headers = ["Metadata-Version: 2.4", "Name: spotpdf", "Version: 0.6.0"]
        if content_type is not None:
            headers.append(f"Description-Content-Type: {content_type}")
        return ("\n".join(headers) + "\n\n" + description).encode()

    def _wheel(
        self,
        metadata: bytes,
        *,
        directory_collision: bool = False,
        duplicate: bool = False,
        extra_members: int = 0,
    ) -> Path:
        path = self.root / "spotpdf-0.6.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as package:
            package.writestr("spotpdf-0.6.0.dist-info/METADATA", metadata)
            if directory_collision:
                package.writestr("spotpdf-0.6.0.dist-info/METADATA/", b"")
            if duplicate:
                package.writestr("other-0.6.0.dist-info/METADATA", metadata)
            for index in range(extra_members):
                package.writestr(f"extra-{index}.txt", b"fixture")
        return path

    def _sdist(
        self,
        metadata: bytes,
        *,
        extra_members: int = 0,
        pax_comment_size: int = 0,
        symlink_collision: bool = False,
    ) -> Path:
        path = self.root / "spotpdf-0.6.0.tar.gz"
        with tarfile.open(path, "w:gz") as package:
            info = tarfile.TarInfo("spotpdf-0.6.0/PKG-INFO")
            info.size = len(metadata)
            if pax_comment_size:
                info.pax_headers["comment"] = "x" * pax_comment_size
            package.addfile(info, io.BytesIO(metadata))
            nested = tarfile.TarInfo("spotpdf-0.6.0/src/spotpdf.egg-info/PKG-INFO")
            nested.size = len(metadata)
            package.addfile(nested, io.BytesIO(metadata))
            if symlink_collision:
                symlink = tarfile.TarInfo("spotpdf-0.6.0/PKG-INFO")
                symlink.type = tarfile.SYMTYPE
                symlink.linkname = "elsewhere"
                package.addfile(symlink)
            for index in range(extra_members):
                payload = b"fixture"
                extra = tarfile.TarInfo(f"spotpdf-0.6.0/extra-{index}.txt")
                extra.size = len(payload)
                package.addfile(extra, io.BytesIO(payload))
        return path

    def _pair(self, metadata: bytes | None = None) -> tuple[Path, Path]:
        raw = metadata or self._metadata()
        return self._wheel(raw), self._sdist(raw)

    def test_renders_matching_packaged_descriptions(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def render(raw: str, **parameters: str) -> str:
            calls.append((raw, parameters))
            return "<h1>spotpdf</h1>"

        output = io.StringIO()
        with redirect_stdout(output):
            readmes = check_pypi_readmes(self._pair(), renderer=render)

        self.assertEqual(len(readmes), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertIn("rendered successfully", output.getvalue())

    def test_passes_markdown_variant_to_renderer(self) -> None:
        wheel, source = self._pair(self._metadata(content_type="text/markdown; variant=CommonMark"))
        parameters: list[dict[str, str]] = []

        def render(raw: str, **options: str) -> str:
            parameters.append(options)
            return "<h1>spotpdf</h1>"

        with redirect_stdout(io.StringIO()):
            check_pypi_readmes((wheel, source), renderer=render)

        self.assertEqual(parameters, [{"variant": "CommonMark"}] * 2)

    def test_requires_exactly_one_wheel_and_source_archive(self) -> None:
        wheel, source = self._pair()
        for paths in ((wheel,), (source,), (wheel, source, wheel)):
            with (
                self.subTest(paths=paths),
                self.assertRaisesRegex(
                    ReadmeCheckError, "exactly one wheel and one source archive"
                ),
            ):
                check_pypi_readmes(paths, renderer=lambda raw, **params: "<p>ok</p>")

    def test_rejects_unsupported_archive(self) -> None:
        wheel, source = self._pair()
        unsupported = self.root / "spotpdf.zip"
        unsupported.write_bytes(b"not a distribution")
        with self.assertRaisesRegex(ReadmeCheckError, "unsupported distribution"):
            check_pypi_readmes(
                (wheel, source, unsupported),
                renderer=lambda raw, **params: "<p>ok</p>",
            )

    def test_rejects_ambiguous_metadata_member(self) -> None:
        raw = self._metadata()
        wheel = self._wheel(raw, duplicate=True)
        source = self._sdist(raw)
        with self.assertRaisesRegex(ReadmeCheckError, "exactly one top-level METADATA"):
            check_pypi_readmes(
                (wheel, source),
                renderer=lambda description, **params: "<p>ok</p>",
            )

    def test_rejects_metadata_path_type_collisions(self) -> None:
        raw = self._metadata()
        with self.subTest(archive="wheel"):
            wheel = self._wheel(raw, directory_collision=True)
            source = self._sdist(raw)
            with self.assertRaisesRegex(ReadmeCheckError, "top-level METADATA"):
                check_pypi_readmes(
                    (wheel, source),
                    renderer=lambda description, **params: "<p>ok</p>",
                )

        with self.subTest(archive="sdist"):
            wheel = self._wheel(raw)
            source = self._sdist(raw, symlink_collision=True)
            with self.assertRaisesRegex(ReadmeCheckError, "top-level PKG-INFO"):
                check_pypi_readmes(
                    (wheel, source),
                    renderer=lambda description, **params: "<p>ok</p>",
                )

    def test_rejects_member_floods_before_metadata_rendering(self) -> None:
        raw = self._metadata()
        with self.subTest(archive="wheel"):
            wheel = self._wheel(raw, extra_members=2)
            source = self._sdist(raw)
            with (
                patch("scripts.check_pypi_readme.MAX_ARCHIVE_MEMBERS", 2),
                self.assertRaisesRegex(ReadmeCheckError, "archive has 3 members"),
            ):
                check_pypi_readmes(
                    (wheel, source),
                    renderer=lambda description, **params: "<p>ok</p>",
                )

        with self.subTest(archive="sdist"):
            wheel = self._wheel(raw)
            source = self._sdist(raw, extra_members=1)
            with (
                patch("scripts.check_pypi_readme.MAX_ARCHIVE_MEMBERS", 2),
                self.assertRaisesRegex(ReadmeCheckError, "more than 2 members"),
            ):
                check_pypi_readmes(
                    (wheel, source),
                    renderer=lambda description, **params: "<p>ok</p>",
                )

    def test_rejects_tar_pax_data_over_decompressed_budget(self) -> None:
        raw = self._metadata()
        wheel = self._wheel(raw)
        source = self._sdist(raw, pax_comment_size=8_192)
        with (
            patch("scripts.check_pypi_readme.MAX_DECOMPRESSED_TAR_BYTES", 4_096),
            self.assertRaisesRegex(ReadmeCheckError, "decompressed TAR data exceeds"),
        ):
            check_pypi_readmes(
                (wheel, source),
                renderer=lambda description, **params: "<p>ok</p>",
            )

    def test_rejects_archive_over_raw_size_budget(self) -> None:
        wheel, source = self._pair()
        with (
            patch("scripts.check_pypi_readme.MAX_ARCHIVE_BYTES", wheel.stat().st_size - 1),
            self.assertRaisesRegex(ReadmeCheckError, "archive exceeds"),
        ):
            check_pypi_readmes(
                (wheel, source),
                renderer=lambda description, **params: "<p>ok</p>",
            )

    def test_requires_markdown_content_type(self) -> None:
        for content_type in (None, "text/plain", "text/x-rst"):
            with self.subTest(content_type=content_type):
                wheel, source = self._pair(self._metadata(content_type=content_type))
                pattern = "Description-Content-Type" if content_type is None else "text/markdown"
                with self.assertRaisesRegex(ReadmeCheckError, pattern):
                    check_pypi_readmes(
                        (wheel, source),
                        renderer=lambda raw, **params: "<p>ok</p>",
                    )

    def test_requires_nonempty_long_description(self) -> None:
        for description in (" \n", "UNKNOWN\n"):
            with self.subTest(description=description):
                wheel, source = self._pair(self._metadata(description))
                with self.assertRaisesRegex(ReadmeCheckError, "missing or empty"):
                    check_pypi_readmes(
                        (wheel, source),
                        renderer=lambda raw, **params: "<p>ok</p>",
                    )

    def test_rejects_mismatched_distribution_descriptions(self) -> None:
        wheel = self._wheel(self._metadata("# Wheel\n"))
        source = self._sdist(self._metadata("# Source\n"))
        with self.assertRaisesRegex(ReadmeCheckError, "do not match"):
            check_pypi_readmes(
                (wheel, source),
                renderer=lambda raw, **params: "<p>ok</p>",
            )

    def test_fails_when_renderer_returns_no_html(self) -> None:
        with self.assertRaisesRegex(ReadmeCheckError, "returned no HTML"):
            check_pypi_readmes(self._pair(), renderer=lambda raw, **params: None)

    def test_fails_when_renderer_warns(self) -> None:
        def warning_renderer(raw: str, **parameters: str) -> str:
            warnings.warn("renderer unavailable", stacklevel=2)
            return "<p>not trusted</p>"

        with self.assertRaisesRegex(ReadmeCheckError, "renderer warned"):
            check_pypi_readmes(self._pair(), renderer=warning_renderer)

    def test_fails_when_renderer_raises(self) -> None:
        def broken_renderer(raw: str, **parameters: str) -> str:
            raise ValueError("invalid Markdown")

        with self.assertRaisesRegex(ReadmeCheckError, "invalid Markdown"):
            check_pypi_readmes(self._pair(), renderer=broken_renderer)


if __name__ == "__main__":
    unittest.main()
