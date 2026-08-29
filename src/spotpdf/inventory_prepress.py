"""Inventory exact colorant-name dependencies in supported prepress structures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pikepdf

from .colors import pdf_name
from .inventory_values import base_role, indexed_name_array, name_or_string, path_name
from .model import (
    ColorantRole,
    NameDependency,
    NameDependencyKind,
    PdfObjectIdentity,
)

IdentityFactory = Callable[[Any, str], PdfObjectIdentity]
RelativeIdentityFactory = Callable[
    [Any, PdfObjectIdentity, str],
    PdfObjectIdentity,
]


@dataclass(frozen=True)
class PrepressResult:
    """Dependencies and declarations found in one prepress structure."""

    dependencies: tuple[NameDependency, ...] = ()
    colorants: tuple[tuple[str, ColorantRole, str], ...] = ()


def inspect_separation_info(
    page: Any,
    page_label: str,
    identity_for: IdentityFactory,
) -> PrepressResult:
    """Inspect the page-level dictionary used by pre-separated PDFs."""

    if not isinstance(page, (pikepdf.Dictionary, pikepdf.Stream)):
        return PrepressResult()
    separation_info = page.get(pikepdf.Name.SeparationInfo, None)
    if not isinstance(separation_info, pikepdf.Dictionary):
        return PrepressResult()
    name = name_or_string(separation_info.get(pikepdf.Name.DeviceColorant, None))
    if name is None:
        return PrepressResult()
    location = f"{page_label} /SeparationInfo /DeviceColorant"
    owner = identity_for(separation_info, f"{page_label} /SeparationInfo")
    dependency = NameDependency(
        name=name,
        kind=NameDependencyKind.SEPARATION_INFO,
        owner=owner,
        location=location,
    )
    return PrepressResult(
        dependencies=(dependency,),
        colorants=((name, base_role(name), location),),
    )


def inspect_annotation(
    value: Any,
    locations: tuple[str, ...],
    identity_for: IdentityFactory,
    relative_identity: RelativeIdentityFactory,
) -> tuple[NameDependency, ...]:
    """Inspect normal appearances of PrinterMark and TrapNet annotations."""

    if not isinstance(value, pikepdf.Dictionary):
        return ()
    subtype = value.get(pikepdf.Name.Subtype, None)
    if subtype not in {pikepdf.Name.PrinterMark, pikepdf.Name.TrapNet}:
        return ()
    dependencies: list[NameDependency] = []
    for appearance, appearance_locations in _normal_appearances(value, locations):
        if subtype == pikepdf.Name.PrinterMark:
            dependencies.extend(
                _printer_mark_dependencies(
                    appearance,
                    appearance_locations,
                    identity_for,
                    relative_identity,
                )
            )
        else:
            dependencies.extend(
                _trap_network_dependencies(
                    appearance,
                    appearance_locations,
                    identity_for,
                    relative_identity,
                )
            )
    return tuple(dependencies)


def _normal_appearances(
    annotation: pikepdf.Dictionary,
    locations: tuple[str, ...],
) -> tuple[tuple[pikepdf.Stream, tuple[str, ...]], ...]:
    appearances = annotation.get(pikepdf.Name.AP, None)
    if not isinstance(appearances, pikepdf.Dictionary):
        return ()
    normal = appearances.get(pikepdf.Name.N, None)
    normal_locations = tuple(f"{location} /AP /N" for location in locations)
    if isinstance(normal, pikepdf.Stream):
        return ((normal, normal_locations),)
    if not isinstance(normal, pikepdf.Dictionary):
        return ()
    return tuple(
        (
            appearance,
            tuple(f"{location} {path_name(state)}" for location in normal_locations),
        )
        for state, appearance in normal.items()
        if isinstance(appearance, pikepdf.Stream)
    )


def _printer_mark_dependencies(
    appearance: pikepdf.Stream,
    locations: tuple[str, ...],
    identity_for: IdentityFactory,
    relative_identity: RelativeIdentityFactory,
) -> tuple[NameDependency, ...]:
    if appearance.get(pikepdf.Name.Subtype, None) != pikepdf.Name.Form:
        return ()
    colorants = appearance.get(pikepdf.Name.Colorants, None)
    if not isinstance(colorants, pikepdf.Dictionary):
        return ()
    form_owner = identity_for(appearance, min(locations))
    owner = relative_identity(colorants, form_owner, " /Colorants")
    return tuple(
        NameDependency(
            name=pdf_name(key),
            kind=NameDependencyKind.PRINTER_MARK_COLORANT,
            owner=owner,
            location=f"{location} /Colorants {path_name(key)}",
        )
        for location in locations
        for key in colorants
    )


def _trap_network_dependencies(
    appearance: pikepdf.Stream,
    locations: tuple[str, ...],
    identity_for: IdentityFactory,
    relative_identity: RelativeIdentityFactory,
) -> tuple[NameDependency, ...]:
    names = appearance.get(pikepdf.Name.SeparationColorNames, None)
    if not isinstance(names, pikepdf.Array):
        return ()
    appearance_owner = identity_for(appearance, min(locations))
    owner = relative_identity(names, appearance_owner, " /SeparationColorNames")
    return tuple(
        NameDependency(
            name=name,
            kind=NameDependencyKind.TRAP_NETWORK_COLORANT,
            owner=owner,
            location=f"{location} /SeparationColorNames[{index}]",
        )
        for location in locations
        for index, name in indexed_name_array(names)
    )
