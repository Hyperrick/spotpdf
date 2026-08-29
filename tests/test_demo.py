from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from examples.create_demo_pdf import build_demo_pdf
from spotpdf.document import inspect_pdf, remove_all_spots


class DemoTests(unittest.TestCase):
    def test_synthetic_demo_exercises_all_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "demo-input.pdf"
            output = root / "demo-output.pdf"

            build_demo_pdf(source)
            before = inspect_pdf(source)
            result = remove_all_spots(source, output)
            after = inspect_pdf(output)

        self.assertEqual(set(before.spots), {"CutContour", "Personalization", "Varnish"})
        self.assertEqual(set(result.spots), set(before.spots))
        self.assertEqual(result.stats.fills_removed, 1)
        self.assertEqual(result.stats.strokes_removed, 1)
        self.assertEqual(result.stats.text_show_operations, 2)
        self.assertEqual(after.spots, {})


if __name__ == "__main__":
    unittest.main()
