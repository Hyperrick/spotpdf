"""Install and exercise each built spotpdf distribution in isolation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from spotpdf import __version__


def run(command: list[str], *, expected_exit: int = 0) -> subprocess.CompletedProcess[str]:
    """Run one smoke-test command and surface captured output on failure."""

    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.returncode != expected_exit:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def json_record(
    completed: subprocess.CompletedProcess[str],
    *,
    command: str,
    expected_exit: int,
    success: bool,
) -> dict[str, object]:
    """Validate one canonical installed-CLI JSON record and its stream."""

    raw = completed.stdout if success else completed.stderr
    other = completed.stderr if success else completed.stdout
    if other or raw.count("\n") != 1 or not raw.endswith("\n"):
        raise SystemExit(f"invalid JSON stream contract for installed {command}")
    payload = json.loads(raw)
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if raw != canonical + "\n":
        raise SystemExit(f"non-canonical installed JSON record for {command}")
    if (
        payload.get("schema_version") != "spotpdf.cli/v1"
        or payload.get("spotpdf_version") != __version__
        or payload.get("command") != command
        or payload.get("exit_code") != expected_exit
        or payload.get("ok") is not success
    ):
        raise SystemExit(f"invalid installed JSON envelope for {command}")
    return payload


def smoke_archive(archive: Path) -> None:
    """Install one archive and exercise text, JSON, and every mutation command."""

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
        alternate = root / "alternate.pdf"
        renamed = root / "renamed.pdf"
        converted = root / "converted.pdf"
        output = root / "output.pdf"
        generator = Path(__file__).parents[1] / "examples" / "create_demo_pdf.py"
        run([sys.executable, str(generator), str(source)])
        version = run([str(executable), "--version"])
        before = run([str(executable), "list", str(source)])
        before_json = run([str(executable), "--format", "json", "list", str(source)])
        check_json = run(
            [str(executable), "check", str(source), "--spot", "Varnish", "--format", "json"],
            expected_exit=2,
        )
        usage_json = run(
            [str(executable), "list", "--format", "json"],
            expected_exit=64,
        )
        alternate_result = run(
            [
                str(executable),
                "set-alternate",
                str(source),
                "--spot",
                "Varnish",
                "--cmyk",
                "100,0,0,0",
                "-o",
                str(alternate),
            ]
        )
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
        convert_result = run(
            [
                str(executable),
                "convert",
                str(source),
                "--spot",
                "Varnish",
                "--to-cmyk",
                "0,62,0,0",
                "-o",
                str(converted),
            ]
        )
        after_convert = run([str(executable), "list", str(converted)])
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
        json_result = json_record(
            before_json,
            command="list",
            expected_exit=0,
            success=True,
        )
        json_names = [item["name"] for item in json_result["result"]["colorants"]]
        if json_names != ["CutContour", "Personalization", "Varnish"] or json_result["result"].get(
            "input"
        ) != str(source):
            raise SystemExit(f"JSON inventory contract failed with {archive.name}")
        check_result = json_record(
            check_json,
            command="check",
            expected_exit=2,
            success=True,
        )
        if check_result["result"].get("present") is not True:
            raise SystemExit(f"JSON check contract failed with {archive.name}")
        usage_result = json_record(
            usage_json,
            command="list",
            expected_exit=64,
            success=False,
        )
        if usage_result["error"].get("code") != "usage_error":
            raise SystemExit(f"JSON usage contract failed with {archive.name}")
        if "no process conversion performed" not in alternate_result.stdout:
            raise SystemExit(f"alternate-preview command failed with {archive.name}")
        renamed_names = {
            line.split("\t", 1)[0]
            for line in after_rename.stdout.splitlines()
            if "\t" in line and not line.startswith("NAME\t")
        }
        if "Varnish" in renamed_names or "Varnish Renamed" not in renamed_names:
            raise SystemExit(f"demo spot was not renamed with {archive.name}")
        converted_names = {
            line.split("\t", 1)[0]
            for line in after_convert.stdout.splitlines()
            if "\t" in line and not line.startswith("NAME\t")
        }
        if "Converted 'Varnish' paint to explicit DeviceCMYK 0,62,0,0" not in (
            convert_result.stdout
        ):
            raise SystemExit(f"conversion command failed with {archive.name}")
        if "Varnish" in converted_names or "CutContour" not in converted_names:
            raise SystemExit(f"conversion inventory is incorrect with {archive.name}")
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
