"""Enforce the repository's 600-line Python module limit."""

from __future__ import annotations

from pathlib import Path

MAX_LINES = 600
IGNORED_PARTS = {".venv", "build", "dist", "tmp", "__pycache__"}


def main() -> None:
    oversized: list[tuple[Path, int]] = []
    for path in sorted(Path.cwd().rglob("*.py")):
        if any(part in IGNORED_PARTS for part in path.parts):
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > MAX_LINES:
            oversized.append((path, line_count))
    if oversized:
        details = "\n".join(f"{path}: {count} lines" for path, count in oversized)
        raise SystemExit(f"Python files over {MAX_LINES} lines:\n{details}")
    print(f"All Python files are at most {MAX_LINES} lines.")


if __name__ == "__main__":
    main()
