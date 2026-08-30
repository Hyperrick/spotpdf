"""Decode generated RGB PNGs and measure renderer-tolerant visual drift."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 10_000_000


class PngFormatError(ValueError):
    """Raised when a generated documentation PNG is unsupported or malformed."""


@dataclass(frozen=True)
class RgbImage:
    width: int
    height: int
    pixels: bytes


@dataclass(frozen=True)
class ImageDifference:
    large_delta_pixel_fraction: float
    mean_absolute_channel_delta: float


def read_rgb_png(path: Path) -> RgbImage:
    """Read a bounded, non-interlaced, 8-bit RGB PNG using only the standard library."""

    try:
        size = path.stat().st_size
        if size > MAX_PNG_BYTES:
            raise PngFormatError(f"PNG exceeds {MAX_PNG_BYTES} bytes: {path}")
        data = path.read_bytes()
    except OSError as error:
        raise PngFormatError(f"could not read PNG {path}: {error}") from error
    if not data.startswith(PNG_SIGNATURE):
        raise PngFormatError(f"invalid PNG signature: {path}")
    if len(data) > MAX_PNG_BYTES:
        raise PngFormatError(f"PNG exceeds {MAX_PNG_BYTES} bytes: {path}")

    header: tuple[int, int, int, int, int, int, int] | None = None
    compressed = bytearray()
    offset = len(PNG_SIGNATURE)
    saw_end = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise PngFormatError(f"truncated PNG chunk: {path}")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise PngFormatError(f"truncated PNG chunk payload: {path}")
        payload = data[offset + 8 : offset + 8 + length]
        if len(chunk_type) != 4 or any(
            byte not in range(ord("A"), ord("Z") + 1) and byte not in range(ord("a"), ord("z") + 1)
            for byte in chunk_type
        ):
            raise PngFormatError(f"invalid PNG chunk type: {path}")
        if chunk_type[2] & 0x20:
            raise PngFormatError(f"invalid PNG reserved chunk bit: {path}")
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(payload, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise PngFormatError(f"invalid PNG chunk checksum: {path}")

        if chunk_type == b"IHDR":
            if header is not None or offset != len(PNG_SIGNATURE) or length != 13:
                raise PngFormatError(f"invalid PNG header: {path}")
            header = struct.unpack(">IIBBBBB", payload)
        elif chunk_type == b"IDAT":
            compressed.extend(payload)
            if len(compressed) > MAX_PNG_BYTES:
                raise PngFormatError(f"compressed PNG pixels exceed the limit: {path}")
        elif chunk_type == b"IEND":
            if length != 0:
                raise PngFormatError(f"invalid PNG end chunk: {path}")
            saw_end = True
            offset = chunk_end
            break
        elif chunk_type[0] & 0x20 == 0 and chunk_type != b"PLTE":
            raise PngFormatError(f"unsupported critical PNG chunk {chunk_type!r}: {path}")
        offset = chunk_end

    if header is None or not saw_end or offset != len(data):
        raise PngFormatError(f"incomplete PNG structure: {path}")
    width, height, depth, color_type, compression, filtering, interlace = header
    if (
        width <= 0
        or height <= 0
        or width * height > MAX_PIXELS
        or (depth, color_type, compression, filtering, interlace) != (8, 2, 0, 0, 0)
    ):
        raise PngFormatError(f"expected bounded non-interlaced 8-bit RGB PNG: {path}")

    stride = width * 3
    expected_size = (stride + 1) * height
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(bytes(compressed), expected_size + 1)
    except zlib.error as error:
        raise PngFormatError(f"invalid compressed PNG pixels: {path}") from error
    if (
        len(raw) != expected_size
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
    ):
        raise PngFormatError(f"unexpected decompressed PNG size: {path}")
    return RgbImage(width=width, height=height, pixels=_unfilter(raw, stride, height))


def compare_rgb_images(
    reference: RgbImage,
    candidate: RgbImage,
    *,
    large_channel_delta: int,
) -> ImageDifference:
    """Measure pixel drift while retaining per-channel antialiasing sensitivity."""

    if (reference.width, reference.height) != (candidate.width, candidate.height):
        raise PngFormatError(
            "PNG dimensions differ: "
            f"{reference.width}x{reference.height} != {candidate.width}x{candidate.height}"
        )
    if not 0 <= large_channel_delta <= 255:
        raise ValueError("large_channel_delta must be between 0 and 255")
    expected_pixel_bytes = reference.width * reference.height * 3
    if (
        reference.width <= 0
        or reference.height <= 0
        or len(reference.pixels) != expected_pixel_bytes
        or len(candidate.pixels) != expected_pixel_bytes
    ):
        raise PngFormatError("RGB pixel payload does not match the image dimensions")

    total_delta = 0
    large_delta_pixels = 0
    reference_pixels = reference.pixels
    candidate_pixels = candidate.pixels
    for offset in range(0, len(reference.pixels), 3):
        red = abs(reference_pixels[offset] - candidate_pixels[offset])
        green = abs(reference_pixels[offset + 1] - candidate_pixels[offset + 1])
        blue = abs(reference_pixels[offset + 2] - candidate_pixels[offset + 2])
        total_delta += red + green + blue
        if max(red, green, blue) > large_channel_delta:
            large_delta_pixels += 1
    pixel_count = reference.width * reference.height
    return ImageDifference(
        large_delta_pixel_fraction=large_delta_pixels / pixel_count,
        mean_absolute_channel_delta=total_delta / (pixel_count * 3),
    )


def crop_rgb_image(
    image: RgbImage,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> RgbImage:
    """Return one validated rectangular region from an RGB image."""

    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        raise PngFormatError("crop bounds are outside the RGB image")
    expected_pixel_bytes = image.width * image.height * 3
    if len(image.pixels) != expected_pixel_bytes:
        raise PngFormatError("RGB pixel payload does not match the image dimensions")
    source_stride = image.width * 3
    cropped_stride = (right - left) * 3
    pixels = bytearray(cropped_stride * (bottom - top))
    for target_row, source_row in enumerate(range(top, bottom)):
        source_offset = source_row * source_stride + left * 3
        target_offset = target_row * cropped_stride
        pixels[target_offset : target_offset + cropped_stride] = image.pixels[
            source_offset : source_offset + cropped_stride
        ]
    return RgbImage(
        width=right - left,
        height=bottom - top,
        pixels=bytes(pixels),
    )


def _unfilter(raw: bytes, stride: int, height: int) -> bytes:
    pixels = bytearray(stride * height)
    source_offset = 0
    for row_index in range(height):
        filter_type = raw[source_offset]
        source_offset += 1
        if filter_type > 4:
            raise PngFormatError(f"unsupported PNG row filter: {filter_type}")
        row_offset = row_index * stride
        previous_offset = row_offset - stride
        for column in range(stride):
            value = raw[source_offset + column]
            left = pixels[row_offset + column - 3] if column >= 3 else 0
            above = pixels[previous_offset + column] if row_index else 0
            upper_left = pixels[previous_offset + column - 3] if row_index and column >= 3 else 0
            if filter_type == 1:
                value += left
            elif filter_type == 2:
                value += above
            elif filter_type == 3:
                value += (left + above) // 2
            elif filter_type == 4:
                value += _paeth(left, above, upper_left)
            pixels[row_offset + column] = value & 0xFF
        source_offset += stride
    return bytes(pixels)


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left
