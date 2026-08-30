from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


class ReleaseWorkflowPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        cls.workflow = path.read_text(encoding="utf-8")
        project_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        cls.project = tomllib.loads(project_path.read_text(encoding="utf-8"))

    def _job(self, name: str) -> str:
        match = re.search(
            rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9_]*:\n|\Z)",
            self.workflow,
        )
        self.assertIsNotNone(match, f"missing workflow job {name!r}")
        return match.group(1)

    def _step(self, job_name: str, step_name: str) -> str:
        job = self._job(job_name)
        match = re.search(
            rf"(?ms)^      - name: {re.escape(step_name)}\n"
            rf"(.*?)(?=^      - name: |\Z)",
            job,
        )
        self.assertIsNotNone(match, f"missing {step_name!r} in {job_name!r}")
        return match.group(1)

    def assertPushTagGuard(self, block: str) -> None:  # noqa: N802
        self.assertIn("github.event_name == 'push'", block)
        self.assertIn("github.ref_type == 'tag'", block)
        self.assertIn("startsWith(github.ref_name, 'v')", block)

    def test_every_release_side_effect_is_push_tag_only(self) -> None:
        for job_name in ("public_corpus", "attest_release", "release", "publish_pypi"):
            with self.subTest(job=job_name):
                self.assertPushTagGuard(self._job(job_name).split("    steps:\n", 1)[0])

        for step_name in (
            "Validate release metadata and tag ancestry",
            "Prepare release assets",
            "Upload verified release assets",
            "Upload verified PyPI distributions",
        ):
            with self.subTest(step=step_name):
                self.assertPushTagGuard(self._step("package", step_name))

    def test_pypi_job_is_a_two_step_oidc_boundary(self) -> None:
        job = self._job("publish_pypi")
        self.assertIn("needs: [package, attest_release, release]", job)
        self.assertIn("name: pypi", job)
        self.assertRegex(job, r"(?m)^    permissions:\n      id-token: write$")
        self.assertEqual(job.count("      - name:"), 2)

        forbidden = (
            "actions/checkout",
            "run:",
            "GH_TOKEN",
            "password:",
            "repository-url:",
            "skip-existing",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, job)

        self.assertIn(
            "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2",
            job,
        )
        self.assertTrue(job.rstrip().endswith("packages-dir: dist/"))

    def test_pypi_artifact_contains_only_distributions(self) -> None:
        step = self._step("package", "Upload verified PyPI distributions")
        self.assertIn("-py3-none-any.whl", step)
        self.assertIn(".tar.gz", step)
        self.assertNotIn("SHA256SUMS", step)
        self.assertIn("if-no-files-found: error", step)
        self.assertIn("overwrite: false", step)

    def test_pypi_waits_for_immutable_github_release(self) -> None:
        release = self._job("release")
        self.assertIn("--json isImmutable", release)
        self.assertIn('if [[ "$immutable" != "true" ]]', release)
        self.assertIn("needs: [package, attest_release, release]", self._job("publish_pypi"))

    def test_package_renders_markdown_before_twine_metadata_check(self) -> None:
        render_step = self._step("package", "Render PyPI Markdown long descriptions")
        metadata_step = self._step("package", "Check PyPI metadata")
        render_command = "python scripts/check_pypi_readme.py dist/*.whl dist/*.tar.gz"
        twine_command = "twine check --strict dist/*.whl dist/*.tar.gz"
        self.assertIn(render_command, render_step)
        self.assertIn(twine_command, metadata_step)
        self.assertIn("uv run --no-sync", render_step)
        self.assertIn("uv run --no-sync", metadata_step)
        package = self._job("package")
        self.assertLess(
            package.index("Render PyPI Markdown long descriptions"),
            package.index("Check PyPI metadata"),
        )

    def test_release_environment_installs_pypi_markdown_renderer(self) -> None:
        release = self.project["dependency-groups"]["release"]
        self.assertIn("readme-renderer[md]>=46,<47", release)
        self.assertIn("twine>=7,<8", release)


if __name__ == "__main__":
    unittest.main()
