"""Reject unsafe or unintended files in built distributions."""

from __future__ import annotations

import argparse
import stat
import string
import tarfile
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

BANNED_DIRECTORIES = {".venv", "__pycache__", "in", "out", "tmp"}
BANNED_FILENAMES = {".DS_Store"}
BANNED_SUFFIXES = {".pdf", ".pyc", ".pyo"}
MAX_CORE_METADATA_BYTES = 4 * 1024 * 1024
PROJECT_URL_LABEL_REMOVALS = str.maketrans("", "", string.punctuation + string.whitespace)
REQUIRED_PROJECT_URLS = {
    "Security": "https://github.com/Hyperrick/spotpdf/security/policy",
    "Support": "https://github.com/Hyperrick/spotpdf/blob/main/SUPPORT.md",
}


def archive_members(archive: Path) -> list[str]:
    """Return normalized member names from a wheel or source archive."""

    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as package:
            return package.namelist()
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as package:
            return package.getnames()
    raise ValueError(f"unsupported distribution archive: {archive}")


def unsafe_reason(member: str) -> str | None:
    """Explain why one archive member must not be published."""

    path = PurePosixPath(member)
    if path.is_absolute() or ".." in path.parts:
        return "unsafe archive path"
    if path.name in BANNED_FILENAMES:
        return "banned metadata file"
    if any(part in BANNED_DIRECTORIES for part in path.parts):
        return "private, temporary, or cache directory"
    if path.suffix.lower() in BANNED_SUFFIXES:
        return "banned file type"
    return None


def _is_core_metadata_path(member: str, expected_name: str) -> bool:
    path = PurePosixPath(member)
    if len(path.parts) != 2 or path.name != expected_name:
        return False
    if expected_name == "METADATA":
        return path.parts[0].endswith(".dist-info")
    return True


def _zip_member_is_regular(info: zipfile.ZipInfo) -> bool:
    if info.is_dir() or info.flag_bits & 0x1:
        return False
    file_type = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
    return file_type in (0, stat.S_IFREG)


def archive_core_metadata(archive: Path, members: list[str]) -> bytes:
    """Read the single top-level Core Metadata record from one distribution."""

    expected_name = "METADATA" if archive.suffix == ".whl" else "PKG-INFO"
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as package:
            candidates = [
                info
                for info in package.infolist()
                if _is_core_metadata_path(info.filename, expected_name)
            ]
            if len(candidates) != 1:
                raise ValueError(
                    f"expected exactly one top-level {expected_name} entry, found {len(candidates)}"
                )
            info = candidates[0]
            if not _zip_member_is_regular(info):
                raise ValueError(
                    f"top-level {expected_name} entry is not an unencrypted regular file"
                )
            if info.file_size > MAX_CORE_METADATA_BYTES:
                raise ValueError(
                    f"top-level {expected_name} entry exceeds the "
                    f"{MAX_CORE_METADATA_BYTES}-byte limit"
                )
            return package.read(info)

    candidates = [member for member in members if _is_core_metadata_path(member, expected_name)]
    if len(candidates) != 1:
        raise ValueError(
            f"expected exactly one top-level {expected_name} entry, found {len(candidates)}"
        )
    member = candidates[0]

    with tarfile.open(archive, "r:gz") as package:
        info = package.getmember(member)
        if not info.isfile():
            raise ValueError(f"top-level {expected_name} entry is not a regular file")
        if info.size > MAX_CORE_METADATA_BYTES:
            raise ValueError(
                f"top-level {expected_name} entry exceeds the {MAX_CORE_METADATA_BYTES}-byte limit"
            )
        extracted = package.extractfile(info)
        if extracted is None:
            raise ValueError(f"could not read top-level {expected_name} entry")
        return extracted.read()


def normalize_project_url_label(label: str) -> str:
    """Apply the PEP 753 consumer normalization for Project-URL labels."""

    return label.translate(PROJECT_URL_LABEL_REMOVALS).lower()


def project_url_failures(archive: Path, metadata: bytes) -> list[str]:
    """Return failures for malformed or missing canonical project links."""

    message = BytesParser(policy=policy.compat32).parsebytes(metadata)
    failures = [f"malformed Core Metadata: {defect}" for defect in message.defects]
    project_urls: dict[str, list[tuple[str, str]]] = {}
    for value in message.get_all("Project-URL", []):
        label, separator, url = str(value).partition(",")
        label = label.strip()
        url = url.strip()
        normalized_label = normalize_project_url_label(label)
        if not separator or not normalized_label or not url:
            failures.append(f"malformed Project-URL entry: {value!s}")
            continue
        project_urls.setdefault(normalized_label, []).append((label, url))

    for entries in project_urls.values():
        if len(entries) > 1:
            labels = ", ".join(repr(label) for label, _url in entries)
            failures.append(f"Project-URL labels collide after normalization: {labels}")

    for label, expected_url in REQUIRED_PROJECT_URLS.items():
        entries = project_urls.get(normalize_project_url_label(label), [])
        if entries != [(label, expected_url)]:
            actual = ", ".join(f"{raw_label}, {url}" for raw_label, url in entries)
            if not actual:
                actual = "missing"
            failures.append(
                f"Project-URL {label!r} must occur exactly once as {expected_url!r}; found {actual}"
            )
    return [f"{archive.name}: {failure}" for failure in failures]


def check_distributions(directory: Path) -> None:
    """Validate the wheel and source archive in a build directory."""

    archives = sorted([*directory.glob("*.whl"), *directory.glob("*.tar.gz")])
    wheels = [archive for archive in archives if archive.suffix == ".whl"]
    source_archives = [archive for archive in archives if archive.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(source_archives) != 1:
        raise SystemExit("expected exactly one wheel and one source archive")

    failures: list[str] = []
    for archive in archives:
        try:
            members = archive_members(archive)
            metadata = archive_core_metadata(archive, members)
        except (
            KeyError,
            OSError,
            RuntimeError,
            ValueError,
            tarfile.TarError,
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
        ) as error:
            failures.append(f"{archive.name}: invalid distribution archive: {error}")
            continue
        for member in members:
            if reason := unsafe_reason(member):
                failures.append(f"{archive.name}: {member}: {reason}")
        failures.extend(project_url_failures(archive, metadata))
    if failures:
        raise SystemExit("distribution validation failed:\n" + "\n".join(failures))
    print(
        "Distribution contents and canonical project URLs are valid: "
        + ", ".join(archive.name for archive in archives)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing build output")
    args = parser.parse_args()
    check_distributions(args.directory)


if __name__ == "__main__":
    main()
