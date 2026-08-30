"""Owner-role preflight for content streams planned for CMYK conversion."""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

import pikepdf

from .convert_resource_contexts import (
    ContentResourceGraph,
    FormOwnerContext,
    build_content_resource_graph,
)
from .inventory_graph import walk_reachable_with_trailer_roots
from .model import UnsupportedSpotUseError
from .objects import ObjectKey, object_key

_PAGE_CONTENT = re.compile(r"^page \d+ /Contents(?:\[\d+\])?$")
_PAGE_CONTENT_ARRAY = re.compile(r"^page \d+ /Contents\[\d+\]$")


class _PlannedWrite(Protocol):
    key: ObjectKey
    kind: str
    label: str


class _OwnerRole(StrEnum):
    PAGE_CONTENT = "page content"
    FORM_XOBJECT = "Form XObject content"
    EMBEDDED_FILE = "embedded-file data"
    METADATA = "metadata stream data"
    FONT_DATA = "font stream data"
    OUTPUT_PROFILE = "output-profile data"
    JAVASCRIPT = "JavaScript stream data"
    OTHER = "a non-content stream role"


def reject_unsafe_planned_stream_owners(
    pdf: pikepdf.Pdf,
    writes: Sequence[_PlannedWrite],
) -> None:
    """Require every planned stream reference to have only content semantics."""

    planned = {write.key: write for write in writes}
    resource_graph = build_content_resource_graph(pdf)
    ancestor_approvals = _planned_ancestor_approvals(
        pdf,
        frozenset(planned),
        resource_graph,
    )
    observed = _collect_owner_locations(
        pdf,
        frozenset({*planned, *ancestor_approvals}),
    )
    _reject_unsafe_ancestor_owners(ancestor_approvals, observed)
    allowed_form_owners = _allowed_form_owners(resource_graph, frozenset(planned))
    form_owners = _form_owners_by_key(resource_graph.form_owners)

    for key, write in planned.items():
        stream_locations = observed[key]
        if not stream_locations:
            raise UnsupportedSpotUseError(
                f"{write.label}: planned content stream has no reachable owner"
            )
        _reject_unsafe_locations(
            write,
            stream_locations,
            allowed_form_owners[key],
            form_owners.get(key, ()),
        )


def _collect_owner_locations(
    pdf: pikepdf.Pdf,
    tracked: frozenset[ObjectKey],
) -> dict[ObjectKey, set[str]]:
    locations = {key: set() for key in tracked}
    for visit in walk_reachable_with_trailer_roots(pdf):
        key = object_key(visit.value)
        if key in locations:
            locations[key].update(visit.locations)
    return locations


def _allowed_form_owners(
    resource_graph: ContentResourceGraph,
    planned: frozenset[ObjectKey],
) -> dict[ObjectKey, dict[str, _OwnerRole]]:
    allowed = {key: {} for key in planned}
    for owner in resource_graph.form_owners:
        _record_owner_key(
            owner.form_key,
            owner.location,
            _OwnerRole.FORM_XOBJECT,
            planned,
            allowed,
        )
    return allowed


def _planned_ancestor_approvals(
    pdf: pikepdf.Pdf,
    planned: frozenset[ObjectKey],
    resource_graph: ContentResourceGraph,
) -> dict[ObjectKey, set[str]]:
    approvals: dict[ObjectKey, set[str]] = {}
    for ancestor in resource_graph.form_owner_ancestors:
        if ancestor.descendant_form_keys & planned:
            approvals.setdefault(ancestor.key, set()).update(ancestor.locations)
    for page_number, page in enumerate(pdf.pages, start=1):
        contents = page.obj.get(pikepdf.Name.Contents, None)
        key = object_key(contents)
        if key[0] != "indirect" or not isinstance(contents, pikepdf.Array):
            continue
        if not any(object_key(item) in planned for item in contents):
            continue
        approvals.setdefault(key, set()).add(f"page {page_number} /Contents")
    return approvals


def _reject_unsafe_ancestor_owners(
    approvals: dict[ObjectKey, set[str]],
    observed: dict[ObjectKey, set[str]],
) -> None:
    for key, approved_locations in approvals.items():
        unexpected = observed[key] - approved_locations
        if unexpected:
            raise UnsupportedSpotUseError(
                f"{min(unexpected)}: planned content owner container has a non-content owner"
            )


def _record_owner_key(
    key: ObjectKey,
    location: str,
    role: _OwnerRole,
    planned: frozenset[ObjectKey],
    allowed: dict[ObjectKey, dict[str, _OwnerRole]],
) -> None:
    if key in planned:
        allowed[key][location] = role


def _reject_unsafe_locations(
    write: _PlannedWrite,
    locations: set[str],
    allowed_form_owners: dict[str, _OwnerRole],
    form_owners: tuple[FormOwnerContext, ...],
) -> None:
    page_locations = {location for location in locations if _PAGE_CONTENT.fullmatch(location)}
    external_locations = locations - page_locations
    array_member = any(_PAGE_CONTENT_ARRAY.fullmatch(location) for location in page_locations)
    if page_locations and (external_locations or write.kind == "Form"):
        if external_locations:
            location = min(external_locations)
            role = allowed_form_owners.get(location, _classify_external_owner(location))
        else:
            location = min(page_locations)
            role = _OwnerRole.PAGE_CONTENT
        subject = "Contents-array member" if array_member else "content stream"
        _raise_owner_error(write, location, role, subject)

    if write.kind == "Page":
        if not page_locations:
            _raise_owner_error(
                write,
                min(locations),
                _classify_external_owner(min(locations)),
                "page content stream",
            )
        return

    inherited_contexts = {
        _rewriter_resource_key(owner.effective_resource_key)
        for owner in form_owners
        if owner.inherits_resources
    }
    approved_inherited_contexts = getattr(
        write,
        "approved_inherited_contexts",
        frozenset(),
    )
    if len(inherited_contexts) > 1 and inherited_contexts != approved_inherited_contexts:
        raise UnsupportedSpotUseError(
            f"{write.label}: a resource-inheriting Form has multiple owner contexts"
        )

    for location in sorted(external_locations):
        role = allowed_form_owners.get(location, _classify_external_owner(location))
        if location in allowed_form_owners:
            continue
        _raise_owner_error(write, location, role, "content stream")


def _form_owners_by_key(
    owners: tuple[FormOwnerContext, ...],
) -> dict[ObjectKey, tuple[FormOwnerContext, ...]]:
    grouped: dict[ObjectKey, list[FormOwnerContext]] = {}
    for owner in owners:
        grouped.setdefault(owner.form_key, []).append(owner)
    return {key: tuple(items) for key, items in grouped.items()}


def _rewriter_resource_key(key: tuple[object, ...]) -> tuple[object, ...]:
    if key[0] == "resources":
        return tuple(key[1:])
    if key[0] == "resources-at":
        return ("direct-at", *key[1:], "Resources")
    return key


def _classify_external_owner(location: str) -> _OwnerRole:
    if " /EmbeddedFiles" in location or " /EF /" in location:
        return _OwnerRole.EMBEDDED_FILE
    if location.endswith(" /Metadata") or " /Metadata " in location:
        return _OwnerRole.METADATA
    if any(
        marker in location for marker in (" /FontFile", " /FontFile2", " /FontFile3", " /ToUnicode")
    ):
        return _OwnerRole.FONT_DATA
    if " /DestOutputProfile" in location:
        return _OwnerRole.OUTPUT_PROFILE
    if location.endswith(" /JS") or " /JS " in location:
        return _OwnerRole.JAVASCRIPT
    return _OwnerRole.OTHER


def _raise_owner_error(
    write: _PlannedWrite,
    location: str,
    role: _OwnerRole,
    subject: str,
) -> None:
    raise UnsupportedSpotUseError(
        f"{write.label}: planned {subject} is also reachable as {role.value} at {location}"
    )


__all__ = ["reject_unsafe_planned_stream_owners"]
