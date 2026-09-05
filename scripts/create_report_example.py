"""Capture real report UI screenshots, or verify their recorded source provenance."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

SCREENSHOTS = ("finding.png", "page-location.png")
CLI_VERSION = "0.1.19"


def inputs(repository: Path) -> dict[str, str]:
    paths = [
        repository / "examples/create_report_demo.py",
        *sorted((repository / "examples/report_demo").glob("*.py")),
        *sorted((repository / "src/spotpdf").glob("report_*.py")),
        repository / "src/spotpdf/diagnostics.py",
        repository / "scripts/create_report_example.py",
        repository / "pyproject.toml",
        repository / "uv.lock",
    ]
    return {p.relative_to(repository).as_posix(): digest(p) for p in paths}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(repository: Path, output: Path) -> None:
    metadata = json.loads((output / "capture.json").read_text())
    if metadata["sha256_inputs"] != inputs(repository):
        raise SystemExit("Report example sources changed; recapture the browser screenshots")
    for name in SCREENSHOTS:
        if digest(output / name) != metadata["screenshots"][name]["sha256"]:
            raise SystemExit(f"Report example screenshot differs from its capture: {name}")
        with Image.open(output / name) as image:
            if list(image.size) != metadata["screenshots"][name]["size"]:
                raise SystemExit(f"Report example screenshot dimensions changed: {name}")
    print("Report example screenshot provenance and image integrity are current.")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def capture(repository: Path, output: Path) -> None:
    if shutil.which("npx") is None:
        raise SystemExit("Screenshot capture needs Node.js/npm (npx). --check needs no browser.")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spotpdf-readme-report-") as name:
        work = Path(name)
        source, report = work / "nord-coffee.pdf", work / "report.html"
        subprocess.run(
            [sys.executable, str(repository / "examples/create_report_demo.py"), str(source)],
            cwd=repository,
            check=True,
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "spotpdf",
                "remove",
                str(source),
                "--spot",
                "FOIL_GOLD",
                "--dry-run",
                "--report",
                str(report),
                "--format",
                "json",
            ],
            cwd=repository,
            capture_output=True,
            text=True,
        )
        if result.returncode != 1:
            raise SystemExit(f"Expected the synthetic spot-image refusal: {result.stdout}")
        payload = json.loads(result.stderr)
        findings = payload["error"]["details"]["findings"]
        boxes = sorted(
            (o["page"], o["bbox"])
            for f in findings
            for o in f["occurrences"]
            if o.get("accuracy") == "object bounds"
        )
        if boxes != [(1, [434, 480, 542, 588]), (2, [440, 104, 522, 186])]:
            raise SystemExit(f"Demo localization changed: {boxes}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(work)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cli = [
            "npx",
            "--yes",
            "--package",
            f"@playwright/cli@{CLI_VERSION}",
            "playwright-cli",
            "-s=spotpdf-docs-report",
        ]

        def browser(*arguments):
            result = subprocess.run(
                [*cli, *arguments], cwd=work, capture_output=True, text=True, timeout=120
            )
            if result.returncode or "### Error" in result.stdout:
                raise RuntimeError(result.stdout + result.stderr)
            return result.stdout

        try:
            browser("open", f"http://127.0.0.1:{server.server_port}/report.html")
            browser("resize", "1040", "1100")
            browser("snapshot")
            browser(
                "run-code",
                """async page => {
                await page.getByRole('heading', {name:'Locate the problem.'}).waitFor();
                await page.locator('#finding-1 .crop').waitFor();
                await page.evaluate(() => document.fonts.ready);
            }""",
            )
            browser("network-state-set", "offline")
            for selector, filename in [
                ("#finding-1", "finding.png"),
                ("#page-1", "page-location.png"),
            ]:
                # Capture the actual element without changing its DOM, CSS, or contents.
                code = (
                    "async page => { await page.locator("
                    + json.dumps(selector)
                    + ").screenshot({path:"
                    + json.dumps(str(output / filename))
                    + ', animations:"disabled", scale:"css"}); }'
                )
                browser("run-code", code)
        finally:
            try:
                browser("close")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
        screenshots = {}
        for filename in SCREENSHOTS:
            with Image.open(output / filename) as image:
                screenshots[filename] = {
                    "sha256": digest(output / filename),
                    "size": list(image.size),
                }
        metadata = {
            "schema_version": 1,
            "sha256_inputs": inputs(repository),
            "screenshots": screenshots,
            "playwright_cli": CLI_VERSION,
            "pdfium": importlib.metadata.version("pypdfium2"),
            "viewport": [1040, 1100],
            "offline": True,
            "expected_locations": boxes,
        }
        (output / "capture.json").write_text(json.dumps(metadata, indent=2) + "\n")
    check(repository, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify snapshots without a browser")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    output = repository / "docs/report-example"
    if args.check:
        check(repository, output)
    else:
        capture(repository, output)


if __name__ == "__main__":
    main()
