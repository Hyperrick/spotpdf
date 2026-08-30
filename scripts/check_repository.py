"""Check tracked repository files and local documentation file targets."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

MARKDOWN_SUFFIXES = {".md", ".markdown"}
MAX_MARKDOWN_BYTES = 4 * 1024 * 1024
MAX_SRCSET_CHARACTERS = 64 * 1024
MAX_SRCSET_CANDIDATES = 256
ASCII_WHITESPACE = " \t\n\f\r"
MARKDOWN_PARSER = MarkdownIt("commonmark")
LineSpan = tuple[int, int]


class RepositoryCheckError(RuntimeError):
    """Raised when tracked paths cannot be inspected."""


class _DocumentationHTMLParser(HTMLParser):
    """Collect HTML asset targets while ignoring comments and raw-text content."""

    def __init__(self, start_line: int) -> None:
        super().__init__(convert_charrefs=True)
        self.start_line = start_line
        self.targets: list[tuple[str, LineSpan]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag
        line = self.start_line + self.getpos()[0] - 1
        for name, value in attrs:
            name = name.casefold()
            if name in {"href", "src"} and value is not None:
                self.targets.append((value, (line, line)))
            elif name == "srcset" and value is not None:
                self.targets.extend((target, (line, line)) for target in _srcset_urls(value))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)


def git_tracked_paths(root: Path) -> tuple[PurePosixPath, ...]:
    """Return paths known to Git without inspecting ignored working files."""

    try:
        completed = subprocess.run(
            ["git", "-C", os.fspath(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        raise RepositoryCheckError(f"could not list tracked files: {detail or error}") from error

    paths = tuple(
        PurePosixPath(os.fsdecode(raw_path))
        for raw_path in completed.stdout.split(b"\0")
        if raw_path
    )
    if any(path.is_absolute() or ".." in path.parts for path in paths):
        raise RepositoryCheckError("git returned a path outside the repository")
    return paths


def tracked_pdf_errors(paths: tuple[PurePosixPath, ...]) -> list[str]:
    """Report tracked PDF paths using a case-insensitive extension check."""

    return [
        f"tracked PDF is forbidden: {path.as_posix()}"
        for path in paths
        if path.suffix.casefold() == ".pdf"
    ]


def documentation_link_errors(
    root: Path,
    paths: tuple[PurePosixPath, ...],
) -> tuple[list[str], int]:
    """Validate repository-relative file targets in every tracked Markdown file."""

    repository = root.resolve()
    tracked = {path.as_posix() for path in paths}
    errors: list[str] = []
    checked_links = 0

    for source in paths:
        if source.suffix.casefold() not in MARKDOWN_SUFFIXES:
            continue
        source_path = repository.joinpath(*source.parts)
        try:
            data = source_path.read_bytes()
            if len(data) > MAX_MARKDOWN_BYTES:
                errors.append(
                    f"tracked Markdown file exceeds {MAX_MARKDOWN_BYTES} bytes: {source.as_posix()}"
                )
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(f"could not read tracked Markdown file {source.as_posix()}: {error}")
            continue

        for target, lines in _link_targets(text):
            relative = _repository_relative_target(source, target)
            if relative is None:
                continue
            checked_links += 1
            problem = _target_problem(repository, relative, tracked)
            if problem is not None:
                location = _format_line_span(lines)
                errors.append(f"{source.as_posix()}:{location}: {target!r} {problem}")

    return errors, checked_links


def _link_targets(text: str) -> list[tuple[str, LineSpan]]:
    environment: dict = {}
    tokens = MARKDOWN_PARSER.parse(text, environment)
    targets: list[tuple[str, LineSpan]] = []
    for token in tokens:
        lines = _token_line_span(token.map)
        if token.type == "inline":
            for child in token.children or ():
                if child.type == "link_open":
                    _append_target(targets, child.attrGet("href"), lines)
                elif child.type == "image":
                    _append_target(targets, child.attrGet("src"), lines)
                elif child.type == "html_inline":
                    for target, _ in _html_link_targets(child.content, lines[0]):
                        targets.append((target, lines))
        elif token.type == "html_block":
            targets.extend(_html_link_targets(token.content, lines[0]))

    references = environment.get("references", {})
    if isinstance(references, dict):
        for reference in references.values():
            if not isinstance(reference, dict):
                continue
            source_map = reference.get("map")
            lines = _token_line_span(source_map if isinstance(source_map, list) else None)
            _append_target(targets, reference.get("href"), lines)
    return targets


def _append_target(
    targets: list[tuple[str, LineSpan]],
    target: object,
    lines: LineSpan,
) -> None:
    if isinstance(target, str):
        targets.append((target, lines))


def _token_line_span(source_map: list[int] | None) -> LineSpan:
    if not source_map or len(source_map) < 2:
        return (1, 1)
    return (source_map[0] + 1, max(source_map[0] + 1, source_map[1]))


def _format_line_span(lines: LineSpan) -> str:
    return str(lines[0]) if lines[0] == lines[1] else f"{lines[0]}-{lines[1]}"


def _html_link_targets(value: str, start_line: int) -> list[tuple[str, LineSpan]]:
    return _parse_html(value, start_line).targets


def _parse_html(value: str, start_line: int) -> _DocumentationHTMLParser:
    parser = _DocumentationHTMLParser(start_line)
    parser.feed(value)
    parser.close()
    return parser


def _srcset_urls(value: str) -> list[str]:
    """Extract candidate URLs with the HTML srcset tokenization rules."""

    if len(value) > MAX_SRCSET_CHARACTERS:
        raise RepositoryCheckError(
            f"HTML srcset exceeds the {MAX_SRCSET_CHARACTERS}-character limit"
        )
    urls: list[str] = []
    position = 0
    while position < len(value):
        while position < len(value) and (
            value[position] in ASCII_WHITESPACE or value[position] == ","
        ):
            position += 1
        start = position
        while position < len(value) and value[position] not in ASCII_WHITESPACE:
            position += 1
        url = value[start:position]
        if not url:
            break
        if url.endswith(","):
            url = url.rstrip(",")
            if url:
                urls.append(url)
                _check_srcset_candidate_count(urls)
            continue
        urls.append(url)
        _check_srcset_candidate_count(urls)

        parentheses = 0
        while position < len(value):
            character = value[position]
            position += 1
            if character == "(":
                parentheses += 1
            elif character == ")" and parentheses:
                parentheses -= 1
            elif character == "," and not parentheses:
                break
    return urls


def _check_srcset_candidate_count(urls: list[str]) -> None:
    if len(urls) > MAX_SRCSET_CANDIDATES:
        raise RepositoryCheckError(
            f"HTML srcset exceeds the {MAX_SRCSET_CANDIDATES}-candidate limit"
        )


def _repository_relative_target(
    source: PurePosixPath,
    target: str,
) -> PurePosixPath | None:
    target = target.strip()
    if not target or target.startswith(("#", "/")):
        return None
    try:
        parsed = urlsplit(target)
    except ValueError:
        return PurePosixPath("..")
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None

    decoded = unquote(parsed.path)
    candidate_parts = source.parent.parts + PurePosixPath(decoded).parts
    normalized: list[str] = []
    for part in candidate_parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not normalized:
                return PurePosixPath("..")
            normalized.pop()
            continue
        normalized.append(part)
    return PurePosixPath(*normalized)


def _target_problem(
    repository: Path,
    relative: PurePosixPath,
    tracked: set[str],
) -> str | None:
    if relative == PurePosixPath(".."):
        return "escapes the repository or is not a valid URL"
    if relative == PurePosixPath("."):
        return None

    candidate = repository.joinpath(*relative.parts)
    try:
        candidate.resolve(strict=False).relative_to(repository)
    except (OSError, ValueError):
        return "resolves outside the repository"

    relative_text = relative.as_posix()
    if relative_text in tracked:
        if candidate.exists():
            return None
        return "points to a tracked path missing from the working tree"

    directory_prefix = f"{relative_text.rstrip('/')}/"
    if candidate.is_dir() and any(path.startswith(directory_prefix) for path in tracked):
        return None
    if candidate.exists():
        return "points to an untracked path"
    return "does not exist"


def validate_repository(root: Path) -> tuple[int, int]:
    """Run all repository checks and return tracked-file and link counts."""

    paths = git_tracked_paths(root)
    errors = tracked_pdf_errors(paths)
    link_errors, checked_links = documentation_link_errors(root, paths)
    errors.extend(link_errors)
    if errors:
        raise RepositoryCheckError("\n".join(errors))
    return len(paths), checked_links


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root to inspect",
    )
    args = parser.parse_args()
    try:
        tracked_files, checked_links = validate_repository(args.repository)
    except RepositoryCheckError as error:
        print(f"Repository hygiene check failed:\n{error}", file=sys.stderr)
        return 1
    print(
        "Repository hygiene checks passed "
        f"({tracked_files} tracked files, {checked_links} local documentation file targets)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
