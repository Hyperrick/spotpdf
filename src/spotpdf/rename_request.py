"""User-request and inventory validation for exact spot-plate renames."""

from __future__ import annotations

from .colors import PROCESS_COLORANTS, SPECIAL_COLORANTS
from .diagnostics import Finding
from .model import (
    ColorantRole,
    InspectionReport,
    InvalidPdfError,
    NameDependencyKind,
    SpotKind,
    UnsupportedSpotUseError,
)

SUPPORTED_RENAME_DEPENDENCIES = frozenset(
    {
        NameDependencyKind.INDIVIDUAL_COLORANT,
        NameDependencyKind.SOLIDITY,
        NameDependencyKind.DOT_GAIN,
        NameDependencyKind.PRINTING_ORDER,
        NameDependencyKind.SEPARATION_INFO,
        NameDependencyKind.PRINTER_MARK_COLORANT,
    }
)


def validate_rename_request(
    report: InspectionReport,
    source: str,
    destination: str,
) -> None:
    """Require one unambiguous Separation source and one unused safe target."""

    if source == destination:
        raise InvalidPdfError("source and destination spot names must be different")
    if source in SPECIAL_COLORANTS or destination in SPECIAL_COLORANTS:
        raise InvalidPdfError("reserved /All and /None separation names cannot be renamed")
    if source in PROCESS_COLORANTS or destination in PROCESS_COLORANTS:
        raise InvalidPdfError("canonical process-color names cannot be renamed or created")

    summary = report.colorants.get(source)
    if summary is None:
        raise InvalidPdfError(f"source spot color is absent: {source!r}")
    if summary.roles != {ColorantRole.SPOT}:
        roles = ", ".join(sorted(role.value for role in summary.roles)) or "unknown"
        raise InvalidPdfError(
            f"source colorant {source!r} is not an unambiguous spot (roles: {roles})"
        )
    if not any(
        definition.kind is SpotKind.SEPARATION
        and any(
            component.name == source and component.role is ColorantRole.SPOT
            for component in definition.components
        )
        for definition in report.definitions.values()
    ):
        raise InvalidPdfError(f"source colorant {source!r} has no reachable Separation definition")

    dependency_names = {dependency.name for dependency in report.dependencies}
    if destination in report.colorants or destination in dependency_names:
        raise InvalidPdfError(f"destination colorant already exists: {destination!r}")
    blocked = {
        dependency.kind
        for dependency in report.dependencies
        if dependency.name == source and dependency.kind not in SUPPORTED_RENAME_DEPENDENCIES
    }
    if blocked:
        kinds = ", ".join(sorted(kind.value for kind in blocked))
        raise UnsupportedSpotUseError(
            f"source colorant has unsupported exact-name dependencies: {kinds}",
            findings=[
                Finding(
                    "unsupported_spot_use",
                    "Unsupported exact-name dependency: " + d.kind.value,
                    [source],
                    d.owner.label,
                    d.location,
                )
                for d in report.dependencies
                if d.name == source and d.kind in blocked
            ],
        )


__all__ = ["SUPPORTED_RENAME_DEPENDENCIES", "validate_rename_request"]
