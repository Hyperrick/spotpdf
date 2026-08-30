"""Fail-closed semantic fingerprints for XML metadata streams."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from xml.etree import ElementTree as ET

_MAX_CANONICAL_XML_BYTES = 8 * 1024 * 1024
_XPACKET_ID = "W5M0MpCehiHzreSzNTczkc9d"
_XPACKET_HEADER = re.compile(
    r"xpacket begin=(?:\"([^\"]*)\"|'([^']*)') "
    r"id=(?:\"([^\"]*)\"|'([^']*)')(.*)",
    re.DOTALL,
)
_XPACKET_TRAILER = re.compile(r"xpacket end=(?:\"([wr])\"|'([wr])')")
_RAW_XPACKET_PI = re.compile(r"<\?(xpacket(?=[\s?]).*?)\?>", re.DOTALL)


class _NoDoctypeTreeBuilder(ET.TreeBuilder):
    """Reject DTD-bearing metadata instead of expanding or normalizing it."""

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise ValueError("DOCTYPE is not accepted in PDF metadata")


def xml_metadata_fingerprint(data: bytes) -> tuple[Any, ...] | None:
    """Return canonical XML meaning, or ``None`` for strict raw comparison.

    XMP packet wrappers contain serialization instructions that pikepdf may
    legitimately rewrite. The XML root is canonicalized while wrapper values
    that affect packet identity or mutability remain part of the fingerprint.
    Malformed, DTD-bearing, or unusually large XML is never normalized.
    """

    if len(data) > _MAX_CANONICAL_XML_BYTES:
        return None
    try:
        root = ET.fromstring(
            data,
            parser=ET.XMLParser(
                target=_NoDoctypeTreeBuilder(insert_comments=True, insert_pis=True)
            ),
        )
        serialized_root = ET.tostring(root, encoding="unicode")
        canonical_root = ET.canonicalize(
            serialized_root,
            with_comments=True,
            strip_text=False,
        )
        envelope, namespaces = _xml_context_fingerprints(data)
    except (ET.ParseError, LookupError, UnicodeError, ValueError):
        return None
    return (
        "canonical-xml",
        envelope,
        namespaces,
        hashlib.sha256(canonical_root.encode("utf-8")).digest(),
    )


def _xml_context_fingerprints(
    data: bytes,
) -> tuple[tuple[tuple[Any, ...], ...], tuple[tuple[int, str, str], ...]]:
    """Capture the outer packet plus scoped namespace declarations."""

    raw_xpackets = _raw_xpacket_processing_instructions(data)
    raw_xpacket_index = 0
    parser = ET.XMLPullParser(events=("start", "end", "comment", "pi", "start-ns"))
    parser.feed(data)
    parser.close()
    depth = 0
    element_index = 0
    envelope: list[tuple[Any, ...]] = []
    namespaces: list[tuple[int, str, str]] = []
    pending_namespaces: list[tuple[str, str]] = []
    for event, node in parser.read_events():
        if event == "start-ns":
            prefix, uri = node
            pending_namespaces.append((prefix or "", uri))
            continue
        if event == "start":
            namespaces.extend(
                (element_index, prefix, uri) for prefix, uri in sorted(pending_namespaces)
            )
            pending_namespaces.clear()
            if depth == 0:
                envelope.append(("root",))
            depth += 1
            element_index += 1
            continue
        if event == "end":
            depth -= 1
            continue
        if event == "pi":
            text = node.text or ""
            if text.split(maxsplit=1)[:1] == ["xpacket"]:
                if raw_xpacket_index >= len(raw_xpackets):
                    raise ET.ParseError("could not bind raw xpacket processing instruction")
                text = raw_xpackets[raw_xpacket_index]
                raw_xpacket_index += 1
            if depth == 0:
                envelope.append(_processing_instruction_fingerprint(text))
            continue
        if depth != 0:
            continue
        if event == "comment":
            envelope.append(("comment", node.text or ""))
    if raw_xpacket_index != len(raw_xpackets):
        raise ET.ParseError("unparsed raw xpacket processing instruction")
    if pending_namespaces:
        raise ET.ParseError("XML metadata has unattached namespace declarations")
    if depth != 0 or not any(record[0] == "root" for record in envelope):
        raise ET.ParseError("XML metadata has no complete root element")
    return tuple(envelope), tuple(namespaces)


def _raw_xpacket_processing_instructions(data: bytes) -> tuple[str, ...]:
    text = _decode_xml(data)
    return tuple(match.group(1) for match in _RAW_XPACKET_PI.finditer(text))


def _decode_xml(data: bytes) -> str:
    signatures = (
        (b"\x00\x00\xfe\xff", "utf-32"),
        (b"\xff\xfe\x00\x00", "utf-32"),
        (b"\xfe\xff", "utf-16"),
        (b"\xff\xfe", "utf-16"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\x00\x00\x00<", "utf-32-be"),
        (b"<\x00\x00\x00", "utf-32-le"),
        (b"\x00<\x00?", "utf-16-be"),
        (b"<\x00?\x00", "utf-16-le"),
    )
    for signature, encoding in signatures:
        if data.startswith(signature):
            return data.decode(encoding)
    return data.decode("utf-8")


def _processing_instruction_fingerprint(text: str) -> tuple[Any, ...]:
    if not text.startswith("xpacket"):
        return ("pi", text)
    header = _XPACKET_HEADER.fullmatch(text)
    if header is not None:
        begin = header.group(1) if header.group(1) is not None else header.group(2)
        identifier = header.group(3) if header.group(3) is not None else header.group(4)
        suffix = header.group(5)
        if begin in {"", "\ufeff"} and identifier == _XPACKET_ID:
            return (
                "xpacket-header",
                "<unicode-byte-order-marker>",
                identifier,
                suffix,
            )
        return ("pi", text)
    trailer = _XPACKET_TRAILER.fullmatch(text)
    if trailer is not None:
        end = trailer.group(1) if trailer.group(1) is not None else trailer.group(2)
        return ("xpacket-trailer", end)
    return ("pi", text)


__all__ = ["xml_metadata_fingerprint"]
