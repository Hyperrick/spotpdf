"""Reachability traversal for the semantic PDF inventory."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pikepdf

from .inventory_values import path_name
from .objects import ObjectKey, object_key


@dataclass(frozen=True)
class GraphVisit:
    """One reachable container with all known human-readable contexts."""

    value: Any
    locations: tuple[str, ...]
    page_label: str | None = None


@dataclass(frozen=True)
class _GraphContext:
    """One root context and its current human-readable path."""

    root: str
    path: str


def walk_reachable(pdf: pikepdf.Pdf) -> Iterator[GraphVisit]:
    """Yield containers once per root context while caching graph edges."""

    expanded: set[tuple[ObjectKey, str]] = set()
    edges: dict[ObjectKey, tuple[tuple[str, Any], ...]] = {}
    direct_objects: list[Any] = []
    retained_direct_keys: set[ObjectKey] = set()
    page_labels = {
        object_key(page.obj): f"page {page_number}"
        for page_number, page in enumerate(pdf.pages, start=1)
    }
    stack = [
        (pdf.Root, (_GraphContext("catalog", "catalog"),)),
        *_resource_roots(pdf),
    ]
    while stack:
        current, contexts = stack.pop()
        if not isinstance(current, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            continue

        key = object_key(current)
        if key[0] == "direct" and key not in retained_direct_keys:
            retained_direct_keys.add(key)
            direct_objects.append(current)

        page_label = page_labels.get(key)
        if page_label is not None:
            contexts = (_GraphContext(page_label, page_label),)
        yield GraphVisit(current, tuple(context.path for context in contexts), page_label)

        fresh_by_root: dict[str, _GraphContext] = {}
        for context in contexts:
            visit_key = (key, context.root)
            if visit_key not in expanded:
                expanded.add(visit_key)
                fresh_by_root.setdefault(context.root, context)
        fresh_contexts = tuple(fresh_by_root.values())
        if not fresh_contexts:
            continue
        object_edges = edges.get(key)
        if object_edges is None:
            object_edges = _object_edges(current)
            edges[key] = object_edges
        children = [
            (
                value,
                tuple(
                    _GraphContext(context.root, f"{context.path}{segment}")
                    for context in fresh_contexts
                ),
            )
            for segment, value in object_edges
        ]
        stack.extend(reversed(children))


def _object_edges(value: Any) -> tuple[tuple[str, Any], ...]:
    """Return cached path segments and child values for one container."""

    if isinstance(value, pikepdf.Array):
        return tuple((f"[{index}]", child) for index, child in enumerate(value))
    is_page_tree_node = value.get(pikepdf.Name.Type, None) in (
        pikepdf.Name.Page,
        pikepdf.Name.Pages,
    )
    return tuple(
        (f" {path_name(name)}", child)
        for name, child in value.items()
        if not (is_page_tree_node and name == pikepdf.Name.Parent)
    )


def _resource_roots(pdf: pikepdf.Pdf) -> list[tuple[Any, tuple[_GraphContext, ...]]]:
    """Return page resource dictionaries with every page that shares them."""

    grouped: dict[ObjectKey, tuple[Any, set[str]]] = {}
    for page_number, page in enumerate(pdf.pages, start=1):
        try:
            resources = page.Resources
        except (AttributeError, KeyError):
            continue
        key = object_key(resources)
        if key not in grouped:
            grouped[key] = (resources, set())
        grouped[key][1].add(f"page {page_number} /Resources")
    return [
        (
            resources,
            tuple(_GraphContext(location, location) for location in sorted(locations)),
        )
        for resources, locations in grouped.values()
    ]
