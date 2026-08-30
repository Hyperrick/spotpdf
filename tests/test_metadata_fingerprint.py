from __future__ import annotations

import unittest

import pikepdf

from spotpdf.metadata_fingerprint import xml_metadata_fingerprint
from spotpdf.rename_slots import semantic_object_fingerprint


class XmlMetadataFingerprintTests(unittest.TestCase):
    def test_xpacket_storage_rewrites_are_semantically_equal(self) -> None:
        before = self._xmp(
            begin="",
            quote="'",
            newline="\r",
            padding="",
        )
        after = self._xmp(
            begin="\ufeff",
            quote='"',
            newline="\n",
            padding="\n",
        )

        before_fingerprint = xml_metadata_fingerprint(before)
        self.assertIsNotNone(before_fingerprint)
        self.assertEqual(before_fingerprint, xml_metadata_fingerprint(after))

    def test_xpacket_identity_and_mutability_remain_significant(self) -> None:
        original = xml_metadata_fingerprint(self._xmp())

        self.assertNotEqual(original, xml_metadata_fingerprint(self._xmp(identifier="Other")))
        self.assertNotEqual(original, xml_metadata_fingerprint(self._xmp(end="r")))
        self.assertNotEqual(original, xml_metadata_fingerprint(self._xmp(begin="arbitrary")))
        self.assertNotEqual(original, xml_metadata_fingerprint(self._xmp(bytes_hint="2048")))
        self.assertNotEqual(original, xml_metadata_fingerprint(self._xmp(encoding="UTF-16")))

    def test_malformed_xpacket_spacing_and_order_are_not_normalized(self) -> None:
        valid = self._xmp()
        begin = 'begin="\ufeff"'
        identifier = 'id="W5M0MpCehiHzreSzNTczkc9d"'
        mutations = (
            valid.replace(b"xpacket begin", b"xpacket  begin", 1),
            valid.replace(b"xpacket end=", b"xpacket end =", 1),
            valid.replace(
                f"{begin} {identifier}".encode(),
                f"{identifier} {begin}".encode(),
                1,
            ),
        )

        fingerprint = xml_metadata_fingerprint(valid)
        for mutation in mutations:
            with self.subTest(packet=mutation.splitlines()[0]):
                self.assertNotEqual(fingerprint, xml_metadata_fingerprint(mutation))

    def test_rdf_value_changes_remain_significant(self) -> None:
        original = xml_metadata_fingerprint(self._xmp(title="Original"))

        self.assertNotEqual(original, xml_metadata_fingerprint(self._xmp(title="Changed")))

    def test_namespace_declarations_for_qname_values_remain_significant(self) -> None:
        original = xml_metadata_fingerprint(self._xmp(qname_namespace="urn:one"))

        self.assertNotEqual(
            original,
            xml_metadata_fingerprint(self._xmp(qname_namespace="urn:two")),
        )

    def test_comments_remain_significant(self) -> None:
        original = xml_metadata_fingerprint(self._xmp(comment="first"))

        self.assertNotEqual(original, xml_metadata_fingerprint(self._xmp(comment="second")))

    def test_malformed_or_dtd_bearing_xml_is_not_normalized(self) -> None:
        malformed = b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>"
        with_doctype = b"<!DOCTYPE x [<!ELEMENT x ANY>]><x/>"

        self.assertIsNone(xml_metadata_fingerprint(malformed))
        self.assertIsNone(xml_metadata_fingerprint(with_doctype))

    def test_malformed_xml_uses_strict_raw_stream_fingerprint(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            first = self._metadata_stream(pdf, b"<broken>")
            second = self._metadata_stream(pdf, b"<different>")

            self.assertNotEqual(
                semantic_object_fingerprint(first),
                semantic_object_fingerprint(second),
            )

    def test_only_declared_xml_metadata_uses_semantic_normalization(self) -> None:
        before = self._xmp(quote="'")
        after = self._xmp(quote='"')
        with pikepdf.Pdf.new() as pdf:
            metadata_before = self._metadata_stream(pdf, before)
            metadata_after = self._metadata_stream(pdf, after)
            ordinary_xml_before = pdf.make_stream(before)
            ordinary_xml_before.Subtype = pikepdf.Name.XML
            ordinary_xml_after = pdf.make_stream(after)
            ordinary_xml_after.Subtype = pikepdf.Name.XML

            self.assertEqual(
                semantic_object_fingerprint(metadata_before),
                semantic_object_fingerprint(metadata_after),
            )
            self.assertNotEqual(
                semantic_object_fingerprint(ordinary_xml_before),
                semantic_object_fingerprint(ordinary_xml_after),
            )

    @staticmethod
    def _xmp(
        *,
        begin: str = "\ufeff",
        identifier: str = "W5M0MpCehiHzreSzNTczkc9d",
        end: str = "w",
        quote: str = '"',
        newline: str = "\n",
        padding: str = "",
        title: str = "Original",
        bytes_hint: str | None = None,
        encoding: str | None = None,
        qname_namespace: str = "urn:qname",
        comment: str = "preserve",
    ) -> bytes:
        packet_attributes = [
            f"begin={quote}{begin}{quote}",
            f"id={quote}{identifier}{quote}",
        ]
        if bytes_hint is not None:
            packet_attributes.append(f"bytes={quote}{bytes_hint}{quote}")
        if encoding is not None:
            packet_attributes.append(f"encoding={quote}{encoding}{quote}")
        lines = (
            f"<?xpacket {' '.join(packet_attributes)}?>",
            f'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:q="{qname_namespace}">',
            '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
            f"    <!--{comment}-->",
            f'    <rdf:Description rdf:about="" title="{title}" qualified="q:name"/>',
            "  </rdf:RDF>",
            "</x:xmpmeta>",
            f"{padding}<?xpacket end={quote}{end}{quote}?>",
        )
        return newline.join(lines).encode("utf-8")

    @staticmethod
    def _metadata_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
        stream = pdf.make_stream(data)
        stream.Type = pikepdf.Name.Metadata
        stream.Subtype = pikepdf.Name.XML
        return stream


if __name__ == "__main__":
    unittest.main()
