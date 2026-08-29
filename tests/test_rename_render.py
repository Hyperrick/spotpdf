from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pikepdf

from spotpdf.document import inspect_pdf
from spotpdf.model import NameDependencyKind
from spotpdf.rename import rename_spot


class RenameRenderTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pdftoppm"), "Poppler pdftoppm is required")
    def test_composite_render_is_pixel_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "before.pdf"
            output = root / "after.pdf"
            self._make_vector_pdf(source)

            rename_spot(source, output, "Old Preview", "New/Preview")

            before = self._render_ppm(source, root / "before")
            after = self._render_ppm(output, root / "after")

        self.assertEqual(after, before)

    def test_prepress_dependencies_are_renamed_and_value_type_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for as_string in (False, True):
                with self.subTest(as_string=as_string):
                    source = root / f"prepress-{as_string}-input.pdf"
                    output = root / f"prepress-{as_string}-output.pdf"
                    self._make_prepress_pdf(source, as_string=as_string)

                    rename_spot(source, output, "Old", "New")

                    report = inspect_pdf(output)
                    self.assertFalse(any(item.name == "Old" for item in report.dependencies))
                    kinds = {item.kind for item in report.dependencies if item.name == "New"}
                    self.assertEqual(
                        kinds,
                        {
                            NameDependencyKind.SEPARATION_INFO,
                            NameDependencyKind.PRINTER_MARK_COLORANT,
                        },
                    )
                    with pikepdf.open(output) as pdf:
                        device_colorant = pdf.pages[0].SeparationInfo.DeviceColorant
                        self.assertEqual(str(device_colorant).lstrip("/"), "New")
                        expected_type = pikepdf.String if as_string else pikepdf.Name
                        self.assertIsInstance(device_colorant, expected_type)
                        mark = pdf.pages[0].Annots[0].AP.N
                        self.assertIn(pikepdf.Name.New, mark.Colorants)
                        self.assertEqual(str(mark.Colorants.New[1]), "/New")

    @staticmethod
    def _make_vector_pdf(path: Path) -> None:
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(144, 144))
            page.Resources = pikepdf.Dictionary(
                ColorSpace=pikepdf.Dictionary(
                    Ink=pikepdf.Array(
                        [
                            pikepdf.Name.Separation,
                            pikepdf.Name("/Old Preview"),
                            pikepdf.Name.DeviceCMYK,
                            pikepdf.Dictionary(
                                FunctionType=2,
                                Domain=pikepdf.Array([0, 1]),
                                C0=pikepdf.Array([0, 0, 0, 0]),
                                C1=pikepdf.Array([0, 0.8, 1, 0]),
                                N=1,
                            ),
                        ]
                    )
                )
            )
            page.Contents = pdf.make_stream(
                b"/Ink cs\n"
                b"0 scn 0 0 36 144 re f\n"
                b"0.25 scn 36 0 36 144 re f\n"
                b"0.5 scn 72 0 36 144 re f\n"
                b"1 scn 108 0 36 144 re f\n"
                b"/Ink CS 0.75 SCN 4 w 8 8 128 128 re S\n"
            )
            pdf.save(path)

    @classmethod
    def _make_prepress_pdf(cls, path: Path, *, as_string: bool) -> None:
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(page_size=(100, 100))
            separation = pdf.make_indirect(cls._separation("Old"))
            page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Ink=separation))
            page.Contents = pdf.make_stream(b"/Ink cs 0.5 scn 0 0 20 20 re f\n")
            device_colorant = pikepdf.String("Old") if as_string else pikepdf.Name.Old
            page.SeparationInfo = pikepdf.Dictionary(
                Pages=pikepdf.Array([page.obj]),
                DeviceColorant=device_colorant,
                ColorSpace=separation,
            )

            mark = cls._form(pdf)
            mark.Colorants = pikepdf.Dictionary(Old=cls._separation("Old"))
            printer_mark = pikepdf.Dictionary(
                Type=pikepdf.Name.Annot,
                Subtype=pikepdf.Name.PrinterMark,
                F=68,
                Rect=pikepdf.Array([0, 0, 10, 10]),
                AP=pikepdf.Dictionary(N=mark),
            )

            page.Annots = pikepdf.Array([printer_mark])
            pdf.save(path, min_version="1.4")

    @staticmethod
    def _separation(name: str) -> pikepdf.Array:
        return pikepdf.Array(
            [
                pikepdf.Name.Separation,
                pikepdf.Name(f"/{name}"),
                pikepdf.Name.DeviceCMYK,
                pikepdf.Dictionary(
                    FunctionType=2,
                    Domain=pikepdf.Array([0, 1]),
                    C0=pikepdf.Array([0, 0, 0, 0]),
                    C1=pikepdf.Array([0, 0.8, 1, 0]),
                    N=1,
                ),
            ]
        )

    @staticmethod
    def _form(pdf: pikepdf.Pdf) -> pikepdf.Stream:
        form = pdf.make_stream(b"")
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 10, 10])
        form.Resources = pikepdf.Dictionary()
        return form

    @staticmethod
    def _render_ppm(path: Path, prefix: Path) -> bytes:
        subprocess.run(
            [
                "pdftoppm",
                "-q",
                "-r",
                "144",
                "-singlefile",
                "-aa",
                "no",
                "-aaVector",
                "no",
                str(path),
                str(prefix),
            ],
            check=True,
        )
        return prefix.with_suffix(".ppm").read_bytes()


if __name__ == "__main__":
    unittest.main()
