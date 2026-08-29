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
    """Raised when changing a target would require unsupported semantics."""


class SpotKind(StrEnum):
    SEPARATION = "Separation"
    DEVICEN = "DeviceN"
    SEPARATION_INFO = "SeparationInfo"
    SPECIAL = "Special"


class ColorantRole(StrEnum):
    """Semantic role of a named PDF colorant."""

    SPOT = "spot"
    PROCESS = "process"
    ALL = "all"
    NONE = "none"


class NameDependencyKind(StrEnum):
    """PDF entries whose values depend on an exact colorant name."""

    PROCESS_COMPONENT = "Process/Components"
    INDIVIDUAL_COLORANT = "Colorants"
    SOLIDITY = "MixingHints/Solidities"
    DOT_GAIN = "MixingHints/DotGain"
    PRINTING_ORDER = "MixingHints/PrintingOrder"
    SEPARATION_INFO = "SeparationInfo/DeviceColorant"
    PRINTER_MARK_COLORANT = "PrinterMark/Colorants"
    TRAP_NETWORK_COLORANT = "TrapNet/SeparationColorNames"


@dataclass(frozen=True)
class PdfObjectIdentity:
    """Serializable identity for an indirect object or one stable direct location."""

    object_number: int | None = None
    generation: int | None = None
    direct_location: str | None = None

    @property
    def label(self) -> str:
        if self.object_number is not None and self.generation is not None:
            return f"{self.object_number} {self.generation} R"
        return f"direct at {self.direct_location or 'unknown location'}"

    @property
    def path_anchor(self) -> str:
        """Return the identity without the human-facing direct-object prefix."""

        if self.object_number is not None and self.generation is not None:
            return f"{self.object_number} {self.generation} R"
        return self.direct_location or "unknown location"


@dataclass(frozen=True)
class ColorantComponent:
    """One named component and its semantic role in a color-space definition."""

    name: str
    role: ColorantRole


@dataclass(frozen=True)
class ColorSpaceDefinition:
    """One reachable Separation or DeviceN definition."""

    identity: PdfObjectIdentity
    kind: SpotKind
    components: tuple[ColorantComponent, ...]
    locations: tuple[str, ...]
    subtype: str | None = None
    process_color_space: str | None = None
    process_components: tuple[str, ...] = ()
    individual_colorants: tuple[str, ...] = ()

    @property
    def object_id(self) -> str:
        return self.identity.label

    @property
    def effective_subtype(self) -> str | None:
        """Return the explicit subtype or the DeviceN default from the PDF spec."""

        if self.kind is SpotKind.DEVICEN:
            return self.subtype or "DeviceN"
        return self.subtype


@dataclass(frozen=True)
class NameDependency:
    """One exact-name dependency that future mutations must account for."""

    name: str
    kind: NameDependencyKind
    owner: PdfObjectIdentity
    location: str
    definition_id: str | None = None


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
    roles: set[ColorantRole] = field(default_factory=set)
    definition_ids: set[str] = field(default_factory=set)
    locations: set[str] = field(default_factory=set)
    pages: set[int] = field(default_factory=set)
    paint_operations: int = 0
    contexts: set[str] = field(default_factory=set)


@dataclass
class InspectionReport:
    """Reachable named-colorant and color-space inventory for a PDF."""

    colorants: dict[str, SpotSummary] = field(default_factory=dict)
    spots: dict[str, SpotSummary] = field(default_factory=dict)
    definitions: dict[str, ColorSpaceDefinition] = field(default_factory=dict)
    dependencies: tuple[NameDependency, ...] = ()

    def get_or_create(self, name: str) -> SpotSummary:
        """Create a legacy spot entry and its all-colorant counterpart."""

        summary = self.get_or_create_colorant(name)
        self.spots.setdefault(name, summary)
        return summary

    def get_or_create_colorant(self, name: str) -> SpotSummary:
        """Return a summary without assuming that the colorant is a spot."""

        return self.colorants.setdefault(name, SpotSummary(name=name))

    def include_spot(self, name: str) -> None:
        """Expose an inventoried colorant through legacy spot/check semantics."""

        self.spots[name] = self.colorants[name]

    @property
    def spot_names(self) -> frozenset[str]:
        """Return names classified only as spot colorants, not process or special."""

        return frozenset(
            name
            for name, summary in self.spots.items()
            if ColorantRole.SPOT in summary.roles
            and ColorantRole.PROCESS not in summary.roles
            and ColorantRole.ALL not in summary.roles
            and ColorantRole.NONE not in summary.roles
        )


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


@dataclass(frozen=True)
class RenameResult:
    """Summary of one atomic spot-plate rename."""

    source: str
    destination: str
    definitions_renamed: int
    references_renamed: int
