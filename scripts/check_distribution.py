"""Reject unsafe or unintended files in built distributions."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

BANNED_DIRECTORIES = {".venv", "__pycache__", "in", "out", "tmp"}
BANNED_FILENAMES = {".DS_Store"}
BANNED_SUFFIXES = {".pdf", ".pyc", ".pyo"}


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


def check_distributions(directory: Path) -> None:
    """Validate the wheel and source archive in a build directory."""

    archives = sorted([*directory.glob("*.whl"), *directory.glob("*.tar.gz")])
    wheels = [archive for archive in archives if archive.suffix == ".whl"]
    source_archives = [archive for archive in archives if archive.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(source_archives) != 1:
        raise SystemExit("expected exactly one wheel and one source archive")

    failures: list[str] = []
    for archive in archives:
        for member in archive_members(archive):
            if reason := unsafe_reason(member):
                failures.append(f"{archive.name}: {member}: {reason}")
    if failures:
        raise SystemExit("unsafe distribution contents:\n" + "\n".join(failures))
    print(f"Distribution contents are clean: {', '.join(a.name for a in archives)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing build output")
    args = parser.parse_args()
    check_distributions(args.directory)


if __name__ == "__main__":
    main()
