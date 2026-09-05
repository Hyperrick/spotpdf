"""Owner validation for resource containers planned for mutation."""

from __future__ import annotations

import pikepdf

from .convert_resource_contexts import ContentResourceContext, ContentResourceGraph
from .inventory_graph import walk_reachable_with_trailer_roots
from .model import UnsupportedSpotUseError
from .objects import ObjectKey, object_key


def reject_unapproved_resource_container_owners(
    pdf: pikepdf.Pdf,
    graph: ContentResourceGraph,
    targets: list[tuple[ContentResourceContext, pikepdf.Dictionary]],
) -> None:
    """Reject an indirect resource container that also has a non-content owner."""

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
                f"{min(unexpected)}: target resource container has a non-content owner",
                location=min(unexpected),
            )


def _record_indirect_approval(
    approved: dict[ObjectKey, set[str]],
    value: object,
    locations: set[str],
) -> None:
    key = object_key(value)
    if key[0] == "indirect":
        approved.setdefault(key, set()).update(locations)


__all__ = ["reject_unapproved_resource_container_owners"]
