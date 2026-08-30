"""Small generated PDF fixtures for processing-budget tests."""

from __future__ import annotations

from pathlib import Path

import pikepdf


def make_spot_pdf(root: Path, *, pages: int = 1) -> Path:
    path = root / f"spot-{pages}.pdf"
    with pikepdf.Pdf.new() as pdf:
        for _ in range(pages):
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Target=separation()))
            page.Contents = pdf.make_stream(b"/Target cs 1 scn 0 0 10 10 re f\nq\nQ\n")
        pdf.save(path)
    return path


def make_run_length_spot_pdf(root: Path) -> Path:
    path = root / "run-length-spot.pdf"
    content = b"/Target cs 1 scn 0 0 10 10 re f\n"
    encoded = bytes((len(content) - 1,)) + content + b"\x80"
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(100, 100))
        page.Resources = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Target=separation()))
        stream = pdf.make_stream(encoded)
        stream.Filter = pikepdf.Name.RunLengthDecode
        page.Contents = stream
        pdf.save(path, compress_streams=False)
    return path


def make_plain_pdf(root: Path, contents: tuple[bytes, ...]) -> Path:
    path = root / "plain.pdf"
    with pikepdf.Pdf.new() as pdf:
        for content in contents:
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary()
            page.Contents = pdf.make_stream(content)
        pdf.save(path)
    return path


def make_shared_form_pdf(root: Path) -> Path:
    path = root / "shared-form.pdf"
    with pikepdf.Pdf.new() as pdf:
        form = pdf.make_stream(b"q\nQ\n")
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 10, 10])
        form.Resources = pikepdf.Dictionary()
        for _ in range(2):
            page = pdf.add_blank_page(page_size=(100, 100))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Shared=form))
            page.Contents = pdf.make_stream(b"/Shared Do\n")
        pdf.save(path)
    return path


def make_alias_pdf(root: Path, *, aliases: int) -> Path:
    path = root / f"aliases-{aliases}.pdf"
    with pikepdf.Pdf.new() as pdf:
        shared = pdf.make_indirect(pikepdf.Dictionary(Value=1))
        pdf.Root.Aliases = pikepdf.Array([shared] * aliases)
        pdf.save(path)
    return path


def run_length_encode_repeated(value: int, length: int) -> bytes:
    encoded = bytearray()
    remaining = length
    while remaining:
        run = min(remaining, 128)
        encoded.extend((257 - run, value))
        remaining -= run
    encoded.append(128)
    return bytes(encoded)


def separation() -> pikepdf.Array:
    return pikepdf.Array(
        [
            pikepdf.Name.Separation,
            pikepdf.Name.DemoSpot,
            pikepdf.Name.DeviceCMYK,
            pikepdf.Dictionary(
                FunctionType=2,
                Domain=pikepdf.Array([0, 1]),
                C0=pikepdf.Array([0, 0, 0, 0]),
                C1=pikepdf.Array([1, 0, 1, 0]),
                N=1,
            ),
        ]
    )
