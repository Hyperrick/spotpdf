"""Regenerate the synthetic README visuals from the current spotpdf CLI."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

try:
    from scripts.docs_image_check import (
        ImageDifference,
        PngFormatError,
        RgbImage,
        compare_rgb_images,
        crop_rgb_image,
        read_rgb_png,
    )
except ModuleNotFoundError:
    from docs_image_check import (
        ImageDifference,
        PngFormatError,
        RgbImage,
        compare_rgb_images,
        crop_rgb_image,
        read_rgb_png,
    )

RASTER_IMAGE_NAMES = ("demo-before.png", "demo-alternate.png", "demo-after.png")
VECTOR_IMAGE_NAMES = ("demo-rename.svg", "demo-convert.svg")
GENERATION_METADATA_NAME = "generation.json"
LARGE_CHANNEL_DELTA = 16
MAX_LARGE_DELTA_PIXEL_FRACTION = 0.025
MAX_MEAN_ABSOLUTE_CHANNEL_DELTA = 2.0
REMOVAL_REGIONS = (
    ("Varnish", (530, 340, 910, 650)),
    ("CutContour", (960, 350, 1350, 630)),
    ("Personalization", (70, 730, 1000, 835)),
)
MIN_REMOVAL_LARGE_DELTA_FRACTION = 0.03
MIN_REMOVAL_MEAN_ABSOLUTE_DELTA = 3.0
MAX_REMOVAL_REGION_LARGE_DELTA_FRACTION = 0.01
MAX_REMOVAL_REGION_MEAN_ABSOLUTE_DELTA = 0.75


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one documentation command and return its captured output."""

    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def create_docs_images(repository: Path, *, destination: Path | None = None) -> None:
    """Create synthetic render comparisons and mutation walkthroughs."""

    renderer = shutil.which("pdftoppm")
    if renderer is None:
        raise SystemExit("Poppler pdftoppm is required to regenerate documentation images")

    images = destination or repository / "docs" / "images"
    images.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "demo.pdf"
        renamed = root / "renamed.pdf"
        alternate = root / "alternate.pdf"
        converted = root / "converted.pdf"
        removed = root / "removed.pdf"
        generator = repository / "examples" / "create_demo_pdf.py"

        run([sys.executable, str(generator), str(source)])
        before = _spotpdf(repository, "list", str(source)).stdout
        rename_result = _spotpdf(
            repository,
            "rename",
            str(source),
            "--spot",
            "Varnish",
            "--to",
            "Varnish Renamed",
            "-o",
            str(renamed),
        ).stdout
        after = _spotpdf(repository, "list", str(renamed)).stdout
        _spotpdf(
            repository,
            "set-alternate",
            str(source),
            "--spot",
            "Varnish",
            "--cmyk",
            "100,0,0,0",
            "-o",
            str(alternate),
        )
        alternate_inventory = _spotpdf(repository, "list", str(alternate)).stdout
        if alternate_inventory != before:
            raise SystemExit("set-alternate documentation inventory changed")
        convert_result = _spotpdf(
            repository,
            "convert",
            str(source),
            "--spot",
            "Varnish",
            "--to-cmyk",
            "0,62,0,0",
            "-o",
            str(converted),
        ).stdout
        converted_inventory = _spotpdf(repository, "list", str(converted)).stdout
        if "Varnish\t" in converted_inventory:
            raise SystemExit("convert documentation output still has the Varnish spot")
        if "CutContour\t" not in converted_inventory:
            raise SystemExit("convert documentation output lost an unrelated spot")
        _spotpdf(repository, "remove", str(source), "--all", "-o", str(removed))
        removed_inventory = _spotpdf(repository, "list", str(removed)).stdout
        if removed_inventory != "No reachable named colorants found.\n":
            raise SystemExit("remove-all documentation output still has named colorants")

        before_png = _render(renderer, source, root / "before")
        renamed_png = _render(renderer, renamed, root / "renamed")
        alternate_png = _render(renderer, alternate, root / "alternate")
        converted_png = _render(renderer, converted, root / "converted")
        removed_png = _render(renderer, removed, root / "after")
        if before_png.read_bytes() != renamed_png.read_bytes():
            raise SystemExit("rename documentation render is not pixel-identical")
        if before_png.read_bytes() == alternate_png.read_bytes():
            raise SystemExit("set-alternate documentation render did not change")
        if before_png.read_bytes() != converted_png.read_bytes():
            raise SystemExit("equivalent convert documentation render is not pixel-identical")
        _verify_removed_regions(before_png, removed_png)

        shutil.copyfile(before_png, images / "demo-before.png")
        shutil.copyfile(alternate_png, images / "demo-alternate.png")
        shutil.copyfile(removed_png, images / "demo-after.png")
        (images / "demo-rename.svg").write_text(
            _rename_svg(before, after, rename_result),
            encoding="utf-8",
            newline="\n",
        )
        (images / "demo-convert.svg").write_text(
            _convert_svg(before, converted_inventory, convert_result),
            encoding="utf-8",
            newline="\n",
        )
        _write_generation_metadata(repository, images / GENERATION_METADATA_NAME)


def check_docs_images(repository: Path) -> None:
    """Regenerate visuals privately and reject meaningful committed-image drift."""

    committed = repository / "docs" / "images"
    _validate_docs_image_set(repository)
    with tempfile.TemporaryDirectory() as temp_dir:
        generated = Path(temp_dir) / "images"
        create_docs_images(repository, destination=generated)
        for name in VECTOR_IMAGE_NAMES + (GENERATION_METADATA_NAME,):
            try:
                if (committed / name).read_bytes() != (generated / name).read_bytes():
                    raise SystemExit(f"generated documentation file is stale: {name}")
            except OSError as error:
                raise SystemExit(f"could not compare documentation file {name}: {error}") from error

        metrics: list[str] = []
        for name in RASTER_IMAGE_NAMES:
            try:
                reference = read_rgb_png(committed / name)
                candidate = read_rgb_png(generated / name)
                difference = compare_rgb_images(
                    reference,
                    candidate,
                    large_channel_delta=LARGE_CHANNEL_DELTA,
                )
            except PngFormatError as error:
                raise SystemExit(str(error)) from error
            if name == "demo-after.png":
                _verify_committed_removal_regions(reference, candidate)
            metrics.append(
                f"{name}: >{LARGE_CHANNEL_DELTA}="
                f"{difference.large_delta_pixel_fraction:.3%}, "
                f"mean={difference.mean_absolute_channel_delta:.3f}"
            )
            if (
                difference.large_delta_pixel_fraction > MAX_LARGE_DELTA_PIXEL_FRACTION
                or difference.mean_absolute_channel_delta > MAX_MEAN_ABSOLUTE_CHANNEL_DELTA
            ):
                raise SystemExit(
                    f"generated documentation PNG is visually stale: {name}; "
                    f">{LARGE_CHANNEL_DELTA} channel delta on "
                    f"{difference.large_delta_pixel_fraction:.3%} of pixels "
                    f"(limit {MAX_LARGE_DELTA_PIXEL_FRACTION:.3%}), mean absolute channel "
                    f"delta {difference.mean_absolute_channel_delta:.3f} "
                    f"(limit {MAX_MEAN_ABSOLUTE_CHANNEL_DELTA:.3f})"
                )
    print("Documentation images are current; " + "; ".join(metrics))


def _validate_docs_image_set(repository: Path) -> None:
    """Require exactly the generated image files to be tracked by Git."""

    expected_names = set(RASTER_IMAGE_NAMES + VECTOR_IMAGE_NAMES + (GENERATION_METADATA_NAME,))
    actual_names = _tracked_docs_image_names(repository)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        unexpected = sorted(actual_names - expected_names)
        raise SystemExit(
            "documentation image set does not match the generator; "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
    untracked_names = _git_docs_image_names(
        repository,
        "--others",
        "--exclude-standard",
        description="non-ignored untracked documentation images",
    )
    if untracked_names:
        raise SystemExit(
            "documentation image directory contains non-ignored untracked files: "
            f"{sorted(untracked_names)!r}"
        )


def _tracked_docs_image_names(repository: Path) -> set[str]:
    """Return tracked paths below docs/images, relative to that directory."""

    return _git_docs_image_names(repository, description="tracked documentation images")


def _git_docs_image_names(
    repository: Path,
    *options: str,
    description: str,
) -> set[str]:
    """Return selected Git paths below docs/images, relative to that directory."""

    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repository),
                "ls-files",
                *options,
                "-z",
                "--",
                "docs/images",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        raise SystemExit(f"could not list {description}: {detail or error}") from error

    prefix = PurePosixPath("docs/images")
    names: set[str] = set()
    for raw_path in completed.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = PurePosixPath(os.fsdecode(raw_path))
        try:
            relative = path.relative_to(prefix)
        except ValueError as error:
            raise SystemExit(
                f"Git returned an unexpected documentation image path: {path}"
            ) from error
        names.add(relative.as_posix())
    return names


def _verify_removed_regions(before: Path, removed: Path) -> None:
    """Require a localized visual change for every removed demo spot plate."""

    try:
        before_image = read_rgb_png(before)
        removed_image = read_rgb_png(removed)
        for spot_name, region in REMOVAL_REGIONS:
            difference = _compare_region(before_image, removed_image, region)
            if (
                difference.large_delta_pixel_fraction < MIN_REMOVAL_LARGE_DELTA_FRACTION
                or difference.mean_absolute_channel_delta < MIN_REMOVAL_MEAN_ABSOLUTE_DELTA
            ):
                raise SystemExit(
                    f"remove-all documentation render retained localized {spot_name} artwork"
                )
    except PngFormatError as error:
        raise SystemExit(str(error)) from error


def _verify_committed_removal_regions(reference: RgbImage, candidate: RgbImage) -> None:
    """Reject a committed removal screenshot with stale localized artwork."""

    for spot_name, region in REMOVAL_REGIONS:
        difference = _compare_region(reference, candidate, region)
        if (
            difference.large_delta_pixel_fraction > MAX_REMOVAL_REGION_LARGE_DELTA_FRACTION
            or difference.mean_absolute_channel_delta > MAX_REMOVAL_REGION_MEAN_ABSOLUTE_DELTA
        ):
            raise SystemExit(
                "generated documentation PNG is visually stale in the "
                f"{spot_name} removal region; >{LARGE_CHANNEL_DELTA} channel delta on "
                f"{difference.large_delta_pixel_fraction:.3%} of pixels "
                f"(limit {MAX_REMOVAL_REGION_LARGE_DELTA_FRACTION:.3%}), mean absolute "
                f"channel delta {difference.mean_absolute_channel_delta:.3f} "
                f"(limit {MAX_REMOVAL_REGION_MEAN_ABSOLUTE_DELTA:.3f})"
            )


def _compare_region(
    reference: RgbImage,
    candidate: RgbImage,
    region: tuple[int, int, int, int],
) -> ImageDifference:
    left, top, right, bottom = region
    return compare_rgb_images(
        crop_rgb_image(
            reference,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        ),
        crop_rgb_image(
            candidate,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        ),
        large_channel_delta=LARGE_CHANNEL_DELTA,
    )


def _write_generation_metadata(repository: Path, destination: Path) -> None:
    inputs = [
        repository / "examples" / "create_demo_pdf.py",
        repository / "pyproject.toml",
        repository / "scripts" / "create_docs_images.py",
        repository / "scripts" / "docs_image_check.py",
        repository / "uv.lock",
        *sorted((repository / "src" / "spotpdf").rglob("*.py")),
    ]
    try:
        digests = {
            path.relative_to(repository).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in inputs
        }
    except OSError as error:
        raise SystemExit(f"could not fingerprint documentation image inputs: {error}") from error
    destination.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sha256_inputs": digests,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _spotpdf(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run(
        [
            sys.executable,
            "-m",
            "spotpdf",
            *arguments,
        ],
        cwd=repository,
    )


def _render(renderer: str, source: Path, prefix: Path) -> Path:
    run(
        [
            renderer,
            "-png",
            "-r",
            "144",
            "-singlefile",
            str(source),
            str(prefix),
        ]
    )
    return prefix.with_suffix(".png")


def _rename_svg(before: str, after: str, rename_result: str) -> str:
    return _mutation_svg(
        before,
        after,
        rename_result,
        title="Atomic spot-plate rename",
        metadata_title="spotpdf atomic rename CLI walkthrough",
        description="The Varnish plate is renamed with a pixel-identical render.",
        subtitle="The plate name changes. Composite pixels, tints, and vector content do not.",
        command='$ spotpdf rename demo.pdf --spot Varnish --to "Varnish Renamed" -o renamed.pdf',
        after_command="$ spotpdf list renamed.pdf",
        badges=(
            (72, 228, 91, "#dcfce7", "#166534", "OLD NAME ABSENT"),
            (318, 230, 337, "#dbeafe", "#1e40af", "NEW NAME PRESENT"),
            (566, 260, 585, "#f3e8ff", "#6b21a8", "PIXEL-IDENTICAL RENDER"),
        ),
    )


def _convert_svg(before: str, after: str, convert_result: str) -> str:
    return _mutation_svg(
        before,
        after,
        convert_result,
        title="Spot plate to explicit DeviceCMYK",
        metadata_title="spotpdf explicit DeviceCMYK conversion CLI walkthrough",
        description="The Varnish plate disappears; its vector paint becomes process CMYK.",
        subtitle="An equivalent recipe keeps this composite render pixel-identical.",
        command=("$ spotpdf convert demo.pdf --spot Varnish --to-cmyk 0,62,0,0 -o converted.pdf"),
        after_command="$ spotpdf list converted.pdf",
        badges=(
            (72, 228, 91, "#dcfce7", "#166534", "SPOT PLATE ABSENT"),
            (318, 230, 337, "#dbeafe", "#1e40af", "CMYK PAINT PRESENT"),
            (566, 260, 585, "#f3e8ff", "#6b21a8", "PIXEL-IDENTICAL RENDER"),
        ),
    )


def _mutation_svg(
    before: str,
    after: str,
    mutation_result: str,
    *,
    title: str,
    metadata_title: str,
    description: str,
    subtitle: str,
    command: str,
    after_command: str,
    badges: tuple[tuple[int, int, int, str, str, str], ...],
) -> str:
    before_lines = _terminal_lines(before)
    after_lines = _terminal_lines(after)
    result = mutation_result.strip().split(";", 1)[0]
    left = _svg_lines(before_lines, x=86, y=326)
    right = _svg_lines(after_lines, x=746, y=326)
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="820" viewBox="0 0 1440 820">',
        f"  <title>{html.escape(metadata_title)}</title>",
        f"  <desc>{html.escape(description)}</desc>",
        '  <rect width="1440" height="820" fill="#f5f7fb"/>',
        '  <rect width="1440" height="168" fill="#0e1422"/>',
        "  " + _svg_text(72, 78, "#ffffff", "sans", 42, title, 700),
        "  " + _svg_text(72, 124, "#c8d0df", "sans", 24, subtitle),
        '  <rect x="70" y="210" width="630" height="438" rx="16" fill="#111827"/>',
        '  <rect x="730" y="210" width="640" height="438" rx="16" fill="#111827"/>',
        "  " + _window_dots(102),
        "  " + _window_dots(762),
        "  " + _svg_text(86, 286, "#93c5fd", "mono", 20, "$ spotpdf list demo.pdf"),
        "  "
        + _svg_text(
            746,
            286,
            "#93c5fd",
            "mono",
            20,
            after_command,
        ),
        "  " + left,
        "  " + right,
        "  " + _svg_text(72, 700, "#111827", "mono", 20, command),
        "  " + _svg_text(72, 738, "#374151", "mono", 17, result),
        *("  " + _badge(*badge) for badge in badges),
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def _svg_text(
    x: int,
    y: int,
    color: str,
    font: str,
    size: int,
    content: str,
    weight: int | None = None,
) -> str:
    family = "Courier New,monospace" if font == "mono" else "Helvetica,Arial,sans-serif"
    weight_attribute = f' font-weight="{weight}"' if weight is not None else ""
    return (
        f'<text x="{x}" y="{y}" fill="{color}" font-family="{family}" '
        f'font-size="{size}"{weight_attribute}>{html.escape(content)}</text>'
    )


def _window_dots(start_x: int) -> str:
    colors = ("#fb7185", "#fbbf24", "#34d399")
    return "".join(
        f'<circle cx="{start_x + index * 24}" cy="242" r="7" fill="{color}"/>'
        for index, color in enumerate(colors)
    )


def _badge(
    rect_x: int,
    width: int,
    text_x: int,
    background: str,
    foreground: str,
    label: str,
) -> str:
    rectangle = (
        f'<rect x="{rect_x}" y="768" width="{width}" height="34" rx="17" fill="{background}"/>'
    )
    text = _svg_text(text_x, 791, foreground, "sans", 16, label, 700)
    return rectangle + text


def _terminal_lines(output: str) -> list[str]:
    rows = [line.split("\t") for line in output.strip().splitlines()]
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    return [
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip()
        for row in rows
    ]


def _svg_lines(lines: list[str], *, x: int, y: int) -> str:
    return "\n  ".join(
        f'<text x="{x}" y="{y + index * 42}" fill="#e5e7eb" '
        f'font-family="Courier New,monospace" font-size="15">{html.escape(line)}</text>'
        for index, line in enumerate(lines)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="spotpdf repository root",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate privately and compare with the committed visuals",
    )
    args = parser.parse_args()
    repository = args.repository.resolve()
    if args.check:
        check_docs_images(repository)
    else:
        create_docs_images(repository)


if __name__ == "__main__":
    main()
