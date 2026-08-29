"""Role-aware inventory of named PDF color spaces and prepress dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pikepdf

from . import inventory_values as values
from .colors import PROCESS_COLORANTS, pdf_name
from .inventory_graph import walk_reachable
from .inventory_prepress import inspect_annotation, inspect_separation_info
from .model import (
    ColorantComponent,
    ColorantRole,
    ColorSpaceDefinition,
    InspectionReport,
    NameDependency,
    NameDependencyKind,
    PdfObjectIdentity,
    SpotKind,
)
from .objects import ObjectKey, object_key


@dataclass
class _DefinitionDraft:
    """Mutable assembly state converted to a public immutable definition."""

    identity: PdfObjectIdentity
    kind: SpotKind
    component_names: tuple[str, ...]
    component_roles: dict[str, ColorantRole]
    spot_lookup_components: set[str] = field(default_factory=set)
    locations: set[str] = field(default_factory=set)
    subtype: str | None = None
    process_color_space: str | None = None
    process_components: tuple[str, ...] = ()
    individual_colorants: tuple[str, ...] = ()


class _InventoryBuilder:
    """Build one semantic report from the cached reachable object graph."""

    def __init__(self, pdf: pikepdf.Pdf) -> None:
        self.pdf = pdf
        self.identities: dict[ObjectKey, PdfObjectIdentity] = {}
        self.definitions: dict[tuple[Any, ...], _DefinitionDraft] = {}
        self.role_overrides: dict[ObjectKey, dict[str, ColorantRole]] = {}
        self.location_role_overrides: dict[str, dict[str, ColorantRole]] = {}
        self.path_definition_keys: dict[str, tuple[Any, ...]] = {}
        self.dependencies: set[NameDependency] = set()
        self.preseparated_colorants: set[tuple[str, ColorantRole, str]] = set()

    def build(self) -> InspectionReport:
        self._walk()
        report = InspectionReport(dependencies=self._sorted_dependencies())
        for draft in sorted(self.definitions.values(), key=lambda item: item.identity.label):
            definition = self._finalize_definition(draft)
            report.definitions[definition.object_id] = definition
            for component in definition.components:
                summary = report.get_or_create_colorant(component.name)
                summary.kinds.add(definition.kind)
                summary.roles.add(component.role)
                summary.definition_ids.add(definition.object_id)
                summary.locations.update(definition.locations)
                if component.name in draft.spot_lookup_components:
                    report.include_spot(component.name)

        for name, role, location in sorted(self.preseparated_colorants):
            summary = report.get_or_create_colorant(name)
            summary.kinds.add(SpotKind.SEPARATION_INFO)
            summary.roles.add(role)
            summary.locations.add(location)
            if role is not ColorantRole.PROCESS:
                report.include_spot(name)
        return report

    def _walk(self) -> None:
        for visit in walk_reachable(self.pdf):
            current = visit.value
            locations = visit.locations
            self._record_color_space(current, locations)
            if visit.page_label is not None:
                prepress = inspect_separation_info(
                    current,
                    visit.page_label,
                    self._identity_for,
                )
                self.dependencies.update(prepress.dependencies)
                self.preseparated_colorants.update(prepress.colorants)
            self.dependencies.update(
                inspect_annotation(
                    current,
                    locations,
                    self._identity_for,
                    self._relative_identity,
                )
            )

    def _record_color_space(self, value: Any, locations: tuple[str, ...]) -> None:
        if not isinstance(value, pikepdf.Array) or not value:
            return
        family = pdf_name(value[0])
        if family == "Separation" and len(value) >= 2:
            self._record_separation(value, locations)
        elif family == "DeviceN" and len(value) >= 2 and isinstance(value[1], pikepdf.Array):
            self._record_devicen(value, locations)

    def _record_separation(self, value: pikepdf.Array, locations: tuple[str, ...]) -> None:
        mapped_locations = {
            location: self.path_definition_keys[location]
            for location in locations
            if location in self.path_definition_keys
        }
        if mapped_locations:
            for location, definition_key in mapped_locations.items():
                self.definitions[definition_key].locations.add(location)
            locations = tuple(
                location for location in locations if location not in mapped_locations
            )
            if not locations:
                return
        name = values.name_value(value[1])
        if name is None:
            return
        key = object_key(value)
        role = self.role_overrides.get(key, {}).get(name, values.base_role(name))
        for location in locations:
            location_role = self.location_role_overrides.get(location, {}).get(name)
            if location_role is not None:
                role = values.dominant_role(role, location_role)
        has_standalone_location = any(
            name not in self.location_role_overrides.get(location, {}) for location in locations
        )
        spot_lookup_components = (
            {name} if role is not ColorantRole.PROCESS or has_standalone_location else set()
        )
        draft = self._get_or_create_draft(
            value,
            locations,
            kind=SpotKind.SEPARATION,
            component_names=(name,),
            component_roles={name: role},
            spot_lookup_components=spot_lookup_components,
        )
        draft.component_roles[name] = values.dominant_role(draft.component_roles[name], role)

    def _record_devicen(self, value: pikepdf.Array, locations: tuple[str, ...]) -> None:
        names = values.name_array(value[1])
        attributes = value[4] if len(value) >= 5 else None
        subtype: str | None = None
        process_color_space: str | None = None
        process_color_space_value: Any = None
        process_components: tuple[str, ...] = ()
        individual_colorants: tuple[str, ...] = ()

        if isinstance(attributes, pikepdf.Dictionary):
            subtype_value = attributes.get(pikepdf.Name.Subtype, None)
            if isinstance(subtype_value, pikepdf.Name):
                subtype = pdf_name(subtype_value)
            process = attributes.get(pikepdf.Name.Process, None)
            if isinstance(process, pikepdf.Dictionary):
                process_color_space_value = process.get(pikepdf.Name.ColorSpace, None)
                process_color_space = values.color_space_name(process_color_space_value)
                process_components = values.name_array(process.get(pikepdf.Name.Components, None))
            colorants = attributes.get(pikepdf.Name.Colorants, None)
            if isinstance(colorants, pikepdf.Dictionary):
                individual_colorants = tuple(
                    sorted((pdf_name(name) for name in colorants), key=str.casefold)
                )

        process_names = frozenset(process_components)
        if subtype != "NChannel" or values.is_cmyk_process_color_space(process_color_space_value):
            process_names |= PROCESS_COLORANTS
        roles = {name: values.devicen_role(name, process_names) for name in names}
        spot_lookup_components = {
            name for name, role in roles.items() if role is not ColorantRole.PROCESS
        }
        draft = self._get_or_create_draft(
            value,
            locations,
            kind=SpotKind.DEVICEN,
            component_names=names,
            component_roles=roles,
            spot_lookup_components=spot_lookup_components,
            subtype=subtype,
            process_color_space=process_color_space,
            process_components=process_components,
            individual_colorants=individual_colorants,
        )
        for name, role in roles.items():
            current = draft.component_roles.get(name, role)
            draft.component_roles[name] = values.dominant_role(current, role)

        if not isinstance(attributes, pikepdf.Dictionary):
            return
        self._record_devicen_dependencies(
            attributes,
            locations,
            draft.identity,
            process_names,
        )

    def _record_devicen_dependencies(
        self,
        attributes: pikepdf.Dictionary,
        locations: tuple[str, ...],
        definition_owner: PdfObjectIdentity,
        process_names: frozenset[str],
    ) -> None:
        definition_id = definition_owner.label
        attributes_owner = self._relative_identity(
            attributes,
            definition_owner,
            "[4]",
        )
        process = attributes.get(pikepdf.Name.Process, None)
        if isinstance(process, pikepdf.Dictionary):
            owner = self._relative_identity(
                process,
                attributes_owner,
                " /Process",
            )
            components = process.get(pikepdf.Name.Components, None)
            for location in locations:
                for index, name in values.indexed_name_array(components):
                    self._add_dependency(
                        name,
                        NameDependencyKind.PROCESS_COMPONENT,
                        owner,
                        f"{location}[4] /Process /Components[{index}]",
                        definition_id,
                    )

        colorants = attributes.get(pikepdf.Name.Colorants, None)
        if isinstance(colorants, pikepdf.Dictionary):
            owner = self._relative_identity(
                colorants,
                attributes_owner,
                " /Colorants",
            )
            for key, nested in colorants.items():
                name = pdf_name(key)
                key_path = values.path_name(key)
                nested_name = values.separation_name(nested)
                for location in locations:
                    nested_location = f"{location}[4] /Colorants {key_path}"
                    self._add_dependency(
                        name,
                        NameDependencyKind.INDIVIDUAL_COLORANT,
                        owner,
                        nested_location,
                        definition_id,
                    )
                if nested_name is not None:
                    self._record_nested_separation(
                        nested,
                        key_name=name,
                        key_path=key_path,
                        nested_name=nested_name,
                        locations=locations,
                        colorants_owner=owner,
                        process_names=process_names,
                    )

        mixing_hints = attributes.get(pikepdf.Name.MixingHints, None)
        if not isinstance(mixing_hints, pikepdf.Dictionary):
            return
        mixing_owner = self._relative_identity(
            mixing_hints,
            attributes_owner,
            " /MixingHints",
        )
        solidities = mixing_hints.get(pikepdf.Name.Solidities, None)
        self._record_dictionary_keys(
            solidities,
            NameDependencyKind.SOLIDITY,
            self._relative_identity(
                solidities,
                mixing_owner,
                " /Solidities",
            ),
            tuple(f"{location}[4] /MixingHints /Solidities" for location in locations),
            definition_id,
        )
        dot_gain = mixing_hints.get(pikepdf.Name.DotGain, None)
        self._record_dictionary_keys(
            dot_gain,
            NameDependencyKind.DOT_GAIN,
            self._relative_identity(
                dot_gain,
                mixing_owner,
                " /DotGain",
            ),
            tuple(f"{location}[4] /MixingHints /DotGain" for location in locations),
            definition_id,
        )
        printing_order = mixing_hints.get(pikepdf.Name.PrintingOrder, None)
        if isinstance(printing_order, pikepdf.Array):
            owner = self._relative_identity(
                printing_order,
                mixing_owner,
                " /PrintingOrder",
            )
            for location in locations:
                for index, name in values.indexed_name_array(printing_order):
                    self._add_dependency(
                        name,
                        NameDependencyKind.PRINTING_ORDER,
                        owner,
                        f"{location}[4] /MixingHints /PrintingOrder[{index}]",
                        definition_id,
                    )

    def _record_dictionary_keys(
        self,
        value: Any,
        kind: NameDependencyKind,
        owner: PdfObjectIdentity,
        locations: tuple[str, ...],
        definition_id: str,
    ) -> None:
        if not isinstance(value, pikepdf.Dictionary):
            return
        for key in value:
            name = pdf_name(key)
            if name == "Default":
                continue
            for location in locations:
                self._add_dependency(
                    name,
                    kind,
                    owner,
                    f"{location} {values.path_name(key)}",
                    definition_id,
                )

    def _record_nested_separation(
        self,
        value: Any,
        *,
        key_name: str,
        key_path: str,
        nested_name: str,
        locations: tuple[str, ...],
        colorants_owner: PdfObjectIdentity,
        process_names: frozenset[str],
    ) -> None:
        nested_locations = tuple(f"{location}[4] /Colorants {key_path}" for location in locations)
        role = values.devicen_role(nested_name, process_names)
        raw_key = object_key(value)
        if raw_key[0] == "indirect":
            definition_key: tuple[Any, ...] = raw_key
            identity = self._identity_for(value, min(nested_locations))
        else:
            definition_key = (
                "anchored",
                colorants_owner.path_anchor,
                key_name,
            )
            identity = PdfObjectIdentity(
                direct_location=f"{colorants_owner.path_anchor} {key_path}"
            )
        for location in nested_locations:
            self.path_definition_keys[location] = definition_key
            self._register_role_override(value, nested_name, role, location)
        draft = self._get_or_create_draft(
            value,
            nested_locations,
            kind=SpotKind.SEPARATION,
            component_names=(nested_name,),
            component_roles={nested_name: role},
            spot_lookup_components=({nested_name} if role is not ColorantRole.PROCESS else set()),
            definition_key=definition_key,
            identity=identity,
        )
        draft.component_roles[nested_name] = values.dominant_role(
            draft.component_roles[nested_name],
            role,
        )

    def _get_or_create_draft(
        self,
        value: Any,
        locations: tuple[str, ...],
        *,
        kind: SpotKind,
        component_names: tuple[str, ...],
        component_roles: dict[str, ColorantRole],
        spot_lookup_components: set[str],
        subtype: str | None = None,
        process_color_space: str | None = None,
        process_components: tuple[str, ...] = (),
        individual_colorants: tuple[str, ...] = (),
        definition_key: tuple[Any, ...] | None = None,
        identity: PdfObjectIdentity | None = None,
    ) -> _DefinitionDraft:
        key = definition_key or object_key(value)
        draft = self.definitions.get(key)
        if draft is None:
            draft = _DefinitionDraft(
                identity=identity or self._identity_for(value, min(locations)),
                kind=kind,
                component_names=component_names,
                component_roles=component_roles.copy(),
                spot_lookup_components=spot_lookup_components.copy(),
                subtype=subtype,
                process_color_space=process_color_space,
                process_components=process_components,
                individual_colorants=individual_colorants,
            )
            self.definitions[key] = draft
        draft.locations.update(locations)
        draft.spot_lookup_components.update(spot_lookup_components)
        return draft

    def _register_role_override(
        self,
        value: Any,
        name: str,
        role: ColorantRole,
        location: str,
    ) -> None:
        key = object_key(value)
        overrides = self.role_overrides.setdefault(key, {})
        previous = overrides.get(name, role)
        overrides[name] = values.dominant_role(previous, role)
        location_overrides = self.location_role_overrides.setdefault(location, {})
        location_previous = location_overrides.get(name, role)
        location_overrides[name] = values.dominant_role(location_previous, role)
        draft = self.definitions.get(key)
        if draft is not None and name in draft.component_roles:
            draft.component_roles[name] = values.dominant_role(
                draft.component_roles[name],
                role,
            )

    def _identity_for(self, value: Any, location: str) -> PdfObjectIdentity:
        key = object_key(value)
        identity = self.identities.get(key)
        if identity is not None:
            return identity
        if key[0] == "indirect":
            identity = PdfObjectIdentity(object_number=key[1], generation=key[2])
        else:
            identity = PdfObjectIdentity(direct_location=location)
        self.identities[key] = identity
        return identity

    def _relative_identity(
        self,
        value: Any,
        definition_owner: PdfObjectIdentity,
        relative_location: str,
    ) -> PdfObjectIdentity:
        if isinstance(value, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            key = object_key(value)
            if key[0] == "indirect":
                return self._identity_for(value, relative_location)
        return PdfObjectIdentity(
            direct_location=f"{definition_owner.path_anchor}{relative_location}"
        )

    def _add_dependency(
        self,
        name: str,
        kind: NameDependencyKind,
        owner: PdfObjectIdentity,
        location: str,
        definition_id: str | None = None,
    ) -> None:
        self.dependencies.add(
            NameDependency(
                name=name,
                kind=kind,
                owner=owner,
                location=location,
                definition_id=definition_id,
            )
        )

    def _sorted_dependencies(self) -> tuple[NameDependency, ...]:
        return tuple(
            sorted(
                self.dependencies,
                key=lambda item: (
                    item.location,
                    item.kind.value,
                    item.name.casefold(),
                    item.name,
                ),
            )
        )

    @staticmethod
    def _finalize_definition(draft: _DefinitionDraft) -> ColorSpaceDefinition:
        components = tuple(
            ColorantComponent(name=name, role=draft.component_roles[name])
            for name in draft.component_names
        )
        return ColorSpaceDefinition(
            identity=draft.identity,
            kind=draft.kind,
            components=components,
            locations=tuple(sorted(draft.locations)),
            subtype=draft.subtype,
            process_color_space=draft.process_color_space,
            process_components=draft.process_components,
            individual_colorants=draft.individual_colorants,
        )


def discover_spot_declarations(pdf: pikepdf.Pdf) -> InspectionReport:
    """Return a role-aware inventory of every reachable named color space."""

    return _InventoryBuilder(pdf).build()
