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
    """Install one archive and run inventory plus atomic all-spot removal."""

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        environment = root / "venv"
        run(["uv", "venv", str(environment), "--python", sys.executable])
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        run(["uv", "pip", "install", "--python", str(python), str(archive.resolve())])

        source = root / "input.pdf"
        output = root / "output.pdf"
        generator = Path(__file__).parents[1] / "examples" / "create_demo_pdf.py"
        run([sys.executable, str(generator), str(source)])
        version = run([str(python), "-m", "spotpdf", "--version"])
        before = run([str(python), "-m", "spotpdf", "list", str(source)])
        run(
            [
                str(python),
                "-m",
                "spotpdf",
                "remove",
                str(source),
                "--all",
                "-o",
                str(output),
            ]
        )
        after = run([str(python), "-m", "spotpdf", "list", str(output)])

        if f"spotpdf {__version__}" not in version.stdout:
            raise SystemExit(f"unexpected installed version from {archive.name}")
        if "Varnish" not in before.stdout:
            raise SystemExit(f"demo spot not found with {archive.name}")
        if "No reachable spot colors found." not in after.stdout:
            raise SystemExit(f"spots remain after removal with {archive.name}")
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
