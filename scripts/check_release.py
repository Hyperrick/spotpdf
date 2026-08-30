"""Validate release metadata and prepare deterministic release checksums."""

from __future__ import annotations

import argparse
import hashlib
import re
import tomllib
from datetime import date
from pathlib import Path

if __package__:
    from .release_readme import ReleaseReadmeError, validate_release_readme
else:
    from release_readme import ReleaseReadmeError, validate_release_readme

PROJECT_NAME = "spotpdf"
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
CHECKSUM_PATTERN = re.compile(r"([0-9a-f]{64})  ([^/]+)")
CHANGELOG_H2_PATTERN = re.compile(r"(?m)^[ ]{0,3}##(?:[ \t]+[^\r\n]*)?$")
CHANGELOG_UNRELEASED_PATTERN = re.compile(r"(?m)^## \[Unreleased\]$")
CHANGELOG_RELEASE_PATTERN = re.compile(
    r"(?m)^## \[((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*))\] - ([0-9]{4}-[0-9]{2}-[0-9]{2})$"
)
README_STABLE_VERSION_PATTERN = re.compile(
    r"\bstable(?: +release)? +v([^\s]+)",
    re.IGNORECASE,
)
README_DEVELOPMENT_ONLY_PATTERN = re.compile(
    rf"\bcurrent +development +branch\b|"
    rf"\bcurrent +development +version\b|"
    rf"\bdevelopment +CLI(?:'s)?\b|"
    rf"\bnext +release\b|"
    rf"\bunreleased\b|"
    rf"\bnot +(?:yet +)?included +in +(?:the +)?stable(?: +release)? +"
    rf"v{VERSION_PATTERN.pattern}(?: +release)?\b|"
    rf"\b(?:is|are) +(?:available|included|present|supported) +only"
    rf" +(?:on|in) +(?:the +)?(?:current +)?development +branch\b|"
    rf"\b(?:is|are) +only +(?:available|included|present|supported)"
    rf" +(?:on|in) +(?:the +)?(?:current +)?development +branch\b",
    re.IGNORECASE,
)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]\r\n]+)\]\([^\)\r\n]*\)")
TRAILING_PROSE_PUNCTUATION = ".,;:!?)]}\u2013\u2014"
RELEASE_DOCUMENTS = (
    (Path("docs/json-output.md"), True),
    (Path("docs/processing-budgets.md"), False),
    (Path("SECURITY.md"), False),
)


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
    for relative_path, require_version_example in RELEASE_DOCUMENTS:
        _validate_release_document(
            root / relative_path,
            version,
            require_version_example=require_version_example,
        )
    _validate_bug_report_template(
        root / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
        version,
    )
    render_release_notes(root, version)
    return version


def render_release_notes(root: Path, version: str) -> str:
    """Render one validated dated changelog section as GitHub Release notes."""

    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseCheckError(f"release version must match X.Y.Z exactly: {version!r}")
    path = root / "CHANGELOG.md"
    text = _read_text(path)
    releases, structure_text = _validated_changelog_sections(text, path)
    match, release_index = _release_match(releases, version, path)
    _validate_release_date(match, version, path)
    if release_index != 0:
        raise ReleaseCheckError(f"dated {version} section is not the newest release in {path}")
    _validate_unreleased_reference(structure_text, path, version)
    body = _release_body(text, structure_text, releases, match, release_index, version, path)
    if release_index + 1 >= len(releases):
        return f"{body}\n"
    previous_version = releases[release_index + 1].group(1)
    comparison = _release_comparison_url(previous_version, version)
    expected_reference = f"[{version}]: {comparison}"
    if _reference_lines(structure_text, version) != [expected_reference]:
        raise ReleaseCheckError(
            f"release comparison does not cover v{previous_version}...v{version} in {path}"
        )
    return f"{body}\n\n[Full changes: v{previous_version}...v{version}]({comparison})\n"


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
    releases, structure_text = _validated_changelog_sections(text, path)
    match, release_index = _release_match(releases, version, path)
    _validate_release_date(match, version, path)
    if release_index != 0:
        raise ReleaseCheckError(f"dated {version} section is not the newest release in {path}")
    _release_body(text, structure_text, releases, match, release_index, version, path)

    _validate_unreleased_reference(structure_text, path, version)

    if release_index + 1 < len(releases):
        previous_version = releases[release_index + 1].group(1)
        expected_release = f"[{version}]: {_release_comparison_url(previous_version, version)}"
        if _reference_lines(structure_text, version) != [expected_release]:
            raise ReleaseCheckError(
                f"release comparison does not cover v{previous_version}...v{version} in {path}"
            )


def _validate_unreleased_reference(text: str, path: Path, version: str) -> None:
    expected = f"[Unreleased]: https://github.com/Hyperrick/spotpdf/compare/v{version}...HEAD"
    if _reference_lines(text, "Unreleased") != [expected]:
        raise ReleaseCheckError(f"Unreleased comparison does not start at v{version} in {path}")


def _validated_changelog_sections(
    text: str,
    path: Path,
) -> tuple[list[re.Match[str]], str]:
    structure_text = _mask_fenced_code(text)
    headings = list(CHANGELOG_H2_PATTERN.finditer(structure_text))
    releases = list(CHANGELOG_RELEASE_PATTERN.finditer(structure_text))
    unreleased = list(CHANGELOG_UNRELEASED_PATTERN.finditer(structure_text))
    valid_spans = {match.span() for match in releases + unreleased}
    unexpected = [match.group(0) for match in headings if match.span() not in valid_spans]
    if unexpected:
        raise ReleaseCheckError(
            f"malformed or unexpected level-2 Changelog section in {path}: {unexpected[0]!r}"
        )
    if len(unreleased) != 1:
        raise ReleaseCheckError(f"expected exactly one Unreleased section in {path}")
    if not releases:
        raise ReleaseCheckError(f"missing dated release sections in {path}")
    seen_versions: set[str] = set()
    for match in releases:
        release_version = match.group(1)
        if release_version in seen_versions:
            raise ReleaseCheckError(f"duplicate dated {release_version} sections in {path}")
        seen_versions.add(release_version)
        _validate_release_date(match, release_version, path)
    if unreleased[0].start() >= releases[0].start():
        raise ReleaseCheckError(
            f"Unreleased section must immediately precede the newest dated release in {path}"
        )
    intervening_headings = [
        match for match in headings if unreleased[0].end() <= match.start() < releases[0].start()
    ]
    if intervening_headings:
        raise ReleaseCheckError(
            f"Unreleased section must immediately precede the newest dated release in {path}"
        )
    if text[unreleased[0].end() : releases[0].start()].strip():
        raise ReleaseCheckError(f"Unreleased section must be empty for a stable tag in {path}")
    return releases, structure_text


def _mask_fenced_code(text: str) -> str:
    """Replace fenced-code characters with spaces while preserving source offsets."""

    output: list[str] = []
    fence_character: str | None = None
    minimum_fence_length = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if fence_character is None:
            opening = re.match(r"^[ ]{0,3}(`{3,}|~{3,})", content)
            if opening is None:
                output.append(line)
                continue
            marker = opening.group(1)
            if marker[0] == "`" and "`" in content[opening.end() :]:
                output.append(line)
                continue
            fence_character = marker[0]
            minimum_fence_length = len(marker)
        else:
            closing = re.fullmatch(
                rf"[ ]{{0,3}}{re.escape(fence_character)}"
                rf"{{{minimum_fence_length},}}[ \t]*",
                content,
            )
            if closing is not None:
                fence_character = None
                minimum_fence_length = 0
        output.append(re.sub(r"[^\r\n]", " ", line))
    if fence_character is not None:
        raise ReleaseCheckError("unclosed Markdown fenced code block")
    return "".join(output)


def _normalized_markdown_prose(text: str) -> str:
    """Return prose paragraphs without fenced code or common inline wrappers."""

    prose = _mask_fenced_code(text)
    prose = MARKDOWN_LINK_PATTERN.sub(r"\1", prose)
    prose = prose.translate(str.maketrans("", "", "*_`"))
    paragraphs = re.split(r"(?:\r?\n[ \t]*){2,}", prose)
    normalized = [re.sub(r"[ \t\r\n]+", " ", paragraph).strip() for paragraph in paragraphs]
    return "\n\n".join(paragraph for paragraph in normalized if paragraph)


def _validate_release_date(match: re.Match[str], version: str, path: Path) -> None:
    try:
        date.fromisoformat(match.group(2))
    except ValueError as error:
        raise ReleaseCheckError(f"invalid {version} release date in {path}") from error


def _release_match(
    releases: list[re.Match[str]],
    version: str,
    path: Path,
) -> tuple[re.Match[str], int]:
    matches = [item for item in releases if item.group(1) == version]
    if not matches:
        raise ReleaseCheckError(f"missing dated {version} section in {path}")
    if len(matches) != 1:
        raise ReleaseCheckError(f"duplicate dated {version} sections in {path}")
    match = matches[0]
    return match, releases.index(match)


def _release_body(
    text: str,
    structure_text: str,
    releases: list[re.Match[str]],
    match: re.Match[str],
    release_index: int,
    version: str,
    path: Path,
) -> str:
    start = match.end()
    if release_index + 1 < len(releases):
        end = releases[release_index + 1].start()
    else:
        footer = re.search(
            r"(?m)^\[(?:Unreleased|[0-9]+\.[0-9]+\.[0-9]+)\]:",
            structure_text[start:],
        )
        end = start + footer.start() if footer is not None else len(text)
    body = text[start:end].strip()
    structural_body = structure_text[start:end].strip()
    if not body or re.search(r"(?m)^### \S", structural_body) is None:
        raise ReleaseCheckError(f"dated {version} section has no release notes in {path}")
    return body


def _release_comparison_url(previous_version: str, version: str) -> str:
    return f"https://github.com/Hyperrick/spotpdf/compare/v{previous_version}...v{version}"


def _reference_lines(text: str, label: str) -> list[str]:
    return re.findall(rf"(?m)^\[{re.escape(label)}\]:[ \t]+\S+[ \t]*$", text)


def _validate_readme(path: Path, tag: str) -> None:
    text = _read_text(path)
    version = tag[1:]
    try:
        validate_release_readme(path, text, tag)
    except ReleaseReadmeError as error:
        raise ReleaseCheckError(str(error)) from error

    prose = _normalized_markdown_prose(text)
    stable_tokens = [
        token.rstrip(TRAILING_PROSE_PUNCTUATION)
        for token in README_STABLE_VERSION_PATTERN.findall(prose)
    ]
    if not stable_tokens:
        raise ReleaseCheckError(f"missing stable release prose in {path}")
    invalid_tokens = [token for token in stable_tokens if VERSION_PATTERN.fullmatch(token) is None]
    if invalid_tokens:
        raise ReleaseCheckError(
            f"stable release prose uses non-stable version tokens in {path}: {invalid_tokens}"
        )
    referenced_versions = set(stable_tokens)
    if referenced_versions != {version}:
        raise ReleaseCheckError(
            f"stable release prose does not exclusively use {tag} in {path}: {stable_tokens}"
        )
    if README_DEVELOPMENT_ONLY_PATTERN.search(prose) is not None:
        raise ReleaseCheckError(f"README contains a development-only release claim in {path}")
    _validate_version_examples(text, path, version, required=True)


def _validate_release_document(
    path: Path,
    version: str,
    *,
    require_version_example: bool,
) -> None:
    """Reject stale stable-version and development-only release documentation."""

    text = _read_text(path)
    prose = _normalized_markdown_prose(text)
    if README_DEVELOPMENT_ONLY_PATTERN.search(prose) is not None:
        raise ReleaseCheckError(f"development-only release claim remains in {path}")

    stable_tokens = [
        token.rstrip(TRAILING_PROSE_PUNCTUATION)
        for token in README_STABLE_VERSION_PATTERN.findall(prose)
    ]
    if stable_tokens and set(stable_tokens) != {version}:
        raise ReleaseCheckError(
            f"stable release prose does not exclusively use v{version} in {path}: {stable_tokens}"
        )
    _validate_version_examples(text, path, version, required=require_version_example)


def _validate_version_examples(text: str, path: Path, version: str, *, required: bool) -> None:
    """Bind machine-output examples to the package version being released."""

    pattern = re.compile(
        r'["\']spotpdf_version["\']\s*:\s*(?P<quote>["\'])(?P<value>[^"\']*)(?P=quote)'
    )
    examples = [match.group("value") for match in pattern.finditer(text)]
    if required and not examples:
        raise ReleaseCheckError(f"missing spotpdf_version example in {path}")
    if examples and set(examples) != {version}:
        raise ReleaseCheckError(
            f"spotpdf_version examples do not exclusively use {version} in {path}: {examples}"
        )


def _validate_bug_report_template(path: Path, version: str) -> None:
    text = _read_text(path)
    pattern = re.compile(
        rf"(?m)^\s*placeholder:\s*[\"']spotpdf\s+({VERSION_PATTERN.pattern})[\"']\s*$"
    )
    if pattern.findall(text) != [version]:
        raise ReleaseCheckError(
            f"bug-report version placeholder does not uniquely use {version} in {path}"
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

    notes = subparsers.add_parser("notes", help="render curated notes from CHANGELOG.md")
    notes.add_argument("--version", required=True)
    notes.add_argument("--root", type=Path, default=Path.cwd())

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
        elif args.command == "notes":
            print(render_release_notes(args.root, args.version), end="")
        elif args.command == "prepare-assets":
            print(prepare_release_assets(args.dist, args.version))
        else:
            verify_release_assets(args.dist, args.version)
            print(f"Release assets verified: {args.dist}")
    except ReleaseCheckError as error:
        parser.exit(1, f"release check failed: {error}\n")


if __name__ == "__main__":
    main()
