"""Exact resource-alias planning for safe spot-paint removal."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pikepdf

from .colors import parse_color_space
from .convert_resource_contexts import (
    ContentResourceContext,
    ContentResourceGraph,
    ResourceContextKey,
    build_content_resource_graph,
)
from .inventory_graph import walk_reachable_with_trailer_roots
from .inventory_values import path_name, separation_name
from .model import InspectionReport, SpotKind, SpotPdfError, UnsupportedSpotUseError
from .objects import ObjectKey, object_key
from .rename_slots import semantic_object_fingerprint
from .resource_owners import reject_unapproved_resource_container_owners

_PAGE_RESOURCES = re.compile(r"^page \d+ /Resources$")
_DEFAULT_COLOR_SPACES = {
    pikepdf.Name.DefaultGray,
    pikepdf.Name.DefaultRGB,
    pikepdf.Name.DefaultCMYK,
}


@dataclass
class RemovalResourceAlias:
    """One exact target Separation alias approved for deletion."""

    color_spaces: pikepdf.Dictionary
    key: pikepdf.Name
    target: str
    original_fingerprint: tuple[Any, ...]
    locations: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return min(self.locations)

    def apply(self) -> None:
        """Verify the planned member and delete it exactly once."""

        if self.key not in self.color_spaces:
            raise SpotPdfError(f"target resource disappeared before apply at {self.label}")
        value = self.color_spaces[self.key]
        if _target_name(value, frozenset({self.target})) != self.target:
            raise SpotPdfError(f"target resource changed before apply at {self.label}")
        if semantic_object_fingerprint(value) != self.original_fingerprint:
            raise SpotPdfError(f"target resource changed before apply at {self.label}")
        del self.color_spaces[self.key]


def collect_removal_resource_aliases(
    pdf: pikepdf.Pdf,
    report: InspectionReport,
    targets: frozenset[str],
    processed_form_resources: Mapping[ObjectKey, frozenset[tuple[Any, ...]]],
) -> tuple[RemovalResourceAlias, ...]:
    """Plan aliases only in content contexts proven by the removal dry run."""

    graph = build_content_resource_graph(pdf)
    target_context_keys = _target_context_keys(graph, targets)
    _reject_unprocessed_target_form_owners(
        graph,
        target_context_keys,
        processed_form_resources,
    )

    removals: dict[tuple[Any, ...], RemovalResourceAlias] = {}
    target_contexts: list[tuple[ContentResourceContext, pikepdf.Dictionary]] = []
    for context in graph.contexts:
        if not _context_was_processed(context, processed_form_resources):
            continue
        color_spaces = context.resources.get(pikepdf.Name.ColorSpace, None)
        if not isinstance(color_spaces, pikepdf.Dictionary):
            continue
        context_has_target = False
        for key, value in color_spaces.items():
            target = _target_name(value, targets)
            if target is None:
                continue
            context_has_target = True
            slot_locations = {
                f"{location} /ColorSpace {path_name(key)}" for location in context.locations
            }
            if key in _DEFAULT_COLOR_SPACES:
                raise UnsupportedSpotUseError(
                    f"{min(slot_locations)}: target is a default color-space override",
                    location=min(slot_locations),
                )
            identity = (*object_key(color_spaces), str(key))
            proposed = RemovalResourceAlias(
                color_spaces=color_spaces,
                key=key,
                target=target,
                original_fingerprint=semantic_object_fingerprint(value),
                locations=slot_locations,
            )
            current = removals.setdefault(identity, proposed)
            if (
                current.target != proposed.target
                or current.original_fingerprint != proposed.original_fingerprint
            ):
                raise UnsupportedSpotUseError(
                    f"{min(slot_locations)}: shared resource alias resolves inconsistently",
                    location=min(slot_locations),
                )
            current.locations.update(slot_locations)
        if context_has_target:
            target_contexts.append((context, color_spaces))

    reject_unapproved_resource_container_owners(pdf, graph, target_contexts)
    _require_complete_location_coverage(pdf, report, targets, removals.values())
    return tuple(sorted(removals.values(), key=lambda item: item.label))


def _target_context_keys(
    graph: ContentResourceGraph,
    targets: frozenset[str],
) -> frozenset[tuple[Any, ...]]:
    return frozenset(
        rewriter_resource_key(context.key)
        for context in graph.contexts
        if _resources_contain_targets(context.resources, targets)
    )


def _reject_unprocessed_target_form_owners(
    graph: ContentResourceGraph,
    target_context_keys: frozenset[tuple[Any, ...]],
    processed_form_resources: Mapping[ObjectKey, frozenset[tuple[Any, ...]]],
) -> None:
    contexts = {context.key: context for context in graph.contexts}
    for owner in graph.form_owners:
        context = contexts[owner.effective_resource_key]
        expected = rewriter_resource_key(context.key)
        actual = processed_form_resources.get(owner.form_key, frozenset())
        if not actual and expected in target_context_keys:
            raise UnsupportedSpotUseError(
                f"{owner.location}: uninvoked Form has target spot resources",
                location=owner.location,
            )
        if (
            actual
            and expected not in actual
            and (not actual.isdisjoint(target_context_keys) or expected in target_context_keys)
        ):
            raise UnsupportedSpotUseError(
                f"{owner.location}: a shared Form requires context-dependent rewriting",
                location=owner.location,
            )


def _context_was_processed(
    context: ContentResourceContext,
    processed_form_resources: Mapping[ObjectKey, frozenset[tuple[Any, ...]]],
) -> bool:
    if any(_PAGE_RESOURCES.fullmatch(location) for location in context.locations):
        return True
    expected = rewriter_resource_key(context.key)
    return any(
        expected in processed_form_resources.get(form_key, frozenset())
        for form_key in context.owner_form_keys
    )


def rewriter_resource_key(key: ResourceContextKey) -> tuple[Any, ...]:
    if key[0] == "resources":
        return tuple(key[1:])
    if key[0] == "resources-at":
        return ("direct-at", *key[1:], "Resources")
    raise SpotPdfError(f"unexpected content resource identity: {key!r}")


def _resources_contain_targets(resources: pikepdf.Dictionary, targets: frozenset[str]) -> bool:
    color_spaces = resources.get(pikepdf.Name.ColorSpace, None)
    return isinstance(color_spaces, pikepdf.Dictionary) and any(
        _target_name(value, targets) is not None for value in color_spaces.values()
    )


def _target_name(value: object, targets: frozenset[str]) -> str | None:
    target = separation_name(value)
    return target if target in targets else None


def _require_complete_location_coverage(
    pdf: pikepdf.Pdf,
    report: InspectionReport,
    targets: frozenset[str],
    removals: Iterable[RemovalResourceAlias],
) -> None:
    planned = {location for removal in removals for location in removal.locations}
    expected = {
        location: definition.kind
        for definition in report.definitions.values()
        if definition.kind is SpotKind.SEPARATION
        and any(component.name in targets for component in definition.components)
        for location in definition.locations
    }
    for visit in walk_reachable_with_trailer_roots(pdf):
        info = parse_color_space(visit.value)
        if not info.contains_any(targets) or info.kind is None:
            continue
        for location in visit.locations:
            expected.setdefault(location, info.kind)
    unplanned = sorted(expected.keys() - planned)
    if unplanned:
        location = unplanned[0]
        raise UnsupportedSpotUseError(
            f"{location}: target {expected[location].value} is not exclusively a removable "
            "content-resource alias",
            location=location,
        )
    if not expected or not planned:
        raise UnsupportedSpotUseError(
            "target Separation has no proven removable content-resource alias"
        )


__all__ = [
    "RemovalResourceAlias",
    "collect_removal_resource_aliases",
    "rewriter_resource_key",
]
