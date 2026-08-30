"""Physical PDF name slots and invariants used by atomic rename plans."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import pikepdf

from .inventory_values import name_or_string, path_name
from .metadata_fingerprint import xml_metadata_fingerprint
from .model import InvalidPdfError, NameDependencyKind, SpotPdfError
from .objects import ObjectKey, object_key
from .trailer_semantics import semantic_trailer_items


class SlotMode(StrEnum):
    ARRAY_NAME = "array-name"
    DICTIONARY_KEY = "dictionary-key"
    NAME_VALUE = "name-value"
    STRING_VALUE = "string-value"


def pdf_name(name: str) -> pikepdf.Name:
    """Create a PDF Name from its decoded, leading-slash-free value."""

    try:
        return pikepdf.Name(f"/{name}")
    except (TypeError, ValueError) as error:
        raise InvalidPdfError(f"invalid PDF colorant name: {name!r}") from error


def container_key(value: Any, locations: tuple[str, ...]) -> tuple[Any, ...]:
    """Identify an indirect container or one direct container at a stable path."""

    key = object_key(value)
    if key[0] == "indirect":
        return key
    return ("direct", min(locations))


@dataclass
class MutableNameSlot:
    """One physical name location, possibly reached through several contexts."""

    container: Any
    member: int | pikepdf.Name
    mode: SlotMode
    source: str
    destination: str
    definition: bool = False
    dependency_kinds: set[NameDependencyKind] = field(default_factory=set)
    locations: set[str] = field(default_factory=set)
    moved_value: Any | None = None

    @property
    def label(self) -> str:
        return min(self.locations) if self.locations else "unknown PDF location"

    def validate(self, *, applied: bool) -> None:
        """Require the slot to contain exactly the expected side of the rename."""

        expected = self.destination if applied else self.source
        unexpected = self.source if applied else self.destination
        if self.mode is SlotMode.DICTIONARY_KEY:
            expected_key = pdf_name(expected)
            unexpected_key = pdf_name(unexpected)
            if expected_key not in self.container or unexpected_key in self.container:
                raise SpotPdfError(f"rename slot changed unexpectedly at {self.label}")
            if applied and self.moved_value is not None:
                try:
                    unchanged = self.container[expected_key] == self.moved_value
                except (KeyError, TypeError, ValueError):
                    unchanged = False
                if not unchanged:
                    raise SpotPdfError(f"renamed dictionary value changed at {self.label}")
            return

        current = self.container[self.member]
        actual = name_or_string(current)
        if actual != expected:
            raise SpotPdfError(
                f"rename slot at {self.label} contains {actual!r}, expected {expected!r}"
            )
        expected_type = {
            SlotMode.ARRAY_NAME: pikepdf.Name,
            SlotMode.NAME_VALUE: pikepdf.Name,
            SlotMode.STRING_VALUE: pikepdf.String,
        }[self.mode]
        if not isinstance(current, expected_type):
            raise SpotPdfError(f"rename slot has the wrong PDF type at {self.label}")

    def apply(self) -> None:
        """Apply one already validated physical name replacement."""

        if self.mode is SlotMode.DICTIONARY_KEY:
            source_key = pdf_name(self.source)
            destination_key = pdf_name(self.destination)
            self.moved_value = self.container[source_key]
            self.container[destination_key] = self.moved_value
            del self.container[source_key]
        elif self.mode is SlotMode.STRING_VALUE:
            self.container[self.member] = pikepdf.String(self.destination)
        else:
            self.container[self.member] = pdf_name(self.destination)


@dataclass(frozen=True)
class SeparationInvariant:
    """Alternate space and tint transform that a plate alias must not alter."""

    separation: pikepdf.Array
    location: str
    alternate_fingerprint: tuple[Any, ...]
    tint_fingerprint: tuple[Any, ...]

    @classmethod
    def capture(cls, separation: pikepdf.Array, location: str) -> SeparationInvariant:
        return cls(
            separation=separation,
            location=location,
            alternate_fingerprint=object_fingerprint(separation[2]),
            tint_fingerprint=object_fingerprint(separation[3]),
        )

    def verify(self) -> None:
        if object_fingerprint(self.separation[2]) != self.alternate_fingerprint:
            raise SpotPdfError(f"alternate color space changed during rename at {self.location}")
        if object_fingerprint(self.separation[3]) != self.tint_fingerprint:
            raise SpotPdfError(f"tint transform changed during rename at {self.location}")


@dataclass
class RenamePlan:
    """A fully validated set of physical name mutations for one plate alias."""

    source: str
    destination: str
    _slots: tuple[MutableNameSlot, ...]
    _invariants: tuple[SeparationInvariant, ...]
    _definition_count: int
    _reference_count: int
    _applied: bool = False

    @property
    def definitions_renamed(self) -> int:
        return self._definition_count

    @property
    def references_renamed(self) -> int:
        return self._reference_count

    def apply(self) -> None:
        """Validate every slot once more, then apply the complete plan."""

        if self._applied:
            raise SpotPdfError("rename plan has already been applied")
        self.verify_invariants()
        for slot in self._slots:
            slot.validate(applied=False)
        for slot in self._slots:
            slot.apply()
        self._applied = True
        self.verify_invariants()

    def verify_invariants(self) -> None:
        """Verify names plus unchanged alternate spaces and tint transforms."""

        for invariant in self._invariants:
            invariant.verify()
        for slot in self._slots:
            slot.validate(applied=self._applied)

    def preflight_fingerprint(self) -> tuple[tuple[Any, ...], ...]:
        """Capture name-normalized semantics around every planned mutation slot."""

        if self._applied:
            raise SpotPdfError("rename semantics must be captured before applying the plan")
        with self._normalize_slots():
            records = []
            for slot in self._slots:
                member: int | str
                if (
                    isinstance(slot.member, pikepdf.Name)
                    and name_or_string(slot.member) == self.source
                ):
                    member = "<renamed-key>"
                else:
                    member = str(slot.member)
                records.append(
                    (
                        slot.mode.value,
                        member,
                        slot.definition,
                        tuple(sorted(kind.value for kind in slot.dependency_kinds)),
                        tuple(
                            sorted(
                                normalize_rename_location(item, self.source)
                                for item in slot.locations
                            )
                        ),
                        semantic_object_fingerprint(slot.container),
                    )
                )
        return tuple(sorted(records, key=repr))

    def normalized_document_fingerprint(self, pdf: pikepdf.Pdf) -> tuple[Any, ...]:
        """Fingerprint the document while masking only this plan's exact slots."""

        with self._normalize_slots():
            return semantic_pdf_fingerprint(pdf)

    @contextmanager
    def _normalize_slots(self) -> Iterator[None]:
        marker = self._normalization_marker()
        marker_name = pdf_name(marker)
        replacements: list[tuple[MutableNameSlot, Any, pikepdf.Name | None]] = []
        try:
            for slot in self._slots:
                slot.validate(applied=self._applied)
                if slot.mode is SlotMode.DICTIONARY_KEY:
                    current_name = self.destination if self._applied else self.source
                    current_key = pdf_name(current_name)
                    original = slot.container[current_key]
                    replacements.append((slot, original, current_key))
                    slot.container[marker_name] = original
                    del slot.container[current_key]
                    continue
                original = slot.container[slot.member]
                replacement = (
                    pikepdf.String(marker) if slot.mode is SlotMode.STRING_VALUE else marker_name
                )
                replacements.append((slot, original, None))
                slot.container[slot.member] = replacement
            yield
        finally:
            for slot, original, original_key in reversed(replacements):
                if original_key is None:
                    slot.container[slot.member] = original
                    continue
                if marker_name in slot.container:
                    slot.container[original_key] = slot.container[marker_name]
                    del slot.container[marker_name]
                elif original_key not in slot.container:
                    slot.container[original_key] = original

    def _normalization_marker(self) -> str:
        """Choose the same collision-free marker before and after the rename."""

        counter = 0
        while True:
            marker = f"__spotpdf_atomic_rename_slot_{counter}__"
            marker_name = pdf_name(marker)
            if marker not in {self.source, self.destination} and all(
                slot.mode is not SlotMode.DICTIONARY_KEY or marker_name not in slot.container
                for slot in self._slots
            ):
                return marker
            counter += 1


def object_fingerprint(value: Any) -> tuple[Any, ...]:
    """Fingerprint values without unstable Python identities for direct objects."""

    key: ObjectKey = object_key(value)
    identity = key if key[0] == "indirect" else ("direct",)
    try:
        serialized = bytes(value.unparse(resolved=True))
    except (AttributeError, TypeError, ValueError, pikepdf.PdfError):
        serialized = repr(value).encode("utf-8", errors="backslashreplace")
    if not isinstance(value, pikepdf.Stream):
        return identity, serialized
    try:
        stream_data = value.read_raw_bytes()
    except (pikepdf.DataDecodingError, pikepdf.PdfError):
        stream_data = value.read_bytes(pikepdf.StreamDecodeLevel.specialized)
    return identity, serialized, stream_data


def semantic_object_fingerprint(
    value: Any,
    *,
    masked_streams: Mapping[ObjectKey, bytes] | None = None,
) -> tuple[Any, ...]:
    """Fingerprint object meaning while ignoring storage and object numbering."""

    return _semantic_value(value, {}, masked_streams or {})


def semantic_pdf_fingerprint(
    pdf: pikepdf.Pdf,
    *,
    masked_streams: Mapping[ObjectKey, bytes] | None = None,
) -> tuple[Any, ...]:
    """Fingerprint semantic trailer entries while ignoring rewrite bookkeeping."""

    return tuple(
        (
            str(key),
            semantic_object_fingerprint(value, masked_streams=masked_streams),
        )
        for key, value in semantic_trailer_items(pdf)
    )


def _semantic_value(
    value: Any,
    references: dict[ObjectKey, int],
    masked_streams: Mapping[ObjectKey, bytes],
) -> tuple[Any, ...]:
    if isinstance(value, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
        key = object_key(value)
        if key[0] == "indirect":
            if key in references:
                return ("reference", references[key])
            references[key] = len(references)
    if isinstance(value, pikepdf.Stream):
        try:
            stream_data = value.read_bytes(pikepdf.StreamDecodeLevel.specialized)
            stream_mode = "decoded"
        except (pikepdf.DataDecodingError, pikepdf.PdfError):
            stream_data = value.read_raw_bytes()
            stream_mode = "raw"
        content_fingerprint: bytes | tuple[Any, ...] = hashlib.sha256(stream_data).digest()
        if stream_mode == "decoded" and _is_xml_metadata(value):
            xml_fingerprint = xml_metadata_fingerprint(stream_data)
            if xml_fingerprint is not None:
                stream_mode = "metadata-xml"
                content_fingerprint = xml_fingerprint
        if key in masked_streams:
            content_fingerprint = masked_streams[key]
        entries = _semantic_dictionary_entries(
            value,
            references,
            masked_streams,
            stream_mode=stream_mode,
        )
        return ("stream", stream_mode, entries, content_fingerprint)
    if isinstance(value, pikepdf.Dictionary):
        return (
            "dictionary",
            _semantic_dictionary_entries(value, references, masked_streams),
        )
    if isinstance(value, pikepdf.Array):
        return (
            "array",
            tuple(_semantic_value(item, references, masked_streams) for item in value),
        )
    try:
        serialized = bytes(value.unparse(resolved=True))
    except (AttributeError, TypeError, ValueError, pikepdf.PdfError):
        serialized = repr(value).encode("utf-8", errors="backslashreplace")
    return ("scalar", serialized)


def _semantic_dictionary_entries(
    value: pikepdf.Dictionary | pikepdf.Stream,
    references: dict[ObjectKey, int],
    masked_streams: Mapping[ObjectKey, bytes],
    *,
    stream_mode: str | None = None,
) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    storage_keys = {
        pikepdf.Name.Length,
        pikepdf.Name.DL,
    }
    if stream_mode in {"decoded", "metadata-xml"}:
        storage_keys.update((pikepdf.Name.Filter, pikepdf.Name.DecodeParms))
    entries = [
        (str(key), item)
        for key, item in value.items()
        if stream_mode is None or key not in storage_keys
    ]
    entries.sort(key=lambda item: item[0])
    return tuple((key, _semantic_value(item, references, masked_streams)) for key, item in entries)


def _is_xml_metadata(value: pikepdf.Stream) -> bool:
    return (
        value.get(pikepdf.Name.Type, None) == pikepdf.Name.Metadata
        and value.get(pikepdf.Name.Subtype, None) == pikepdf.Name.XML
    )


def normalize_rename_location(location: str, name: str) -> str:
    """Normalize path segments whose dictionary key is renamed by a plan."""

    encoded = path_name(pdf_name(name))
    for parent in ("/Colorants", "/Solidities", "/DotGain"):
        location = location.replace(
            f" {parent} {encoded}",
            f" {parent} <renamed-key>",
        )
    return location


__all__ = [
    "MutableNameSlot",
    "RenamePlan",
    "SeparationInvariant",
    "SlotMode",
    "container_key",
    "object_fingerprint",
    "normalize_rename_location",
    "pdf_name",
    "semantic_pdf_fingerprint",
    "semantic_object_fingerprint",
]
