"""Proven Page-to-XObject-to-Form resource graph for CMYK conversion."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import pikepdf

from .inventory_values import path_name
from .model import InvalidPdfError
from .objects import ObjectKey, object_key

ResourceContextKey = tuple[Any, ...]


@dataclass(frozen=True)
class ContentResourceContext:
    """One genuine Page/Form resource dictionary and approved owner paths."""

    key: ResourceContextKey
    resources: pikepdf.Dictionary
    locations: tuple[str, ...]
    owner_form_keys: frozenset[ObjectKey]


@dataclass(frozen=True)
class FormOwnerContext:
    """One genuine Form XObject slot and its effective resource identity."""

    form_key: ObjectKey
    form: pikepdf.Stream
    location: str
    effective_resource_key: ResourceContextKey
    inherits_resources: bool


@dataclass(frozen=True)
class FormOwnerAncestor:
    """One indirect genuine-content container above descendant Forms."""

    key: ObjectKey
    locations: tuple[str, ...]
    descendant_form_keys: frozenset[ObjectKey]


@dataclass(frozen=True)
class ContentResourceGraph:
    """All proven resource dictionaries and direct Form owner slots."""

    contexts: tuple[ContentResourceContext, ...]
    form_owners: tuple[FormOwnerContext, ...]
    form_owner_ancestors: tuple[FormOwnerAncestor, ...]


@dataclass
class _ContextDraft:
    key: ResourceContextKey
    resources: pikepdf.Dictionary
    locations: set[str] = field(default_factory=set)
    owner_form_keys: set[ObjectKey] = field(default_factory=set)
    ancestor_keys: set[ObjectKey] = field(default_factory=set)
    direct_form_keys: set[ObjectKey] = field(default_factory=set)


@dataclass
class _AncestorDraft:
    key: ObjectKey
    locations: set[str] = field(default_factory=set)
    descendant_form_keys: set[ObjectKey] = field(default_factory=set)


def build_content_resource_graph(pdf: pikepdf.Pdf) -> ContentResourceGraph:
    """Follow only genuine Page resources and their recursive Form XObjects."""

    drafts: dict[ResourceContextKey, _ContextDraft] = {}
    ancestors: dict[ObjectKey, _AncestorDraft] = {}
    form_child_contexts: dict[ObjectKey, set[ResourceContextKey]] = {}
    queue: deque[tuple[ResourceContextKey, str, str]] = deque()
    for page_number, page in enumerate(pdf.pages, start=1):
        resources = page.obj.get(pikepdf.Name.Resources, None)
        if not isinstance(resources, pikepdf.Dictionary):
            continue
        location = f"page {page_number} /Resources"
        key = _add_context(drafts, resources, ("page", page_number), location)
        _record_context_ancestor(drafts[key], ancestors, resources, location)
        queue.append((key, f"page {page_number}", location))

    owners: dict[tuple[ObjectKey, str], FormOwnerContext] = {}
    expanded: set[tuple[ResourceContextKey, str]] = set()
    while queue:
        context_key, root, location = queue.popleft()
        state = (context_key, root)
        if state in expanded:
            continue
        expanded.add(state)
        context = drafts[context_key]
        xobjects = context.resources.get(pikepdf.Name.XObject, None)
        if not isinstance(xobjects, pikepdf.Dictionary):
            continue
        xobject_location = f"{location} /XObject"
        _record_context_ancestor(context, ancestors, xobjects, xobject_location)
        for name, candidate in xobjects.items():
            if not _is_form(candidate):
                continue
            form_key = object_key(candidate)
            context.direct_form_keys.add(form_key)
            owner_location = f"{xobject_location} {path_name(name)}"
            _record_ancestor(ancestors, candidate, owner_location)
            own_resources = candidate.get(pikepdf.Name.Resources, None)
            if isinstance(own_resources, pikepdf.Dictionary):
                resource_location = f"{owner_location} /Resources"
                effective_key = _add_context(
                    drafts,
                    own_resources,
                    ("form", *form_key),
                    resource_location,
                    owner_form_key=form_key,
                )
                _record_context_ancestor(
                    drafts[effective_key],
                    ancestors,
                    own_resources,
                    resource_location,
                )
                form_child_contexts.setdefault(form_key, set()).add(effective_key)
                queue.append((effective_key, root, resource_location))
                inherits = False
            else:
                if pikepdf.Name.Resources in candidate:
                    raise InvalidPdfError(f"{owner_location}: malformed Form /Resources dictionary")
                effective_key = context_key
                inherits = True
            owners[(form_key, owner_location)] = FormOwnerContext(
                form_key,
                candidate,
                owner_location,
                effective_key,
                inherits,
            )

    _add_approved_context_aliases(drafts, ancestors, owners)
    descendants = _descendant_forms(drafts, form_child_contexts)
    for context_key, context in drafts.items():
        for ancestor_key in context.ancestor_keys:
            ancestors[ancestor_key].descendant_form_keys.update(descendants[context_key])
    for form_key, child_contexts in form_child_contexts.items():
        if form_key not in ancestors:
            continue
        for context_key in child_contexts:
            ancestors[form_key].descendant_form_keys.update(descendants[context_key])

    contexts = tuple(
        ContentResourceContext(
            draft.key,
            draft.resources,
            tuple(sorted(draft.locations)),
            frozenset(draft.owner_form_keys),
        )
        for draft in sorted(drafts.values(), key=lambda item: min(item.locations))
    )
    return ContentResourceGraph(
        contexts,
        tuple(sorted(owners.values(), key=lambda item: item.location)),
        tuple(
            FormOwnerAncestor(
                draft.key,
                tuple(sorted(draft.locations)),
                frozenset(draft.descendant_form_keys),
            )
            for draft in sorted(ancestors.values(), key=lambda item: min(item.locations))
        ),
    )


def _record_ancestor(
    drafts: dict[ObjectKey, _AncestorDraft],
    value: object,
    location: str,
) -> ObjectKey | None:
    key = object_key(value)
    if key[0] != "indirect":
        return None
    draft = drafts.setdefault(key, _AncestorDraft(key))
    draft.locations.add(location)
    return key


def _record_context_ancestor(
    context: _ContextDraft,
    ancestors: dict[ObjectKey, _AncestorDraft],
    value: object,
    location: str,
) -> None:
    key = _record_ancestor(ancestors, value, location)
    if key is not None:
        context.ancestor_keys.add(key)


def _add_approved_context_aliases(
    contexts: dict[ResourceContextKey, _ContextDraft],
    ancestors: dict[ObjectKey, _AncestorDraft],
    owners: dict[tuple[ObjectKey, str], FormOwnerContext],
) -> None:
    """Add every genuine location accumulated for shared resource contexts."""

    for context in contexts.values():
        xobjects = context.resources.get(pikepdf.Name.XObject, None)
        if not isinstance(xobjects, pikepdf.Dictionary):
            continue
        for location in context.locations:
            xobject_location = f"{location} /XObject"
            _record_context_ancestor(context, ancestors, xobjects, xobject_location)
            for name, candidate in xobjects.items():
                if not _is_form(candidate):
                    continue
                form_key = object_key(candidate)
                owner_location = f"{xobject_location} {path_name(name)}"
                _record_ancestor(ancestors, candidate, owner_location)
                own_resources = candidate.get(pikepdf.Name.Resources, None)
                if isinstance(own_resources, pikepdf.Dictionary):
                    effective_key = _resource_context_key(
                        own_resources,
                        ("form", *form_key),
                    )
                    inherits = False
                else:
                    effective_key = context.key
                    inherits = True
                owners.setdefault(
                    (form_key, owner_location),
                    FormOwnerContext(
                        form_key,
                        candidate,
                        owner_location,
                        effective_key,
                        inherits,
                    ),
                )


def _descendant_forms(
    contexts: dict[ResourceContextKey, _ContextDraft],
    form_child_contexts: dict[ObjectKey, set[ResourceContextKey]],
) -> dict[ResourceContextKey, set[ObjectKey]]:
    """Resolve transitive Form descendants for every proven resource context."""

    descendants = {key: set(context.direct_form_keys) for key, context in contexts.items()}
    changed = True
    while changed:
        changed = False
        for context_key, context in contexts.items():
            additions: set[ObjectKey] = set()
            for form_key in context.direct_form_keys:
                for child_key in form_child_contexts.get(form_key, set()):
                    additions.update(descendants[child_key])
            if not additions.issubset(descendants[context_key]):
                descendants[context_key].update(additions)
                changed = True
    return descendants


def _add_context(
    drafts: dict[ResourceContextKey, _ContextDraft],
    resources: pikepdf.Dictionary,
    anchor: tuple[Any, ...],
    location: str,
    *,
    owner_form_key: ObjectKey | None = None,
) -> ResourceContextKey:
    key = _resource_context_key(resources, anchor)
    draft = drafts.get(key)
    if draft is None:
        draft = _ContextDraft(key, resources)
        drafts[key] = draft
    draft.locations.add(location)
    if owner_form_key is not None:
        draft.owner_form_keys.add(owner_form_key)
    return key


def _resource_context_key(
    resources: pikepdf.Dictionary,
    anchor: tuple[Any, ...],
) -> ResourceContextKey:
    key = object_key(resources)
    if key[0] == "indirect":
        return ("resources", *key)
    return ("resources-at", *anchor)


def _is_form(value: object) -> bool:
    return (
        isinstance(value, pikepdf.Stream)
        and value.get(pikepdf.Name.Subtype, None) == pikepdf.Name.Form
    )


__all__ = [
    "ContentResourceContext",
    "ContentResourceGraph",
    "FormOwnerAncestor",
    "FormOwnerContext",
    "ResourceContextKey",
    "build_content_resource_graph",
]
