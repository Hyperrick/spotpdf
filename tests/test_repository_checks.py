from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from scripts.check_repository import (
    MAX_SRCSET_CANDIDATES,
    RepositoryCheckError,
    _srcset_urls,
    documentation_link_errors,
    git_tracked_paths,
    tracked_pdf_errors,
)


class RepositoryCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _write(self, relative: str, content: str = "fixture\n") -> PurePosixPath:
        path = self.root.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return PurePosixPath(relative)

    def test_accepts_tracked_relative_markdown_and_html_links(self) -> None:
        readme = self._write(
            "README.md",
            "[Guide](docs/guide.md#usage)\n"
            "![Example](docs/images/example%20image.png?raw=1)\n"
            '<a href="LICENSE">License</a>\n'
            "<img src='docs/images/example&amp;image.png'>\n"
            "<a href=docs/guide.md>Unquoted guide</a>\n"
            "\n[Reference][guide]\n\n"
            "[guide]: <docs/guide.md>\n\n"
            "[External](https://example.com/file.md)\n"
            "[Section](#usage)\n"
            '<a href="">Current document</a>\n',
        )
        guide = self._write("docs/guide.md")
        image = self._write("docs/images/example image.png")
        html_image = self._write("docs/images/example&image.png")
        license_path = self._write("LICENSE")

        errors, checked = documentation_link_errors(
            self.root,
            (readme, guide, image, html_image, license_path),
        )

        self.assertEqual(errors, [])
        self.assertEqual(checked, 7)

    def test_ignores_links_inside_code_and_comments(self) -> None:
        readme = self._write(
            "README.md",
            "`[inline](missing-one.md)`\n\n"
            "```markdown\n[example](missing-two.md)\n```\n\n"
            "<!-- [hidden](missing-three.md) -->\n"
            '<!-- <a href="missing-four.md">hidden HTML</a> -->\n'
            "<script>const example = 'href=\"missing-five.md\"';</script>\n",
        )

        errors, checked = documentation_link_errors(self.root, (readme,))

        self.assertEqual(errors, [])
        self.assertEqual(checked, 0)

    def test_ignores_links_inside_indented_code(self) -> None:
        readme = self._write(
            "README.md",
            "Example:\n\n    [not a link](missing.md)\n",
        )

        errors, checked = documentation_link_errors(self.root, (readme,))

        self.assertEqual(errors, [])
        self.assertEqual(checked, 0)

    def test_commonmark_fences_mask_longer_closers_but_not_invalid_info(self) -> None:
        readme = self._write(
            "README.md",
            "~~~markdown\n[Example](missing-example.md)\n~~~~\n"
            "```invalid`info\n[Rendered](missing-rendered.md)\n```\n```\n",
        )

        errors, checked = documentation_link_errors(self.root, (readme,))

        self.assertEqual(checked, 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("'missing-rendered.md' does not exist", errors[0])

    def test_commonmark_closes_an_unclosed_fence_at_end_of_document(self) -> None:
        readme = self._write("README.md", "```markdown\n[Example](missing.md)\n")

        errors, checked = documentation_link_errors(self.root, (readme,))

        self.assertEqual(checked, 0)
        self.assertEqual(errors, [])

    def test_reports_missing_untracked_and_escaping_targets(self) -> None:
        readme = self._write(
            "docs/README.md",
            "[Missing](missing.md)\n[Private](private.png)\n[Outside](../../outside.md)\n",
        )
        self._write("docs/private.png")
        outside = self.root.parent / f"{self.root.name}-outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)

        errors, checked = documentation_link_errors(self.root, (readme,))

        self.assertEqual(checked, 3)
        self.assertIn("'missing.md' does not exist", errors[0])
        self.assertIn("'private.png' points to an untracked path", errors[1])
        self.assertIn("'../../outside.md' escapes the repository", errors[2])

    def test_unquoted_html_missing_target_is_reported(self) -> None:
        readme = self._write("README.md", "<img src=docs/missing.png>\n")

        errors, checked = documentation_link_errors(self.root, (readme,))

        self.assertEqual(checked, 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("'docs/missing.png' does not exist", errors[0])

    def test_html_srcset_candidates_are_checked_without_splitting_url_commas(self) -> None:
        readme = self._write(
            "README.md",
            '<img src="docs/base.png" '
            'srcset="docs/small.png 1x, docs/missing-large.png 2x">\n'
            '<source srcset="data:image/svg+xml,&lt;svg&gt; 1x, docs/a%2Cb.png 2x">\n',
        )
        base = self._write("docs/base.png")
        small = self._write("docs/small.png")
        comma = self._write("docs/a,b.png")

        errors, checked = documentation_link_errors(self.root, (readme, base, small, comma))

        self.assertEqual(checked, 4)
        self.assertEqual(len(errors), 1)
        self.assertIn("'docs/missing-large.png' does not exist", errors[0])

    def test_srcset_candidate_count_is_bounded(self) -> None:
        value = ", ".join(f"image-{index}.png 1x" for index in range(MAX_SRCSET_CANDIDATES + 1))

        with self.assertRaisesRegex(RepositoryCheckError, "candidate limit"):
            _srcset_urls(value)

    def test_fragments_are_out_of_scope_but_their_file_paths_are_checked(self) -> None:
        readme = self._write(
            "README.md",
            "[Same file](#missing-anchor)\n"
            "[Existing file](docs/guide.md#missing-anchor)\n"
            "[Missing file](docs/missing.md#ignored-anchor)\n",
        )
        guide = self._write("docs/guide.md")

        errors, checked = documentation_link_errors(self.root, (readme, guide))

        self.assertEqual(checked, 2)
        self.assertEqual(len(errors), 1)
        self.assertIn("'docs/missing.md#ignored-anchor' does not exist", errors[0])

    def test_multiline_inline_links_report_the_container_line_range(self) -> None:
        readme = self._write(
            "README.md",
            "[First](missing-one.md)\ncontinued [Second](missing-two.md)\n",
        )

        errors, checked = documentation_link_errors(self.root, (readme,))

        self.assertEqual(checked, 2)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(error.startswith("README.md:1-2:") for error in errors))

    def test_multiline_html_block_reports_the_exact_attribute_line(self) -> None:
        readme = self._write(
            "README.md",
            "<div>\n<img src=docs/missing.png>\n</div>\n",
        )

        errors, checked = documentation_link_errors(self.root, (readme,))

        self.assertEqual(checked, 1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("README.md:2:"))

    def test_links_to_repository_root_are_valid(self) -> None:
        readme = self._write("README.md", "[Root](.)\n")
        guide = self._write("docs/guide.md", "[Root](..)\n")

        errors, checked = documentation_link_errors(self.root, (readme, guide))

        self.assertEqual(errors, [])
        self.assertEqual(checked, 2)

    def test_balanced_and_escaped_parentheses_in_destinations_are_valid(self) -> None:
        readme = self._write(
            "README.md",
            "[Balanced](docs/a(b).md)\n[Escaped](docs/a\\(b\\).md)\n",
        )
        target = self._write("docs/a(b).md")

        errors, checked = documentation_link_errors(self.root, (readme, target))

        self.assertEqual(errors, [])
        self.assertEqual(checked, 2)

    def test_list_containers_and_nested_or_escaped_labels_are_parsed(self) -> None:
        readme = self._write(
            "README.md",
            "- item\n\n"
            "    [List link](missing-list.md)\n\n"
            "[Escaped \\]](missing-escaped.md)\n"
            "[Nested [label]](missing-nested.md)\n",
        )

        errors, checked = documentation_link_errors(self.root, (readme,))

        self.assertEqual(checked, 3)
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("missing-list.md" in error for error in errors))
        self.assertTrue(any("missing-escaped.md" in error for error in errors))
        self.assertTrue(any("missing-nested.md" in error for error in errors))

    def test_multiline_reference_and_destination_entity_are_normalized(self) -> None:
        readme = self._write(
            "README.md",
            "[Use][ref]\n\n[ref]:\n  missing.md\n\n[Entity](docs/a&amp;b.md)\n",
        )
        entity_target = self._write("docs/a&b.md")

        errors, checked = documentation_link_errors(self.root, (readme, entity_target))

        self.assertEqual(checked, 3)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("missing.md" in error for error in errors))

    def test_tracked_pdf_detection_is_case_insensitive(self) -> None:
        errors = tracked_pdf_errors(
            (
                PurePosixPath("docs/example.PDF"),
                PurePosixPath("fixtures/input.PdF"),
                PurePosixPath("docs/pdf-format.md"),
            )
        )

        self.assertEqual(
            errors,
            [
                "tracked PDF is forbidden: docs/example.PDF",
                "tracked PDF is forbidden: fixtures/input.PdF",
            ],
        )

    def test_git_listing_excludes_ignored_and_untracked_files(self) -> None:
        self._write(".gitignore", "private/\n")
        self._write("README.md")
        self._write("private/customer.pdf")
        self._write("notes.txt")
        subprocess.run(["git", "init", "--quiet", self.root], check=True)
        subprocess.run(
            ["git", "-C", self.root, "add", ".gitignore", "README.md"],
            check=True,
        )

        self.assertEqual(
            git_tracked_paths(self.root),
            (PurePosixPath(".gitignore"), PurePosixPath("README.md")),
        )


if __name__ == "__main__":
    unittest.main()
