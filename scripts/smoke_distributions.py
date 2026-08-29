"""Install and exercise each built spotpdf distribution in isolation."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from spotpdf import __version__


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one smoke-test command and surface captured output on failure."""

    return subprocess.run(command, check=True, text=True, capture_output=True)


def smoke_archive(archive: Path) -> None:
    """Install one archive and run inventory, rename, and all-spot removal."""

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        environment = root / "venv"
        run(["uv", "venv", str(environment), "--python", sys.executable])
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        executable = environment / (
            "Scripts/spotpdf.exe" if sys.platform == "win32" else "bin/spotpdf"
        )
        run(["uv", "pip", "install", "--python", str(python), str(archive.resolve())])

        source = root / "input.pdf"
        renamed = root / "renamed.pdf"
        output = root / "output.pdf"
        generator = Path(__file__).parents[1] / "examples" / "create_demo_pdf.py"
        run([sys.executable, str(generator), str(source)])
        version = run([str(executable), "--version"])
        before = run([str(executable), "list", str(source)])
        run(
            [
                str(executable),
                "rename",
                str(source),
                "--spot",
                "Varnish",
                "--to",
                "Varnish Renamed",
                "-o",
                str(renamed),
            ]
        )
        after_rename = run([str(executable), "list", str(renamed)])
        run(
            [
                str(executable),
                "remove",
                str(renamed),
                "--all",
                "-o",
                str(output),
            ]
        )
        after = run([str(executable), "list", str(output)])

        if f"spotpdf {__version__}" not in version.stdout:
            raise SystemExit(f"unexpected installed version from {archive.name}")
        if "Varnish" not in before.stdout:
            raise SystemExit(f"demo spot not found with {archive.name}")
        renamed_names = {
            line.split("\t", 1)[0]
            for line in after_rename.stdout.splitlines()
            if "\t" in line and not line.startswith("NAME\t")
        }
        if "Varnish" in renamed_names or "Varnish Renamed" not in renamed_names:
            raise SystemExit(f"demo spot was not renamed with {archive.name}")
        if "No reachable named colorants found." not in after.stdout:
            raise SystemExit(f"named colorants remain after removal with {archive.name}")
        print(f"Smoke test passed: {archive.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing build output")
    args = parser.parse_args()
    archives = sorted([*args.directory.glob("*.whl"), *args.directory.glob("*.tar.gz")])
    if len(archives) != 2:
        raise SystemExit("expected exactly one wheel and one source archive")
    for archive in archives:
        smoke_archive(archive)


if __name__ == "__main__":
    main()
