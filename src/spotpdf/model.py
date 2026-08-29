"""Shared domain models and errors for spotpdf."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("spotpdf")
except PackageNotFoundError:  # pragma: no cover - direct source-tree fallback
    __version__ = "0+unknown"


class SpotPdfError(Exception):
    """Base class for user-facing processing failures."""


class InvalidPdfError(SpotPdfError):
    """Raised when an input is unsafe or cannot be parsed strictly."""


class UnsupportedSpotUseError(SpotPdfError):
    """Raised when removing a target would require unsupported semantics."""


class SpotKind(StrEnum):
    SEPARATION = "Separation"
    DEVICEN = "DeviceN"
    SPECIAL = "Special"


@dataclass(frozen=True)
class ColorSpaceInfo:
    """Resolved information for a PDF color-space resource."""

    kind: SpotKind | None = None
    colorants: tuple[str, ...] = ()
    resource_name: str | None = None
    resolved: bool = True

    def contains(self, spot: str) -> bool:
        return spot in self.colorants

    def contains_any(self, spots: frozenset[str]) -> bool:
        """Return whether this color space contains any selected colorant."""

        return not spots.isdisjoint(self.colorants)


@dataclass
class SpotSummary:
    """Declaration and paint-use summary for one colorant."""

    name: str
    kinds: set[SpotKind] = field(default_factory=set)
    pages: set[int] = field(default_factory=set)
    paint_operations: int = 0
    contexts: set[str] = field(default_factory=set)


@dataclass
class InspectionReport:
    """Reachable spot-color inventory for a PDF."""

    spots: dict[str, SpotSummary] = field(default_factory=dict)

    def get_or_create(self, name: str) -> SpotSummary:
        return self.spots.setdefault(name, SpotSummary(name=name))


@dataclass
class RemovalStats:
    """Counts of content changed by a removal operation."""

    pages_changed: set[int] = field(default_factory=set)
    forms_changed: int = 0
    text_blocks: int = 0
    text_show_operations: int = 0
    fills_removed: int = 0
    strokes_removed: int = 0
    resources_removed: int = 0

    @property
    def changed(self) -> bool:
        return any(
            (
                self.pages_changed,
                self.forms_changed,
                self.text_show_operations,
                self.fills_removed,
                self.strokes_removed,
                self.resources_removed,
            )
        )


@dataclass(frozen=True)
class BatchRemovalResult:
    """Names and aggregate counters from one atomic multi-spot rewrite."""

    spots: tuple[str, ...]
    stats: RemovalStats
