"""Validated, immutable collection of one exact Separation conversion target."""

from __future__ import annotations

from dataclasses import dataclass
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
from .rename_slots import semantic_object_fingerprint


@dataclass(frozen=True)
class SeparationTargetDefinition:
    """One complete target definition and all paths that reach it."""

    definition_id: str
    separation: pikepdf.Array
    locations: tuple[str, ...]
    original_fingerprint: tuple[Any, ...]

    def verify_original(self, spot: str) -> None:
        if not _is_complete_target(self.separation, spot):
            raise SpotPdfError(f"Separation identity changed before apply at {min(self.locations)}")
        if semantic_object_fingerprint(self.separation) != self.original_fingerprint:
            raise SpotPdfError(
                f"Separation definition changed before apply at {min(self.locations)}"
            )


@dataclass(frozen=True)
class SeparationTargetSet:
    """Every reachable definition for one unambiguous spot plate."""

    spot: str
    definitions: tuple[SeparationTargetDefinition, ...]

    @property
    def definition_ids(self) -> frozenset[str]:
        return frozenset(item.definition_id for item in self.definitions)

    @property
    def locations(self) -> frozenset[str]:
        return frozenset(location for item in self.definitions for location in item.locations)

    def definition_id_for(self, locations: tuple[str, ...]) -> str:
        matches = {
            item.definition_id
            for item in self.definitions
            if not set(locations).isdisjoint(item.locations)
        }
        if len(matches) != 1:
            label = min(locations) if locations else "unknown location"
            raise UnsupportedSpotUseError(
                f"{label}: target Separation has no unique inventory identity"
            )
        return matches.pop()

    def verify_original(self) -> None:
        for definition in self.definitions:
            definition.verify_original(self.spot)


@dataclass
class _DefinitionDraft:
    definition_id: str
    separation: pikepdf.Array
    locations: set[str]
    original_fingerprint: tuple[Any, ...]


class _TargetCollector:
    def __init__(self, pdf: pikepdf.Pdf, report: InspectionReport, spot: str) -> None:
        self.pdf = pdf
        self.report = report
        self.spot = spot
        self.definitions: dict[str, _DefinitionDraft] = {}

    def collect(self) -> SeparationTargetSet:
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
        definitions = tuple(
            SeparationTargetDefinition(
                definition_id=draft.definition_id,
                separation=draft.separation,
                locations=tuple(sorted(draft.locations)),
                original_fingerprint=draft.original_fingerprint,
            )
            for draft in sorted(self.definitions.values(), key=lambda item: item.definition_id)
        )
        return SeparationTargetSet(self.spot, definitions)

    def _validate_request(self) -> None:
        if self.spot in SPECIAL_COLORANTS:
            raise InvalidPdfError("reserved /All and /None colorants cannot be converted")
        if self.spot in PROCESS_COLORANTS:
            raise InvalidPdfError("canonical process colorants cannot be converted")
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
                f"DeviceN use of {self.spot!r} is not supported by convert"
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
        if not _is_complete_target(value, self.spot):
            raise UnsupportedSpotUseError(
                f"{location}: malformed Separation array cannot be converted safely"
            )
        validate_existing_preview(value[2], value[3], location)
        definition_id = self._definition_id(locations)
        fingerprint = semantic_object_fingerprint(value)
        draft = self.definitions.get(definition_id)
        if draft is None:
            self.definitions[definition_id] = _DefinitionDraft(
                definition_id,
                value,
                set(locations),
                fingerprint,
            )
            return
        if draft.original_fingerprint != fingerprint:
            raise UnsupportedSpotUseError(
                f"{location}: inventory identity maps to conflicting Separation arrays"
            )
        draft.locations.update(locations)

    def _definition_id(self, locations: tuple[str, ...]) -> str:
        location_set = set(locations)
        matches = {
            definition.object_id
            for definition in self.report.definitions.values()
            if definition.kind is SpotKind.SEPARATION
            and any(component.name == self.spot for component in definition.components)
            and not location_set.isdisjoint(definition.locations)
        }
        if len(matches) != 1:
            raise UnsupportedSpotUseError(
                f"{min(locations)}: Separation has no unique inventory identity"
            )
        return matches.pop()

    def _expected_definition_ids(self) -> set[str]:
        return {
            definition.object_id
            for definition in self.report.definitions.values()
            if definition.kind is SpotKind.SEPARATION
            and any(component.name == self.spot for component in definition.components)
        }

    def _validate_coverage(self) -> None:
        expected = self._expected_definition_ids()
        actual = set(self.definitions)
        if actual != expected:
            raise UnsupportedSpotUseError(
                "inventory Separation definitions could not be mapped one-to-one "
                f"(expected {len(expected)}, planned {len(actual)})"
            )


def _is_complete_target(value: Any, spot: str) -> bool:
    return (
        isinstance(value, pikepdf.Array)
        and len(value) == 4
        and name_value(value[0]) == "Separation"
        and name_value(value[1]) == spot
        and isinstance(value[2], (pikepdf.Name, pikepdf.Array))
        and isinstance(value[3], (pikepdf.Dictionary, pikepdf.Stream))
    )


def collect_separation_targets(
    pdf: pikepdf.Pdf,
    report: InspectionReport,
    spot: str,
) -> SeparationTargetSet:
    """Collect every exact target definition without evaluating its tint function."""

    return _TargetCollector(pdf, report, spot).collect()


__all__ = [
    "SeparationTargetDefinition",
    "SeparationTargetSet",
    "collect_separation_targets",
]
