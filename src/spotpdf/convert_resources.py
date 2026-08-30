"""Exact resource-dictionary removals for a completed conversion plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pikepdf

from .convert_resource_contexts import (
    ContentResourceContext,
    ContentResourceGraph,
    build_content_resource_graph,
)
from .inventory_graph import walk_reachable_with_trailer_roots
from .inventory_values import path_name, separation_name
from .model import SpotPdfError, UnsupportedSpotUseError
from .objects import ObjectKey, object_key
from .rename_slots import semantic_object_fingerprint
from .separation_targets import SeparationTargetSet

_DEFAULT_COLOR_SPACES = {
    pikepdf.Name.DefaultGray,
    pikepdf.Name.DefaultRGB,
    pikepdf.Name.DefaultCMYK,
}


@dataclass
class ColorSpaceRemoval:
    """One exact `/Resources /ColorSpace` dictionary member to delete."""

    color_spaces: pikepdf.Dictionary
    key: pikepdf.Name
    definition_id: str
    original_fingerprint: tuple[Any, ...]
    locations: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return min(self.locations)

    def verify_original(self, spot: str) -> None:
        if self.key not in self.color_spaces:
            raise SpotPdfError(f"target resource disappeared before apply at {self.label}")
        value = self.color_spaces[self.key]
        if separation_name(value) != spot:
            raise SpotPdfError(f"target resource changed before apply at {self.label}")
        if semantic_object_fingerprint(value) != self.original_fingerprint:
            raise SpotPdfError(f"target resource changed before apply at {self.label}")

    def verify_removed(self) -> None:
        if self.key in self.color_spaces:
            raise SpotPdfError(f"target resource remains after apply at {self.label}")


def collect_target_resource_removals(
    pdf: pikepdf.Pdf,
    targets: SeparationTargetSet,
) -> tuple[ColorSpaceRemoval, ...]:
    """Plan all and only target aliases from actual resource dictionaries."""

    removals: dict[tuple[Any, ...], ColorSpaceRemoval] = {}
    resource_graph = build_content_resource_graph(pdf)
    target_contexts: list[tuple[ContentResourceContext, pikepdf.Dictionary]] = []
    for context in resource_graph.contexts:
        color_spaces = context.resources.get(pikepdf.Name.ColorSpace, None)
        if not isinstance(color_spaces, pikepdf.Dictionary):
            continue
        context_has_target = False
        for key, value in color_spaces.items():
            if separation_name(value) != targets.spot:
                continue
            context_has_target = True
            slot_locations = tuple(
                f"{location} /ColorSpace {path_name(key)}" for location in context.locations
            )
            if key in _DEFAULT_COLOR_SPACES:
                raise UnsupportedSpotUseError(
                    f"{min(slot_locations)}: target is a default color-space override"
                )
            definition_id = targets.definition_id_for(slot_locations)
            identity = (*object_key(color_spaces), str(key))
            proposed = ColorSpaceRemoval(
                color_spaces=color_spaces,
                key=key,
                definition_id=definition_id,
                original_fingerprint=semantic_object_fingerprint(value),
                locations=set(slot_locations),
            )
            current = removals.setdefault(identity, proposed)
            if (
                current.definition_id != proposed.definition_id
                or current.original_fingerprint != proposed.original_fingerprint
            ):
                raise UnsupportedSpotUseError(
                    f"{min(slot_locations)}: shared resource alias resolves inconsistently"
                )
            current.locations.update(slot_locations)
        if context_has_target:
            target_contexts.append((context, color_spaces))

    _reject_unapproved_container_owners(pdf, resource_graph, target_contexts)

    planned_locations = frozenset(
        location for removal in removals.values() for location in removal.locations
    )
    if not targets.locations.issubset(planned_locations):
        unsupported = sorted(targets.locations - planned_locations)
        detail = min(unsupported or ["unknown location"])
        raise UnsupportedSpotUseError(
            f"{detail}: target Separation is not exclusively a removable resource alias"
        )
    planned_ids = {removal.definition_id for removal in removals.values()}
    if planned_ids != targets.definition_ids:
        raise UnsupportedSpotUseError(
            "not every target Separation definition has a removable resource alias"
        )
    return tuple(sorted(removals.values(), key=lambda item: item.label))


def _reject_unapproved_container_owners(
    pdf: pikepdf.Pdf,
    graph: ContentResourceGraph,
    targets: list[tuple[ContentResourceContext, pikepdf.Dictionary]],
) -> None:
    approved: dict[ObjectKey, set[str]] = {}
    approved_form_locations: dict[ObjectKey, set[str]] = {}
    for owner in graph.form_owners:
        approved_form_locations.setdefault(owner.form_key, set()).add(owner.location)

    for context, color_spaces in targets:
        _record_indirect_approval(approved, context.resources, set(context.locations))
        _record_indirect_approval(
            approved,
            color_spaces,
            {f"{location} /ColorSpace" for location in context.locations},
        )
        for form_key in context.owner_form_keys:
            approved.setdefault(form_key, set()).update(
                approved_form_locations.get(form_key, set())
            )

    if not approved:
        return
    observed = {key: set() for key in approved}
    for visit in walk_reachable_with_trailer_roots(pdf):
        key = object_key(visit.value)
        if key in observed:
            observed[key].update(visit.locations)
    for key, locations in observed.items():
        unexpected = locations - approved[key]
        if unexpected:
            raise UnsupportedSpotUseError(
                f"{min(unexpected)}: target resource container has a non-content owner"
            )


def _record_indirect_approval(
    approved: dict[ObjectKey, set[str]],
    value: object,
    locations: set[str],
) -> None:
    key = object_key(value)
    if key[0] == "indirect":
        approved.setdefault(key, set()).update(locations)


__all__ = ["ColorSpaceRemoval", "collect_target_resource_removals"]
