"""Validated mutation plans for Separation alternate CMYK previews."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pikepdf

from .alternate_validation import reject_inline_target_definitions, validate_existing_preview
from .colors import PROCESS_COLORANTS, SPECIAL_COLORANTS
from .inventory_graph import walk_reachable
from .inventory_values import name_or_string, name_value
from .model import (
    ColorantRole,
    InspectionReport,
    InvalidPdfError,
    SpotKind,
    SpotPdfError,
    UnsupportedSpotUseError,
)
from .rename_hazards import devicen_target_mentions, name_field_mentions
from .rename_slots import semantic_object_fingerprint, semantic_pdf_fingerprint

NormalizedCmyk = tuple[float, float, float, float]


@dataclass
class AlternateSlot:
    """One complete Separation definition whose preview may be replaced."""

    separation: pikepdf.Array
    spot: str
    original_alternate: tuple[Any, ...]
    original_tint: tuple[Any, ...]
    requested_tint: tuple[Any, ...]
    locations: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return min(self.locations)

    def verify_original(self) -> None:
        self._verify_identity()
        if semantic_object_fingerprint(self.separation[2]) != self.original_alternate:
            raise SpotPdfError(f"alternate color space changed before apply at {self.label}")
        if semantic_object_fingerprint(self.separation[3]) != self.original_tint:
            raise SpotPdfError(f"tint transform changed before apply at {self.label}")

    def verify_requested(self) -> None:
        self._verify_identity()
        if self.separation[2] != pikepdf.Name.DeviceCMYK:
            raise SpotPdfError(f"requested /DeviceCMYK alternate is absent at {self.label}")
        if semantic_object_fingerprint(self.separation[3]) != self.requested_tint:
            raise SpotPdfError(f"requested linear CMYK tint transform is absent at {self.label}")

    def _verify_identity(self) -> None:
        if (
            len(self.separation) != 4
            or name_value(self.separation[0]) != "Separation"
            or name_value(self.separation[1]) != self.spot
        ):
            raise SpotPdfError(f"Separation identity changed unexpectedly at {self.label}")


@dataclass
class AlternatePlan:
    """A complete set of preview-only mutations for one spot plate."""

    pdf: pikepdf.Pdf
    spot: str
    cmyk: NormalizedCmyk
    slots: tuple[AlternateSlot, ...]
    _applied: bool = False

    @property
    def definitions_changed(self) -> int:
        return len(self.slots)

    def apply(self) -> None:
        """Replace only alternate-space and tint-transform array members."""

        if self._applied:
            raise SpotPdfError("alternate preview plan has already been applied")
        for slot in self.slots:
            slot.verify_original()
        tint_transform = self.pdf.make_indirect(_linear_cmyk_function(self.cmyk))
        for slot in self.slots:
            slot.separation[2] = pikepdf.Name.DeviceCMYK
            slot.separation[3] = tint_transform
        self._applied = True
        self.verify_requested()

    def verify_requested(self) -> None:
        """Require every planned definition to contain the exact requested preview."""

        for slot in self.slots:
            slot.verify_requested()

    def normalized_document_fingerprint(self) -> tuple[Any, ...]:
        """Fingerprint the document while masking only the two preview slots."""

        with self._masked_preview_slots():
            return semantic_pdf_fingerprint(self.pdf)

    @contextmanager
    def _masked_preview_slots(self) -> Iterator[None]:
        originals: list[tuple[AlternateSlot, Any, Any]] = []
        try:
            for slot in self.slots:
                slot.verify_requested() if self._applied else slot.verify_original()
                originals.append((slot, slot.separation[2], slot.separation[3]))
                slot.separation[2] = pikepdf.Name("/__spotpdf_alternate_space_slot__")
                slot.separation[3] = pikepdf.Name("/__spotpdf_tint_transform_slot__")
            yield
        finally:
            for slot, alternate, tint in reversed(originals):
                slot.separation[2] = alternate
                slot.separation[3] = tint


class _PlanBuilder:
    """Find every matching Separation and reject DeviceN or malformed uses."""

    def __init__(
        self,
        pdf: pikepdf.Pdf,
        report: InspectionReport,
        spot: str,
        cmyk: NormalizedCmyk,
    ) -> None:
        self.pdf = pdf
        self.report = report
        self.spot = spot
        self.cmyk = cmyk
        self.requested_tint = semantic_object_fingerprint(_linear_cmyk_function(cmyk))
        self.slots: dict[str, AlternateSlot] = {}

    def build(self) -> AlternatePlan:
        self._validate_request()
        for visit in walk_reachable(self.pdf):
            value = visit.value
            if not isinstance(value, pikepdf.Array) or not value:
                continue
            family = name_or_string(value[0])
            if family == "Separation":
                self._inspect_separation(value, visit.locations)
            elif family == "DeviceN" and devicen_target_mentions(value, frozenset({self.spot})):
                raise UnsupportedSpotUseError(
                    f"{min(visit.locations)}: DeviceN use of {self.spot!r} is not supported"
                )
        reject_inline_target_definitions(self.pdf, self.spot)
        self._validate_coverage()
        return AlternatePlan(
            pdf=self.pdf,
            spot=self.spot,
            cmyk=self.cmyk,
            slots=tuple(sorted(self.slots.values(), key=lambda slot: slot.label)),
        )

    def _validate_request(self) -> None:
        if self.spot in SPECIAL_COLORANTS:
            raise InvalidPdfError("reserved /All and /None separation previews cannot be changed")
        if self.spot in PROCESS_COLORANTS:
            raise InvalidPdfError("canonical process-color previews cannot be changed")
        summary = self.report.colorants.get(self.spot)
        if summary is None:
            raise InvalidPdfError(f"spot color is absent: {self.spot!r}")
        if summary.roles != {ColorantRole.SPOT}:
            roles = ", ".join(sorted(role.value for role in summary.roles)) or "unknown"
            raise InvalidPdfError(
                f"colorant {self.spot!r} is not an unambiguous spot (roles: {roles})"
            )
        if SpotKind.DEVICEN in summary.kinds:
            raise UnsupportedSpotUseError(
                f"DeviceN use of {self.spot!r} is not supported by set-alternate"
            )
        if not self._expected_definition_ids():
            raise InvalidPdfError(
                f"spot color {self.spot!r} has no reachable Separation definition"
            )

    def _inspect_separation(
        self,
        value: pikepdf.Array,
        locations: tuple[str, ...],
    ) -> None:
        raw_name = value[1] if len(value) >= 2 else None
        if name_or_string(raw_name) != self.spot:
            if name_field_mentions(raw_name, frozenset({self.spot})):
                raise UnsupportedSpotUseError(
                    f"{min(locations)}: malformed Separation name field mentions {self.spot!r}"
                )
            return
        location = min(locations)
        if (
            not isinstance(value[0], pikepdf.Name)
            or not isinstance(raw_name, pikepdf.Name)
            or len(value) != 4
            or not isinstance(value[2], (pikepdf.Name, pikepdf.Array))
            or not isinstance(value[3], (pikepdf.Dictionary, pikepdf.Stream))
        ):
            raise UnsupportedSpotUseError(
                f"{location}: malformed Separation array cannot be changed safely"
            )
        validate_existing_preview(value[2], value[3], location)
        definition_id = self._definition_id(locations)
        proposed = AlternateSlot(
            separation=value,
            spot=self.spot,
            original_alternate=semantic_object_fingerprint(value[2]),
            original_tint=semantic_object_fingerprint(value[3]),
            requested_tint=self.requested_tint,
        )
        slot = self.slots.setdefault(definition_id, proposed)
        if slot.separation is not value and (
            semantic_object_fingerprint(slot.separation) != semantic_object_fingerprint(value)
        ):
            raise UnsupportedSpotUseError(
                f"{location}: inventory identity maps to conflicting Separation arrays"
            )
        slot.locations.update(locations)

    def _definition_id(self, locations: tuple[str, ...]) -> str:
        location_set = set(locations)
        matches = [
            definition.object_id
            for definition in self.report.definitions.values()
            if definition.kind is SpotKind.SEPARATION
            and any(component.name == self.spot for component in definition.components)
            and location_set.intersection(definition.locations)
        ]
        if len(set(matches)) != 1:
            raise UnsupportedSpotUseError(
                f"{min(locations)}: Separation has no unique inventory identity"
            )
        return matches[0]

    def _expected_definition_ids(self) -> set[str]:
        return {
            definition.object_id
            for definition in self.report.definitions.values()
            if definition.kind is SpotKind.SEPARATION
            and any(component.name == self.spot for component in definition.components)
        }

    def _validate_coverage(self) -> None:
        expected = self._expected_definition_ids()
        actual = set(self.slots)
        if actual != expected:
            raise UnsupportedSpotUseError(
                "inventory Separation definitions could not be mapped one-to-one "
                f"(expected {len(expected)}, planned {len(actual)})"
            )


def _linear_cmyk_function(cmyk: NormalizedCmyk) -> pikepdf.Dictionary:
    full_tone = tuple(_canonical_pdf_component(value) for value in cmyk)
    return pikepdf.Dictionary(
        FunctionType=2,
        Domain=pikepdf.Array([0, 1]),
        Range=pikepdf.Array([0, 1, 0, 1, 0, 1, 0, 1]),
        C0=pikepdf.Array([0, 0, 0, 0]),
        C1=pikepdf.Array(full_tone),
        N=1,
    )


def _canonical_pdf_component(value: float):
    component = pikepdf.Array([value])[0]
    return int(component) if component in {0, 1} else component


def build_alternate_plan(
    pdf: pikepdf.Pdf,
    report: InspectionReport,
    spot: str,
    cmyk: NormalizedCmyk,
) -> AlternatePlan:
    """Build a complete preview mutation plan without changing the PDF."""

    return _PlanBuilder(pdf, report, spot, canonicalize_normalized_cmyk(cmyk)).build()


def canonicalize_normalized_cmyk(cmyk: NormalizedCmyk) -> NormalizedCmyk:
    """Round normalized components exactly as pikepdf will store PDF numbers."""

    values = tuple(float(_canonical_pdf_component(value)) for value in cmyk)
    return values[0], values[1], values[2], values[3]


__all__ = [
    "AlternatePlan",
    "NormalizedCmyk",
    "build_alternate_plan",
    "canonicalize_normalized_cmyk",
]
