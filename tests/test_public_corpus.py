from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.public_corpus import load_manifest, obtain_case


class PublicCorpusManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Path(__file__).resolve().parents[1]
        self.cases = load_manifest(self.repository / "corpus" / "manifest.toml")

    def test_manifest_covers_distinct_remove_rename_and_process_cases(self) -> None:
        self.assertEqual(len(self.cases), 6)
        self.assertEqual({case.operation for case in self.cases}, {"remove-all", "rename"})
        self.assertTrue(any(case.same_composite for case in self.cases))
        self.assertTrue(any(case.byte_identical for case in self.cases))
        self.assertEqual(sum(case.size for case in self.cases), 910891)

    def test_offline_mode_rejects_missing_or_unverified_files(self) -> None:
        case = self.cases[0]
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = Path(temp_dir)
            with self.assertRaisesRegex(RuntimeError, "verified offline corpus file"):
                obtain_case(case, cache, offline=True)
            (cache / case.filename).write_bytes(b"not the pinned PDF")
            with self.assertRaisesRegex(RuntimeError, "verified offline corpus file"):
                obtain_case(case, cache, offline=True)


if __name__ == "__main__":
    unittest.main()
