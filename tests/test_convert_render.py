from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.convert import convert_spot_to_cmyk
from tests.conversion_fixtures import separation

_HAS_RENDER_TOOLS = all(shutil.which(command) for command in ("qpdf", "pdftoppm", "gs"))


@unittest.skipUnless(_HAS_RENDER_TOOLS, "qpdf, Poppler, and Ghostscript are required")
class ConvertRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_converted_composite_matches_independent_cmyk_oracle_and_spot_plate_is_gone(
        self,
    ) -> None:
        source = self._make_source()
        oracle = self._make_oracle()
        output = self.root / "converted.pdf"

        convert_spot_to_cmyk(source, output, "DemoSpot", (0, 80, 100, 0))

        checked = subprocess.run(
            ["qpdf", "--check", str(output)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        source_render = self._render_ppm(source, "source")
        converted_render = self._render_ppm(output, "converted")
        oracle_render = self._render_ppm(oracle, "oracle")
        self.assertNotEqual(source_render, oracle_render)
        self.assertEqual(converted_render, oracle_render)

        source_plates = self._plate_names(source, "source-plates")
        converted_plates = self._plate_names(output, "converted-plates")
        self.assertIn("DemoSpot", source_plates)
        self.assertNotIn("DemoSpot", converted_plates)
        self.assertTrue({"Cyan", "Magenta", "Yellow", "Black"} <= converted_plates)

    def _make_source(self) -> Path:
        path = self.root / "source.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page(page_size=(240, 180))
        page.Resources = pikepdf.Dictionary(
            ColorSpace=pikepdf.Dictionary(Ink=separation(alternate_cmyk=(1, 0, 0, 0)))
        )
        page.Contents = pdf.make_stream(
            b"\n".join(
                (
                    b"/Ink cs",
                    b"10 10 80 60 re f",
                    b"0.25 scn",
                    b"110 10 80 60 re f",
                    b"/Ink CS",
                    b"0.5 SCN",
                    b"12 w 20 130 m 210 130 l S",
                )
            )
        )
        pdf.save(path)
        return path

    def _make_oracle(self) -> Path:
        path = self.root / "oracle.pdf"
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page(page_size=(240, 180))
        page.Resources = pikepdf.Dictionary()
        page.Contents = pdf.make_stream(
            b"\n".join(
                (
                    b"0 0.8 1 0 k",
                    b"10 10 80 60 re f",
                    b"0 0.2 0.25 0 k",
                    b"110 10 80 60 re f",
                    b"0 0.8 1 0 K",
                    b"0 0.4 0.5 0 K",
                    b"12 w 20 130 m 210 130 l S",
                )
            )
        )
        pdf.save(path)
        return path

    def _render_ppm(self, path: Path, label: str) -> bytes:
        prefix = self.root / label
        rendered = subprocess.run(
            ["pdftoppm", "-r", "144", "-singlefile", str(path), str(prefix)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        return prefix.with_suffix(".ppm").read_bytes()

    def _plate_names(self, path: Path, label: str) -> set[str]:
        directory = self.root / label
        directory.mkdir()
        prefix = directory / "plate-%d.tif"
        rendered = subprocess.run(
            [
                "gs",
                "-q",
                "-dSAFER",
                "-dBATCH",
                "-dNOPAUSE",
                "-sDEVICE=tiffsep",
                "-r72",
                f"-sOutputFile={prefix}",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        names: set[str] = set()
        for plate in directory.glob("*.tif"):
            if "(" in plate.stem and plate.stem.endswith(")"):
                names.add(plate.stem.rsplit("(", 1)[1][:-1])
        return names


if __name__ == "__main__":
    unittest.main()
