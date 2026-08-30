"""Render packaged Markdown long descriptions with PyPI's renderer."""

from __future__ import annotations

import argparse
import gzip
import stat
import struct
import tarfile
import warnings
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import BinaryIO

MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2_048
MAX_DECOMPRESSED_TAR_BYTES = 16 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_ZIP_CENTRAL_DIRECTORY_BYTES = 2 * 1024 * 1024
MAX_ZIP_COMMENT_BYTES = 65_535
ZIP_EOCD = struct.Struct("<4s4H2LH")
ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
MarkdownRenderer = Callable[..., str | None]


class ReadmeCheckError(RuntimeError):
    """Raised when a packaged PyPI README cannot be verified."""


@dataclass(frozen=True)
class PackagedReadme:
    """Long-description data read from one built distribution."""

    archive: Path
    content_type: str
    parameters: tuple[tuple[str, str], ...]
    description: str


class _BoundedReader:
    """Expose at most a fixed number of decompressed bytes."""

    def __init__(self, source: BinaryIO, limit: int, path: Path) -> None:
        self._source = source
        self._remaining = limit
        self._path = path

    def read(self, size: int = -1) -> bytes:
        request = size
        if request < 0 or request > self._remaining + 1:
            request = self._remaining + 1
        data = self._source.read(request)
        if len(data) > self._remaining:
            raise ReadmeCheckError(
                f"{self._path.name}: decompressed TAR data exceeds the "
                f"{MAX_DECOMPRESSED_TAR_BYTES}-byte limit"
            )
        self._remaining -= len(data)
        return data


def _is_metadata_path(name: str, expected: str) -> bool:
    parts = PurePosixPath(name).parts
    if len(parts) != 2 or parts[1] != expected:
        return False
    return expected != "METADATA" or parts[0].endswith(".dist-info")


def _validate_archive_file(path: Path) -> int:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ReadmeCheckError(f"cannot inspect distribution archive {path}: {exc}") from exc
    if not stat.S_ISREG(details.st_mode):
        raise ReadmeCheckError(f"distribution is not a regular file: {path}")
    if details.st_size > MAX_ARCHIVE_BYTES:
        raise ReadmeCheckError(
            f"{path.name}: archive exceeds the {MAX_ARCHIVE_BYTES}-byte size limit"
        )
    return details.st_size


def _zip_directory_limits(path: Path, archive_size: int) -> int:
    tail_size = min(archive_size, ZIP_EOCD.size + MAX_ZIP_COMMENT_BYTES)
    try:
        with path.open("rb") as stream:
            stream.seek(archive_size - tail_size)
            tail = stream.read(tail_size)
    except OSError as exc:
        raise ReadmeCheckError(f"{path.name}: cannot read wheel directory: {exc}") from exc
    offset = tail.rfind(ZIP_EOCD_SIGNATURE)
    if offset < 0 or len(tail) - offset < ZIP_EOCD.size:
        raise ReadmeCheckError(f"{path.name}: missing ZIP end-of-directory record")
    (
        signature,
        disk_number,
        directory_disk,
        disk_entries,
        total_entries,
        directory_size,
        directory_offset,
        comment_size,
    ) = ZIP_EOCD.unpack_from(tail, offset)
    if signature != ZIP_EOCD_SIGNATURE:
        raise ReadmeCheckError(f"{path.name}: invalid ZIP end-of-directory record")
    if offset + ZIP_EOCD.size + comment_size != len(tail):
        raise ReadmeCheckError(f"{path.name}: ambiguous ZIP end-of-directory record")
    if disk_number or directory_disk or disk_entries != total_entries:
        raise ReadmeCheckError(f"{path.name}: multi-disk wheels are unsupported")
    if total_entries == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        raise ReadmeCheckError(f"{path.name}: ZIP64 wheels are unsupported")
    if total_entries > MAX_ARCHIVE_MEMBERS:
        raise ReadmeCheckError(
            f"{path.name}: archive has {total_entries} members; limit is {MAX_ARCHIVE_MEMBERS}"
        )
    if directory_size > MAX_ZIP_CENTRAL_DIRECTORY_BYTES:
        raise ReadmeCheckError(
            f"{path.name}: ZIP directory exceeds the {MAX_ZIP_CENTRAL_DIRECTORY_BYTES}-byte limit"
        )
    directory_end = directory_offset + directory_size
    eocd_position = archive_size - tail_size + offset
    if directory_end != eocd_position:
        raise ReadmeCheckError(f"{path.name}: inconsistent ZIP directory bounds")
    return total_entries


def _zip_member_is_regular(info: zipfile.ZipInfo) -> bool:
    if info.is_dir() or info.flag_bits & 0x1:
        return False
    file_type = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
    return file_type in (0, stat.S_IFREG)


def _bounded_metadata(path: Path, member: str, raw: bytes) -> bytes:
    if not raw:
        raise ReadmeCheckError(f"{path.name}: {member} is empty")
    if len(raw) > MAX_METADATA_BYTES:
        raise ReadmeCheckError(
            f"{path.name}: {member} exceeds the {MAX_METADATA_BYTES}-byte metadata limit"
        )
    return raw


def _read_wheel_metadata(path: Path) -> bytes:
    archive_size = _validate_archive_file(path)
    expected_members = _zip_directory_limits(path, archive_size)
    try:
        with zipfile.ZipFile(path) as package:
            members = package.infolist()
            if len(members) != expected_members:
                raise ReadmeCheckError(
                    f"{path.name}: ZIP member count does not match its directory record"
                )
            candidates = [item for item in members if _is_metadata_path(item.filename, "METADATA")]
            if len(candidates) != 1:
                raise ReadmeCheckError(
                    f"{path.name}: expected exactly one top-level METADATA entry, "
                    f"found {len(candidates)}"
                )
            info = candidates[0]
            if not _zip_member_is_regular(info):
                raise ReadmeCheckError(
                    f"{path.name}: {info.filename} is not an unencrypted regular file"
                )
            if info.file_size > MAX_METADATA_BYTES:
                raise ReadmeCheckError(
                    f"{path.name}: {info.filename} exceeds the "
                    f"{MAX_METADATA_BYTES}-byte metadata limit"
                )
            return _bounded_metadata(path, info.filename, package.read(info))
    except ReadmeCheckError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ReadmeCheckError(f"{path.name}: invalid wheel archive: {exc}") from exc


def _read_sdist_metadata(path: Path) -> bytes:
    _validate_archive_file(path)
    try:
        with (
            path.open("rb") as compressed,
            gzip.GzipFile(fileobj=compressed, mode="rb") as decompressed,
        ):
            bounded = _BoundedReader(decompressed, MAX_DECOMPRESSED_TAR_BYTES, path)
            with tarfile.open(fileobj=bounded, mode="r|") as package:
                member_count = 0
                metadata: bytes | None = None
                for member in package:
                    member_count += 1
                    if member_count > MAX_ARCHIVE_MEMBERS:
                        raise ReadmeCheckError(
                            f"{path.name}: archive has more than {MAX_ARCHIVE_MEMBERS} members"
                        )
                    if not _is_metadata_path(member.name, "PKG-INFO"):
                        continue
                    if metadata is not None:
                        raise ReadmeCheckError(
                            f"{path.name}: expected exactly one top-level PKG-INFO entry, "
                            "found more than one"
                        )
                    if not member.isfile():
                        raise ReadmeCheckError(f"{path.name}: {member.name} is not a regular file")
                    if member.size > MAX_METADATA_BYTES:
                        raise ReadmeCheckError(
                            f"{path.name}: {member.name} exceeds the "
                            f"{MAX_METADATA_BYTES}-byte metadata limit"
                        )
                    extracted = package.extractfile(member)
                    if extracted is None:
                        raise ReadmeCheckError(f"{path.name}: cannot read {member.name}")
                    metadata = _bounded_metadata(
                        path,
                        member.name,
                        extracted.read(MAX_METADATA_BYTES + 1),
                    )
                if metadata is None:
                    raise ReadmeCheckError(
                        f"{path.name}: expected exactly one top-level PKG-INFO entry, found 0"
                    )
                return metadata
    except ReadmeCheckError:
        raise
    except (EOFError, OSError, tarfile.TarError) as exc:
        raise ReadmeCheckError(f"{path.name}: invalid source archive: {exc}") from exc


def _read_metadata(path: Path) -> bytes:
    if path.suffix == ".whl":
        return _read_wheel_metadata(path)
    if path.name.endswith(".tar.gz"):
        return _read_sdist_metadata(path)
    raise ReadmeCheckError(f"unsupported distribution archive: {path}")


def _parse_content_type(path: Path, value: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    header = EmailMessage()
    try:
        header["Content-Type"] = value
    except ValueError as exc:
        raise ReadmeCheckError(
            f"{path.name}: invalid Description-Content-Type header: {exc}"
        ) from exc
    parsed = header["Content-Type"]
    if parsed is None or parsed.defects:
        raise ReadmeCheckError(f"{path.name}: invalid Description-Content-Type header")
    parameters = tuple(sorted((str(key), str(item)) for key, item in parsed.params.items()))
    return header.get_content_type(), parameters


def packaged_readme(path: Path) -> PackagedReadme:
    """Read and validate the long description from one distribution."""

    message = BytesParser(policy=default).parsebytes(_read_metadata(path))
    if message.defects:
        details = ", ".join(type(defect).__name__ for defect in message.defects)
        raise ReadmeCheckError(f"{path.name}: malformed core metadata ({details})")
    content_type_headers = message.get_all("Description-Content-Type") or []
    if len(content_type_headers) != 1:
        raise ReadmeCheckError(
            f"{path.name}: expected exactly one Description-Content-Type header, "
            f"found {len(content_type_headers)}"
        )
    content_type, parameters = _parse_content_type(path, str(content_type_headers[0]))
    if content_type != "text/markdown":
        raise ReadmeCheckError(
            f"{path.name}: expected text/markdown long description, found {content_type}"
        )
    description = message.get_payload()
    if (
        not isinstance(description, str)
        or not description.strip()
        or description.strip() == "UNKNOWN"
    ):
        raise ReadmeCheckError(f"{path.name}: long description is missing or empty")
    return PackagedReadme(path, content_type, parameters, description)


def _load_markdown_renderer() -> MarkdownRenderer:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            from readme_renderer import markdown
        except Exception as exc:
            raise ReadmeCheckError(
                "PyPI Markdown renderer is unavailable; install readme-renderer[md]"
            ) from exc
    if caught:
        messages = "; ".join(str(item.message) for item in caught)
        raise ReadmeCheckError(f"PyPI Markdown renderer is unavailable: {messages}")
    return markdown.render


def _distribution_pair(paths: Iterable[Path]) -> tuple[Path, Path]:
    archives = tuple(Path(path) for path in paths)
    wheels = [path for path in archives if path.suffix == ".whl"]
    source_archives = [path for path in archives if path.name.endswith(".tar.gz")]
    unsupported = [
        path for path in archives if path.suffix != ".whl" and not path.name.endswith(".tar.gz")
    ]
    if unsupported:
        names = ", ".join(path.name for path in unsupported)
        raise ReadmeCheckError(f"unsupported distribution archive(s): {names}")
    if len(wheels) != 1 or len(source_archives) != 1:
        raise ReadmeCheckError("expected exactly one wheel and one source archive")
    return wheels[0], source_archives[0]


def check_pypi_readmes(
    paths: Iterable[Path],
    *,
    renderer: MarkdownRenderer | None = None,
) -> tuple[PackagedReadme, PackagedReadme]:
    """Render matching wheel and sdist long descriptions, failing closed."""

    wheel, source_archive = _distribution_pair(paths)
    readmes = (packaged_readme(wheel), packaged_readme(source_archive))
    reference = readmes[0]
    for candidate in readmes[1:]:
        if (
            candidate.content_type != reference.content_type
            or candidate.parameters != reference.parameters
            or candidate.description != reference.description
        ):
            raise ReadmeCheckError("wheel and source archive long descriptions do not match")

    render = renderer or _load_markdown_renderer()
    for packaged in readmes:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                html = render(packaged.description, **dict(packaged.parameters))
            except Exception as exc:
                raise ReadmeCheckError(
                    f"{packaged.archive.name}: Markdown rendering failed: {exc}"
                ) from exc
        if caught:
            messages = "; ".join(str(item.message) for item in caught)
            raise ReadmeCheckError(f"{packaged.archive.name}: Markdown renderer warned: {messages}")
        if not isinstance(html, str) or not html.strip():
            raise ReadmeCheckError(
                f"{packaged.archive.name}: Markdown renderer returned no HTML; "
                "install readme-renderer[md]"
            )

    names = ", ".join(packaged.archive.name for packaged in readmes)
    print(f"PyPI Markdown long descriptions rendered successfully: {names}")
    return readmes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path, help="wheel and source archive")
    args = parser.parse_args()
    try:
        check_pypi_readmes(args.archives)
    except ReadmeCheckError as exc:
        parser.exit(1, f"PyPI README check failed: {exc}\n")


if __name__ == "__main__":
    main()
