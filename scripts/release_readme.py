"""Validate release-bound README links and stable installation commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

PROJECT_OWNER = "Hyperrick"
PROJECT_NAME = "spotpdf"
PROJECT_GIT_URL = "git+https://github.com/Hyperrick/spotpdf.git"
MAX_README_BYTES = 4 * 1024 * 1024
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
LIVE_POLICY_PATHS = {
    PurePosixPath("CONTRIBUTING.md"),
    PurePosixPath("SUPPORT.md"),
    PurePosixPath("docs/releasing.md"),
}


class ReleaseReadmeError(ValueError):
    """Raised when a README would be incomplete or misleading on PyPI."""


@dataclass(frozen=True)
class _Target:
    value: str
    line: int
    attribute: str


@dataclass(frozen=True)
class _ProjectContent:
    ref: str
    path: PurePosixPath
    transport: str
    canonical: bool


class _TargetHTMLParser(HTMLParser):
    """Collect raw HTML targets with source lines."""

    def __init__(self, start_line: int) -> None:
        super().__init__(convert_charrefs=True)
        self.start_line = start_line
        self.targets: list[_Target] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag
        line = self.start_line + self.getpos()[0] - 1
        for name, value in attrs:
            normalized = name.casefold()
            if normalized in {"href", "src", "srcset"}:
                self.targets.append(_Target(value or "", line, normalized))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)


def validate_release_readme(path: Path, text: str, tag: str) -> str:
    """Validate PyPI rendering targets and return the stable install channel."""

    if not tag.startswith("v") or VERSION_PATTERN.fullmatch(tag[1:]) is None:
        raise ReleaseReadmeError(f"release tag must match vX.Y.Z exactly: {tag!r}")
    if len(text.encode("utf-8")) > MAX_README_BYTES:
        raise ReleaseReadmeError(f"README exceeds {MAX_README_BYTES} bytes: {path}")

    channel = _stable_install_channel(text, path, tag)
    errors: list[str] = []
    for target in _readme_targets(text):
        value = target.value.strip()
        if target.attribute == "srcset":
            errors.append(f"{path}:{target.line}: HTML srcset is not supported in the PyPI README")
            continue
        if _is_repository_relative(value):
            errors.append(
                f"{path}:{target.line}: repository-relative {target.attribute} target "
                f"will break on PyPI: {value!r}"
            )
            continue
        errors.extend(_project_content_errors(path, target, value, tag))
    if errors:
        raise ReleaseReadmeError("PyPI README validation failed:\n" + "\n".join(errors))
    return channel


def _stable_install_channel(text: str, path: Path, tag: str) -> str:
    version = tag[1:]
    visible_code = "\n".join(
        token.content for token in _parse_markdown(text) if token.type in {"code_block", "fence"}
    )
    channels: list[str] = []
    for command in ("python -m pip install", "uv tool install", "pipx install"):
        git_pattern = re.compile(
            rf"(?m)^{re.escape(command)}[ \t]+{re.escape(PROJECT_GIT_URL)}@v"
            rf"({VERSION_PATTERN.pattern})[ \t]*$"
        )
        pypi_pattern = re.compile(
            rf"(?m)^{re.escape(command)}[ \t]+{PROJECT_NAME}=="
            rf"({VERSION_PATTERN.pattern})[ \t]*$"
        )
        git_versions = git_pattern.findall(visible_code)
        pypi_versions = pypi_pattern.findall(visible_code)
        if git_versions == [version] and not pypi_versions:
            channels.append("git-tag")
        elif pypi_versions == [version] and not git_versions:
            channels.append("pypi")
        else:
            raise ReleaseReadmeError(
                f"stable install command {command!r} does not uniquely use {tag} "
                f"through one supported channel in {path}"
            )
    if len(set(channels)) != 1:
        raise ReleaseReadmeError(f"stable install commands use mixed channels in {path}")
    return channels[0]


def _readme_targets(text: str) -> list[_Target]:
    targets: list[_Target] = []
    pending = [(_parse_markdown(text), 1)]
    while pending:
        tokens, inherited_line = pending.pop()
        for token in tokens:
            line = token.map[0] + 1 if token.map is not None else inherited_line
            if token.type == "link_open":
                if (value := token.attrGet("href")) is not None:
                    targets.append(_Target(value, line, "href"))
            elif token.type == "image":
                if (value := token.attrGet("src")) is not None:
                    targets.append(_Target(value, line, "src"))
            elif token.type in {"html_inline", "html_block"}:
                parser = _TargetHTMLParser(line)
                parser.feed(token.content)
                parser.close()
                targets.extend(parser.targets)
            if token.children:
                pending.append((token.children, line))
    return sorted(targets, key=lambda target: (target.line, target.attribute, target.value))


def _parse_markdown(text: str) -> list:
    try:
        from markdown_it import MarkdownIt
    except ImportError as error:
        raise ReleaseReadmeError("markdown-it-py is required to validate README targets") from error
    return MarkdownIt("commonmark").parse(text)


def _is_repository_relative(value: str) -> bool:
    if value.startswith("#"):
        return False
    if not value or value.startswith("//"):
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return True
    return not parsed.scheme and not parsed.netloc


def _project_content_errors(
    readme_path: Path,
    target: _Target,
    value: str,
    tag: str,
) -> list[str]:
    content = _project_content(value)
    if content is None:
        return []

    prefix = f"{readme_path}:{target.line}:"
    errors: list[str] = []
    if not content.canonical:
        errors.append(f"{prefix} project content URL must use canonical HTTPS: {value!r}")
    live_policy = (
        target.attribute == "href" and content.ref == "main" and content.path in LIVE_POLICY_PATHS
    )
    if content.ref != tag and not live_policy:
        errors.append(
            f"{prefix} project content URL must use {tag}, not {content.ref!r}: {value!r}"
        )
    expected_transports = {"raw"} if target.attribute == "src" else {"blob", "tree"}
    if content.transport not in expected_transports:
        errors.append(
            f"{prefix} project {target.attribute} URL uses {content.transport!r} instead of "
            f"{sorted(expected_transports)!r}: {value!r}"
        )
    if (
        content.path.is_absolute()
        or not content.path.parts
        or any(part in {".", ".."} for part in content.path.parts)
    ):
        errors.append(f"{prefix} project content URL has an unsafe path: {value!r}")
        return errors

    candidate = readme_path.resolve().parent.joinpath(*content.path.parts)
    expected_kind = "directory" if content.transport == "tree" else "file"
    exists = candidate.is_dir() if content.transport == "tree" else candidate.is_file()
    if not exists:
        errors.append(
            f"{prefix} project content URL does not identify a local {expected_kind}: {value!r}"
        )
    return errors


def _project_content(value: str) -> _ProjectContent | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    try:
        port = parsed.port
    except ValueError:
        port = -1
    original_host = (parsed.hostname or "").casefold()
    host = original_host.rstrip(".")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    identity = [part.casefold() for part in parts[:2]]
    project = [PROJECT_OWNER.casefold(), PROJECT_NAME.casefold()]
    if host in {"github.com", "www.github.com"} and identity == project and len(parts) >= 3:
        transport = parts[2].casefold()
        if transport in {"blob", "raw", "tree"}:
            ref = parts[3] if len(parts) >= 4 else ""
            canonical = (
                parsed.scheme.casefold() == "https"
                and parsed.username is None
                and parsed.password is None
                and port is None
                and original_host == "github.com"
                and parsed.netloc.casefold() == "github.com"
                and parts[2] == transport
            )
            return _ProjectContent(
                ref,
                PurePosixPath(*parts[4:]),
                transport,
                canonical,
            )
    if host == "raw.githubusercontent.com" and identity == project and len(parts) >= 2:
        ref = parts[2] if len(parts) >= 3 else ""
        canonical = (
            parsed.scheme.casefold() == "https"
            and parsed.username is None
            and parsed.password is None
            and port is None
            and original_host == "raw.githubusercontent.com"
            and parsed.netloc.casefold() == "raw.githubusercontent.com"
        )
        return _ProjectContent(ref, PurePosixPath(*parts[3:]), "raw", canonical)
    return None
