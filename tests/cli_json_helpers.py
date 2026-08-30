from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pikepdf

from examples.create_demo_pdf import build_demo_pdf
from spotpdf.scan import MAX_FORM_NESTING

PROJECT_ROOT = Path(__file__).parents[1]


class JsonCliTestCase(unittest.TestCase):
    """Shared subprocess contract assertions for JSON CLI tests."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic demo ü.pdf"
        build_demo_pdf(self.source)

    def _run(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._command(arguments),
            cwd=PROJECT_ROOT,
            env=self._environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            timeout=30,
        )

    def _run_bytes(self, *arguments: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            self._command(arguments),
            cwd=PROJECT_ROOT,
            env=self._environment(),
            capture_output=True,
            text=False,
            check=False,
            timeout=30,
        )

    def _success(
        self,
        completed: subprocess.CompletedProcess[str],
        *,
        command: str,
        exit_code: int = 0,
    ) -> dict[str, object]:
        self.assertEqual(completed.returncode, exit_code, completed.stderr)
        self.assertEqual(completed.stderr, "")
        payload = self._canonical_payload(completed.stdout)
        self.assertEqual(payload["schema_version"], "spotpdf.cli/v1")
        self.assertIsInstance(payload["spotpdf_version"], str)
        self.assertEqual(payload["command"], command)
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["exit_code"], exit_code)
        self.assertIn("result", payload)
        self.assertNotIn("error", payload)
        return payload

    def _error(
        self,
        completed: subprocess.CompletedProcess[str],
        *,
        code: str,
        exit_code: int = 1,
    ) -> dict[str, object]:
        self.assertEqual(completed.returncode, exit_code)
        self.assertEqual(completed.stdout, "")
        payload = self._canonical_payload(completed.stderr)
        self.assertEqual(payload["schema_version"], "spotpdf.cli/v1")
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["exit_code"], exit_code)
        self.assertEqual(payload["error"]["code"], code)
        self.assertNotIn("result", payload)
        return payload

    def _canonical_payload(self, raw: str) -> dict[str, object]:
        self.assertTrue(raw.endswith("\n"))
        self.assertEqual(raw.count("\n"), 1)
        payload = json.loads(raw)
        self.assertEqual(
            raw,
            json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
        )
        return payload

    @staticmethod
    def _stats(
        *,
        changed: bool = True,
        pages_changed: list[int] | None = None,
        forms_changed: int = 0,
        text_blocks: int = 0,
        text_show_operations: int = 0,
        fills_removed: int = 0,
        strokes_removed: int = 0,
        resources_removed: int = 0,
    ) -> dict[str, object]:
        return {
            "changed": changed,
            "pages_changed": [1] if pages_changed is None else pages_changed,
            "forms_changed": forms_changed,
            "text_blocks": text_blocks,
            "text_show_operations": text_show_operations,
            "fills_removed": fills_removed,
            "strokes_removed": strokes_removed,
            "resources_removed": resources_removed,
        }

    def _make_devicen_pdf(self, path: Path) -> None:
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            function = pikepdf.Dictionary(
                FunctionType=2,
                Domain=pikepdf.Array([0, 1]),
                C0=pikepdf.Array([0, 0, 0, 0]),
                C1=pikepdf.Array([1, 0, 0, 0]),
                N=1,
            )
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Mixed=pikepdf.Array(
                        [
                            pikepdf.Name.DeviceN,
                            pikepdf.Array([pikepdf.Name.DemoSpot, pikepdf.Name.Cyan]),
                            pikepdf.Name.DeviceCMYK,
                            function,
                        ]
                    )
                )
            )
            page.Contents = pdf.make_stream(b"/Mixed cs 1 1 scn 0 0 10 10 re f\n")
            pdf.save(path)

    def _make_deep_form_pdf(self, path: Path) -> None:
        with pikepdf.Pdf.new() as pdf:
            nested = self._form(
                pdf,
                b"/Target cs 1 scn 0 0 10 10 re f\n",
                pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Target=self._separation())),
            )
            for _ in range(MAX_FORM_NESTING + 1):
                nested = self._form(
                    pdf,
                    b"/Next Do\n",
                    pikepdf.Dictionary(XObject=pikepdf.Dictionary(Next=nested)),
                )
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Root=nested))
            page.Contents = pdf.make_stream(b"/Root Do\n")
            pdf.save(path)

    @staticmethod
    def _form(
        pdf: pikepdf.Pdf,
        content: bytes,
        resources: pikepdf.Dictionary,
    ) -> pikepdf.Stream:
        form = pdf.make_stream(content)
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 100, 100])
        form.Resources = resources
        return form

    @staticmethod
    def _separation() -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name.DemoSpot,
                pikepdf.Name.DeviceCMYK,
                pikepdf.Dictionary(
                    FunctionType=2,
                    Domain=pikepdf.Array([0, 1]),
                    C0=pikepdf.Array([0, 0, 0, 0]),
                    C1=pikepdf.Array([0, 1, 1, 0]),
                    N=1,
                ),
            ]
        )

    @staticmethod
    def _environment() -> dict[str, str]:
        environment = os.environ.copy()
        source_path = str(PROJECT_ROOT / "src")
        existing_path = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_path if not existing_path else source_path + os.pathsep + existing_path
        )
        environment["PYTHONIOENCODING"] = "utf-8"
        return environment

    @staticmethod
    def _command(arguments: tuple[object, ...]) -> list[str]:
        return [sys.executable, "-m", "spotpdf", *(str(item) for item in arguments)]
