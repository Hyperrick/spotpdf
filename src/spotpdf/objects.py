"""Stable traversal helpers for pikepdf object graphs."""

from __future__ import annotations

from typing import Any

ObjectKey = tuple[str, int, int] | tuple[str, int]


def object_key(obj: Any) -> ObjectKey:
    """Return an object key that is stable while direct wrappers stay alive."""

    try:
        objgen = tuple(obj.objgen)
    except (AttributeError, TypeError):
        objgen = (0, 0)
    if objgen != (0, 0):
        return ("indirect", int(objgen[0]), int(objgen[1]))
    return ("direct", id(obj))


def anchored_object_key(obj: Any, anchor: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return stable identity for indirect objects and anchored direct values."""

    key = object_key(obj)
    if key[0] == "indirect":
        return key
    return ("direct-at", *anchor)


class ObjectTracker:
    """Track visited objects and retain direct wrappers to prevent id reuse."""

    def __init__(self) -> None:
        self._seen: set[ObjectKey] = set()
        self._direct_objects: list[Any] = []

    def visit(self, obj: Any) -> bool:
        """Return true once per object for the lifetime of this tracker."""

        key = object_key(obj)
        if key in self._seen:
            return False
        self._seen.add(key)
        if key[0] == "direct":
            self._direct_objects.append(obj)
        return True
