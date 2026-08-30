from __future__ import annotations

import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

from scripts.create_docs_images import (
    GENERATION_METADATA_NAME,
    RASTER_IMAGE_NAMES,
    REMOVAL_REGIONS,
    VECTOR_IMAGE_NAMES,
    _validate_docs_image_set,
    _verify_committed_removal_regions,
    _verify_removed_regions,
)
from scripts.docs_image_check import (
    PngFormatError,
    RgbImage,
    compare_rgb_images,
    crop_rgb_image,
    read_rgb_png,
)


class DocumentationImageCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_decodes_every_png_row_filter(self) -> None:
        rows = (
            bytes((10, 20, 30, 40, 50, 60)),
            bytes((12, 22, 32, 42, 52, 62)),
            bytes((14, 24, 34, 44, 54, 64)),
            bytes((16, 26, 36, 46, 56, 66)),
            bytes((18, 28, 38, 48, 58, 68)),
        )
        path = self.root / "filters.png"
        _write_png(path, rows, filter_types=(0, 1, 2, 3, 4))

        image = read_rgb_png(path)

        self.assertEqual((image.width, image.height), (2, 5))
        self.assertEqual(image.pixels, b"".join(rows))

    def test_rejects_corrupt_png_checksum(self) -> None:
        path = self.root / "corrupt.png"
        _write_png(path, (bytes((1, 2, 3)),), filter_types=(0,))
        data = bytearray(path.read_bytes())
        data[-5] ^= 1
        path.write_bytes(data)

        with self.assertRaisesRegex(PngFormatError, "checksum"):
            read_rgb_png(path)

    def test_rejects_unknown_critical_chunk_by_png_reserved_bit(self) -> None:
        path = self.root / "critical.png"
        _write_png(path, (bytes((1, 2, 3)),), filter_types=(0,))
        data = path.read_bytes()
        path.write_bytes(data[:-12] + _chunk(b"AbCd", b"") + data[-12:])

        with self.assertRaisesRegex(PngFormatError, "unsupported critical"):
            read_rgb_png(path)

    def test_rejects_checksum_valid_invalid_compressed_pixels(self) -> None:
        path = self.root / "invalid-idat.png"
        header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", b"not a zlib stream")
            + _chunk(b"IEND", b"")
        )

        with self.assertRaisesRegex(PngFormatError, "compressed PNG pixels"):
            read_rgb_png(path)

    def test_measures_large_pixels_and_mean_channel_delta(self) -> None:
        reference = RgbImage(2, 1, bytes((0, 0, 0, 100, 100, 100)))
        candidate = RgbImage(2, 1, bytes((20, 10, 0, 100, 100, 100)))

        difference = compare_rgb_images(reference, candidate, large_channel_delta=16)

        self.assertEqual(difference.large_delta_pixel_fraction, 0.5)
        self.assertEqual(difference.mean_absolute_channel_delta, 5.0)

    def test_rejects_dimension_mismatch_and_invalid_delta(self) -> None:
        one_pixel = RgbImage(1, 1, bytes((0, 0, 0)))
        two_pixels = RgbImage(2, 1, bytes((0, 0, 0, 0, 0, 0)))

        with self.assertRaisesRegex(PngFormatError, "dimensions differ"):
            compare_rgb_images(one_pixel, two_pixels, large_channel_delta=16)
        with self.assertRaisesRegex(ValueError, "between 0 and 255"):
            compare_rgb_images(one_pixel, one_pixel, large_channel_delta=256)
        with self.assertRaisesRegex(PngFormatError, "pixel payload"):
            compare_rgb_images(RgbImage(1, 1, b""), one_pixel, large_channel_delta=16)

    def test_crops_an_exact_rgb_region(self) -> None:
        image = RgbImage(
            3,
            2,
            bytes(
                (
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                    7,
                    8,
                    9,
                    10,
                    11,
                    12,
                    13,
                    14,
                    15,
                    16,
                    17,
                    18,
                )
            ),
        )

        cropped = crop_rgb_image(image, left=1, top=0, right=3, bottom=2)

        self.assertEqual((cropped.width, cropped.height), (2, 2))
        self.assertEqual(cropped.pixels, bytes((4, 5, 6, 7, 8, 9, 13, 14, 15, 16, 17, 18)))
        with self.assertRaisesRegex(PngFormatError, "crop bounds"):
            crop_rgb_image(image, left=2, top=0, right=4, bottom=1)

    def test_demo_removal_requires_each_localized_visual_change(self) -> None:
        images = Path(__file__).resolve().parents[1] / "docs" / "images"
        before = images / "demo-before.png"
        after = images / "demo-after.png"
        before_image = read_rgb_png(before)
        after_image = read_rgb_png(after)

        _verify_removed_regions(before, after)
        _verify_committed_removal_regions(after_image, after_image)
        for spot_name, region in REMOVAL_REGIONS:
            stale_image = _replace_region(after_image, before_image, region)
            stale_path = self.root / f"stale-{spot_name}.png"
            _write_rgb_image(stale_path, stale_image)
            with (
                self.subTest(spot_name=spot_name, check="generated removal"),
                self.assertRaisesRegex(SystemExit, spot_name),
            ):
                _verify_removed_regions(before, stale_path)
            with (
                self.subTest(spot_name=spot_name, check="committed screenshot"),
                self.assertRaisesRegex(SystemExit, spot_name),
            ):
                _verify_committed_removal_regions(stale_image, after_image)

    def test_docs_image_set_ignores_untracked_platform_files(self) -> None:
        self._initialize_image_repository()
        platform_file = self.root / "docs" / "images" / ".DS_Store"
        platform_file.write_bytes(b"local Finder metadata")

        _validate_docs_image_set(self.root)

    def test_docs_image_set_rejects_a_tracked_nested_file(self) -> None:
        self._initialize_image_repository()
        nested = self.root / "docs" / "images" / "nested" / "extra.png"
        nested.parent.mkdir()
        nested.write_bytes(b"unexpected")
        subprocess.run(["git", "-C", self.root, "add", nested], check=True)

        with self.assertRaisesRegex(SystemExit, "nested/extra.png"):
            _validate_docs_image_set(self.root)

    def test_docs_image_set_rejects_a_non_ignored_untracked_file(self) -> None:
        self._initialize_image_repository()
        nested = self.root / "docs" / "images" / "nested" / "customer.png"
        nested.parent.mkdir()
        nested.write_bytes(b"must not enter an sdist")

        with self.assertRaisesRegex(SystemExit, "nested/customer.png"):
            _validate_docs_image_set(self.root)

    def _initialize_image_repository(self) -> None:
        subprocess.run(["git", "init", "--quiet", self.root], check=True)
        (self.root / ".gitignore").write_text(".DS_Store\n", encoding="utf-8")
        images = self.root / "docs" / "images"
        images.mkdir(parents=True)
        for name in RASTER_IMAGE_NAMES + VECTOR_IMAGE_NAMES + (GENERATION_METADATA_NAME,):
            (images / name).write_bytes(b"tracked fixture")
        subprocess.run(
            ["git", "-C", self.root, "add", ".gitignore", "docs/images"],
            check=True,
        )


def _write_png(path: Path, rows: tuple[bytes, ...], *, filter_types: tuple[int, ...]) -> None:
    width = len(rows[0]) // 3
    filtered = bytearray()
    previous = bytes(len(rows[0]))
    for row, filter_type in zip(rows, filter_types, strict=True):
        filtered.append(filter_type)
        for column, value in enumerate(row):
            left = row[column - 3] if column >= 3 else 0
            above = previous[column]
            upper_left = previous[column - 3] if column >= 3 else 0
            predictors = (
                0,
                left,
                above,
                (left + above) // 2,
                _paeth(left, above, upper_left),
            )
            filtered.append((value - predictors[filter_type]) & 0xFF)
        previous = row
    header = struct.pack(">IIBBBBB", width, len(rows), 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(filtered)))
        + _chunk(b"IEND", b"")
    )


def _write_rgb_image(path: Path, image: RgbImage) -> None:
    row_size = image.width * 3
    rows = tuple(
        image.pixels[offset : offset + row_size] for offset in range(0, len(image.pixels), row_size)
    )
    _write_png(path, rows, filter_types=(0,) * image.height)


def _replace_region(
    base: RgbImage,
    replacement: RgbImage,
    region: tuple[int, int, int, int],
) -> RgbImage:
    if (base.width, base.height) != (replacement.width, replacement.height):
        raise ValueError("replacement dimensions differ")
    left, top, right, bottom = region
    pixels = bytearray(base.pixels)
    row_size = base.width * 3
    for row in range(top, bottom):
        start = row * row_size + left * 3
        end = row * row_size + right * 3
        pixels[start:end] = replacement.pixels[start:end]
    return RgbImage(base.width, base.height, bytes(pixels))


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
    return (left, above, upper_left)[distances.index(min(distances))]
