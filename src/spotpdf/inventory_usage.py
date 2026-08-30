"""Internal result and counter models for read-only content inventory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ColorantUsage:
    """Removal-oriented paint counters for one inventoried colorant."""

    pages: set[int] = field(default_factory=set)
    text_show_operations: int = 0
    fills: int = 0
    strokes: int = 0

    @property
    def paint_operations(self) -> int:
        return self.text_show_operations + self.fills + self.strokes


@dataclass
class InspectionMetrics:
    """Deterministic work counters used by the inventory benchmark."""

    resource_contexts_scanned: int = 0
    page_streams_parsed: int = 0
    form_streams_parsed: int = 0
    instructions_visited: int = 0

    @property
    def streams_parsed(self) -> int:
        return self.page_streams_parsed + self.form_streams_parsed


@dataclass
class ContentInventory:
    """Per-colorant usage, first unsupported contexts, and scan metrics."""

    usage: dict[str, ColorantUsage]
    unsupported: dict[str, str] = field(default_factory=dict)
    metrics: InspectionMetrics = field(default_factory=InspectionMetrics)


@dataclass
class TextSummary:
    """Transactional per-colorant counters for one PDF text object."""

    target_only: int = 0
    retained: int = 0
    text_shows: int = 0
    fills: int = 0
    strokes: int = 0


@dataclass(frozen=True)
class FormScan:
    """Reusable signature and affected colorants for one parsed Form."""

    resource_identity: tuple[Any, ...]
    nonstroking: frozenset[str]
    stroking: frozenset[str]
    text_render_mode: int
    changed: frozenset[str]
