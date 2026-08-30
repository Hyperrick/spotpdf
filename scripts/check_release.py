"""Validate release metadata and prepare deterministic release checksums."""

from __future__ import annotations

import argparse
import hashlib
import re
import tomllib
from datetime import date
from pathlib import Path

PROJECT_NAME = "spotpdf"
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
CHECKSUM_PATTERN = re.compile(r"([0-9a-f]{64})  ([^/]+)")


class ReleaseCheckError(ValueError):
    """Raised when release metadata or artifacts are not safe to publish."""


def validate_release_metadata(root: Path, tag: str) -> str:
    """Validate a stable tag against project, lockfile, and release documentation."""

    if not tag.startswith("v") or VERSION_PATTERN.fullmatch(tag[1:]) is None:
        raise ReleaseCheckError(f"release tag must match vX.Y.Z exactly: {tag!r}")

    version = _project_version(root / "pyproject.toml")
    if tag != f"v{version}":
        raise ReleaseCheckError(f"release tag {tag!r} does not match project version {version!r}")

    locked_version = _locked_project_version(root / "uv.lock")
    if locked_version != version:
        raise ReleaseCheckError(
            f"uv.lock project version {locked_version!r} does not match {version!r}"
        )

    _validate_changelog(root / "CHANGELOG.md", version)
    _validate_readme(root / "README.md", tag)
    return version


def prepare_release_assets(directory: Path, version: str) -> Path:
    """Validate the two distributions and write their conventional SHA256SUMS file."""

    archives = _validate_archive_set(
        directory,
        version,
        checksum_policy="optional",
        allow_uv_gitignore=True,
    )
    checksum_path = directory / "SHA256SUMS"
    if checksum_path.is_symlink():
        raise ReleaseCheckError(f"refusing checksum symlink: {checksum_path}")
    checksum_path.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in archives),
        encoding="ascii",
    )
    _verify_release_assets(directory, version, allow_uv_gitignore=True)
    return checksum_path


def verify_release_assets(directory: Path, version: str) -> None:
    """Require exactly two distributions and a valid checksum manifest for them."""

    _verify_release_assets(directory, version, allow_uv_gitignore=False)


def _verify_release_assets(directory: Path, version: str, *, allow_uv_gitignore: bool) -> None:
    archives = _validate_archive_set(
        directory,
        version,
        checksum_policy="required",
        allow_uv_gitignore=allow_uv_gitignore,
    )
    checksum_path = directory / "SHA256SUMS"
    if checksum_path.is_symlink() or not checksum_path.is_file():
        raise ReleaseCheckError(f"missing regular checksum file: {checksum_path}")

    try:
        lines = checksum_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReleaseCheckError(f"could not read {checksum_path}: {error}") from error
    if len(lines) != len(archives):
        raise ReleaseCheckError("SHA256SUMS must contain exactly one line per distribution")

    expected = {path.name: _sha256(path) for path in archives}
    actual: dict[str, str] = {}
    for line in lines:
        match = CHECKSUM_PATTERN.fullmatch(line)
        if match is None:
            raise ReleaseCheckError(f"invalid SHA256SUMS line: {line!r}")
        digest, name = match.groups()
        if name in actual:
            raise ReleaseCheckError(f"duplicate SHA256SUMS entry: {name}")
        actual[name] = digest
    if actual != expected:
        raise ReleaseCheckError("SHA256SUMS does not exactly match the release distributions")


def _project_version(path: Path) -> str:
    data = _read_toml(path)
    try:
        name = data["project"]["name"]
        version = data["project"]["version"]
    except (KeyError, TypeError) as error:
        raise ReleaseCheckError(f"missing project name or version in {path}") from error
    if name != PROJECT_NAME or not isinstance(version, str):
        raise ReleaseCheckError(f"unexpected project metadata in {path}")
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseCheckError(f"project version must be stable X.Y.Z: {version!r}")
    return version


def _locked_project_version(path: Path) -> str:
    data = _read_toml(path)
    packages = data.get("package")
    if not isinstance(packages, list):
        raise ReleaseCheckError(f"missing package list in {path}")
    matches = [
        item for item in packages if isinstance(item, dict) and item.get("name") == PROJECT_NAME
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("version"), str):
        raise ReleaseCheckError(f"expected exactly one locked {PROJECT_NAME} package in {path}")
    return matches[0]["version"]


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ReleaseCheckError(f"could not parse {path}: {error}") from error


def _validate_changelog(path: Path, version: str) -> None:
    text = _read_text(path)
    releases = list(
        re.finditer(
            r"(?m)^## \[((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\."
            r"(?:0|[1-9][0-9]*))\] - ([0-9]{4}-[0-9]{2}-[0-9]{2})$",
            text,
        )
    )
    match = next((item for item in releases if item.group(1) == version), None)
    if match is None:
        raise ReleaseCheckError(f"missing dated {version} section in {path}")
    try:
        date.fromisoformat(match.group(2))
    except ValueError as error:
        raise ReleaseCheckError(f"invalid {version} release date in {path}") from error
    expected_unreleased = (
        f"[Unreleased]: https://github.com/Hyperrick/spotpdf/compare/v{version}...HEAD"
    )
    if expected_unreleased not in text:
        raise ReleaseCheckError(f"Unreleased comparison does not start at v{version} in {path}")

    release_index = releases.index(match)
    if release_index + 1 < len(releases):
        previous_version = releases[release_index + 1].group(1)
        expected_release = (
            f"[{version}]: https://github.com/Hyperrick/spotpdf/compare/"
            f"v{previous_version}...v{version}"
        )
        if expected_release not in text:
            raise ReleaseCheckError(
                f"release comparison does not cover v{previous_version}...v{version} in {path}"
            )


def _validate_readme(path: Path, tag: str) -> None:
    text = _read_text(path)
    repository = "git+https://github.com/Hyperrick/spotpdf.git"
    expected_commands = (
        f"uv tool install {repository}@{tag}",
        f"pipx install {repository}@{tag}",
    )
    missing = [command for command in expected_commands if command not in text]
    if missing:
        raise ReleaseCheckError(
            f"stable install commands do not all use {tag} in {path}: {missing}"
        )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReleaseCheckError(f"could not read {path}: {error}") from error


def _expected_archive_names(version: str) -> tuple[str, str]:
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseCheckError(f"release version must match X.Y.Z exactly: {version!r}")
    return (
        f"{PROJECT_NAME}-{version}-py3-none-any.whl",
        f"{PROJECT_NAME}-{version}.tar.gz",
    )


def _validate_archive_set(
    directory: Path,
    version: str,
    *,
    checksum_policy: str = "forbidden",
    allow_uv_gitignore: bool = False,
) -> tuple[Path, Path]:
    expected_names = _expected_archive_names(version)
    expected_sets = [set(expected_names)]
    if checksum_policy == "required":
        expected_sets = [set(expected_names) | {"SHA256SUMS"}]
    elif checksum_policy == "optional":
        expected_sets.append(set(expected_names) | {"SHA256SUMS"})
    elif checksum_policy != "forbidden":
        raise ValueError(f"unknown checksum policy: {checksum_policy}")
    try:
        entries = list(directory.iterdir())
    except OSError as error:
        raise ReleaseCheckError(
            f"could not inspect release directory {directory}: {error}"
        ) from error
    actual_names = {entry.name for entry in entries}
    if allow_uv_gitignore and ".gitignore" in actual_names:
        marker = directory / ".gitignore"
        try:
            marker_is_valid = (
                not marker.is_symlink() and marker.is_file() and marker.read_bytes() == b"*"
            )
        except OSError as error:
            raise ReleaseCheckError(
                f"could not validate uv build marker {marker}: {error}"
            ) from error
        if not marker_is_valid:
            raise ReleaseCheckError(f"unexpected uv build marker contents: {marker}")
        actual_names.remove(".gitignore")
    if actual_names not in expected_sets:
        expected_labels = [sorted(names) for names in expected_sets]
        raise ReleaseCheckError(
            f"release directory must contain exactly one of {expected_labels}; "
            f"found {sorted(actual_names)}"
        )

    archives = tuple(directory / name for name in expected_names)
    for archive in archives:
        if archive.is_symlink() or not archive.is_file():
            raise ReleaseCheckError(f"release asset must be a regular file: {archive}")
        if archive.stat().st_size == 0:
            raise ReleaseCheckError(f"release asset is empty: {archive}")
    return archives


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    metadata = subparsers.add_parser("metadata", help="validate release metadata")
    metadata.add_argument("--tag", required=True)
    metadata.add_argument("--root", type=Path, default=Path.cwd())

    prepare = subparsers.add_parser("prepare-assets", help="write and verify SHA256SUMS")
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--dist", type=Path, default=Path("dist"))

    verify = subparsers.add_parser("verify-assets", help="verify release assets")
    verify.add_argument("--version", required=True)
    verify.add_argument("--dist", type=Path, default=Path("dist"))

    args = parser.parse_args()
    try:
        if args.command == "metadata":
            print(validate_release_metadata(args.root, args.tag))
        elif args.command == "prepare-assets":
            print(prepare_release_assets(args.dist, args.version))
        else:
            verify_release_assets(args.dist, args.version)
            print(f"Release assets verified: {args.dist}")
    except ReleaseCheckError as error:
        parser.exit(1, f"release check failed: {error}\n")


if __name__ == "__main__":
    main()
