"""Incremental PDF graph accounting for processing-budget preflight."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pikepdf

from .limits import ProcessingLimits, enforce_limit
from .objects import ObjectTracker


@dataclass(frozen=True)
class GraphBudgetResult:
    """Reachability work and unique Form streams found during one traversal."""

    reachable_objects: int
    forms: tuple[Any, ...]


def audit_reachable_graph(
    pdf: pikepdf.Pdf,
    limits: ProcessingLimits,
) -> GraphBudgetResult:
    """Count the trailer root and each reached container edge incrementally."""

    root = pdf.trailer
    reachable_objects = 1
    enforce_limit(limits, "reachable_objects", reachable_objects)

    tracker = ObjectTracker()
    forms: list[Any] = []
    root_children = _expand_container(root, tracker, forms)
    if root_children is None:
        return GraphBudgetResult(reachable_objects, ())

    iterators = [root_children]
    while iterators:
        try:
            child = next(iterators[-1])
        except StopIteration:
            iterators.pop()
            continue

        reachable_objects += 1
        enforce_limit(limits, "reachable_objects", reachable_objects)
        children = _expand_container(child, tracker, forms)
        if children is not None:
            iterators.append(children)

    return GraphBudgetResult(reachable_objects, tuple(forms))


def _expand_container(
    value: Any,
    tracker: ObjectTracker,
    forms: list[Any],
) -> Iterator[Any] | None:
    if not isinstance(value, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
        return None
    if not tracker.visit(value):
        return None
    if (
        isinstance(value, pikepdf.Stream)
        and value.get(pikepdf.Name.Subtype, None) == pikepdf.Name.Form
    ):
        forms.append(value)
    return _children(value)


def _children(value: Any) -> Iterator[Any]:
    """Yield values without materializing a large container edge tuple."""

    if isinstance(value, pikepdf.Array):
        yield from value
        return
    for key in value:
        yield value[key]


__all__ = ["GraphBudgetResult", "audit_reachable_graph"]
