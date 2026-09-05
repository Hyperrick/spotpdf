"""Semantic planning for atomic spot-color plate renames."""

from __future__ import annotations

from typing import Any

import pikepdf

from .colors import pdf_name as decode_pdf_name
from .inventory_graph import walk_reachable
from .inventory_values import name_or_string, name_value, path_name
from .model import (
    InspectionReport,
    InvalidPdfError,
    NameDependencyKind,
    SpotKind,
    UnsupportedSpotUseError,
)
from .rename_hazards import (
    devicen_target_mentions,
    inspect_hazards,
    is_matching_separation,
    mixing_hints_contain,
    name_array_contains,
    name_field_mentions,
    normal_appearance_forms,
    subtree_mentions,
)
from .rename_pages import inspect_rename_page
from .rename_request import SUPPORTED_RENAME_DEPENDENCIES, validate_rename_request
from .rename_slots import (
    MutableNameSlot,
    RenamePlan,
    SeparationInvariant,
    SlotMode,
    container_key,
    pdf_name,
)
from .rename_structures import (
    ProcessStructure,
    validate_colorants_dictionary,
    validate_mixing_hints,
    validate_process_dictionary,
)


class _PlanBuilder:
    """Discover and validate rename slots without mutating the open PDF."""

    def __init__(
        self,
        pdf: pikepdf.Pdf,
        report: InspectionReport,
        source: str,
        destination: str,
    ) -> None:
        self.pdf = pdf
        self.report = report
        self.source = source
        self.destination = destination
        self.source_name = pdf_name(source)
        self.destination_name = pdf_name(destination)
        self.slots: dict[tuple[Any, ...], MutableNameSlot] = {}
        self.invariants: dict[tuple[Any, ...], SeparationInvariant] = {}
        self.mapped_definitions: set[tuple[Any, ...]] = set()
        self.mapped_dependencies: set[tuple[Any, ...]] = set()
        self.supported_colorant_locations: set[str] = set()
        self.colorant_candidates: set[str] = set()

    def build(self) -> RenamePlan:
        validate_rename_request(self.report, self.source, self.destination)
        for visit in walk_reachable(self.pdf):
            self.inspect_visit(visit)
        self._validate_coverage()
        plan = RenamePlan(
            source=self.source,
            destination=self.destination,
            _slots=tuple(sorted(self.slots.values(), key=lambda slot: slot.label)),
            _invariants=tuple(
                sorted(self.invariants.values(), key=lambda invariant: invariant.location)
            ),
            _definition_count=len(self.mapped_definitions),
            _reference_count=len(self.mapped_dependencies),
        )
        plan.verify_invariants()
        return plan

    def inspect_visit(self, visit) -> None:
        """Inspect one original resource without applying any planned changes."""
        value = visit.value
        inspect_hazards(value, visit.locations, self.source, self.destination)
        self._inspect_color_space(value, visit.locations)
        if visit.page_label is not None:
            inspect_rename_page(self, value, visit.page_label)
        self._inspect_annotation(value, visit.locations)
        self._collect_colorants_candidate(value, visit.locations)

    def _inspect_color_space(self, value: Any, locations: tuple[str, ...]) -> None:
        if not isinstance(value, pikepdf.Array) or not value:
            return
        family = name_value(value[0])
        if family == "Separation":
            self._inspect_separation(value, locations)
        elif family == "DeviceN":
            self._inspect_devicen(value, locations)

    def _inspect_separation(self, value: pikepdf.Array, locations: tuple[str, ...]) -> None:
        if len(value) < 2:
            return
        raw_name = value[1]
        decoded_name = name_or_string(raw_name)
        if decoded_name in {self.source, self.destination} and not isinstance(
            raw_name, pikepdf.Name
        ):
            raise UnsupportedSpotUseError(
                f"{min(locations)}: target occurs in a malformed Separation array",
                location=min(locations),
            )
        if name_value(raw_name) != self.source:
            return
        location = min(locations)
        if len(value) != 4:
            raise UnsupportedSpotUseError(
                f"{location}: malformed Separation array cannot be renamed safely",
                location=location,
            )
        if subtree_mentions(value[2], frozenset({self.source})) or subtree_mentions(
            value[3], frozenset({self.source})
        ):
            raise UnsupportedSpotUseError(
                f"{location}: target is nested in its alternate space or tint transform",
                location=location,
            )
        identity = self._definition_identity(SpotKind.SEPARATION, locations)
        self.mapped_definitions.add(identity)
        self.invariants.setdefault(identity, SeparationInvariant.capture(value, location))
        self._add_slot(
            value,
            1,
            SlotMode.ARRAY_NAME,
            locations,
            definition=True,
            physical_identity=identity,
        )

    def _inspect_devicen(self, value: pikepdf.Array, locations: tuple[str, ...]) -> None:
        components = value[1] if len(value) >= 2 else None
        component_names = (
            [name_value(item) for item in components]
            if isinstance(components, pikepdf.Array)
            else []
        )
        source_indices = [
            index for index, name in enumerate(component_names) if name == self.source
        ]
        attributes_mention = self._devicen_attributes_mention_source(value)
        if not source_indices and not attributes_mention:
            if devicen_target_mentions(
                value,
                frozenset({self.source, self.destination}),
            ):
                raise UnsupportedSpotUseError(
                    f"{min(locations)}: target occurs in a malformed DeviceN array",
                    location=min(locations),
                )
            return
        location = min(locations)
        if (
            len(value) not in {4, 5}
            or not isinstance(components, pikepdf.Array)
            or any(name is None for name in component_names)
        ):
            raise UnsupportedSpotUseError(f"{location}: malformed DeviceN array", location=location)
        if not component_names or "All" in component_names:
            raise UnsupportedSpotUseError(
                f"{location}: invalid DeviceN component names", location=location
            )
        repeated_names = [name for name in component_names if name != "None"]
        if len(set(repeated_names)) != len(repeated_names):
            raise UnsupportedSpotUseError(
                f"{location}: duplicate DeviceN component names", location=location
            )

        if source_indices:
            identity = self._definition_identity(SpotKind.DEVICEN, locations)
            self.mapped_definitions.add(identity)
            component_locations = tuple(f"{item}[1]" for item in locations)
            component_identity = container_key(components, component_locations)
            if component_identity[0] != "indirect":
                component_identity = (*identity, "components")
            for index in source_indices:
                self._add_slot(
                    components,
                    index,
                    SlotMode.ARRAY_NAME,
                    component_locations,
                    definition=True,
                    physical_identity=component_identity,
                )
        if len(value) == 4:
            return
        attributes = value[4]
        if not isinstance(attributes, pikepdf.Dictionary):
            raise UnsupportedSpotUseError(
                f"{location}: malformed DeviceN attributes", location=location
            )
        raw_subtype = attributes.get(pikepdf.Name.Subtype, None)
        subtype = name_value(raw_subtype)
        if raw_subtype is not None and subtype not in {"DeviceN", "NChannel"}:
            raise UnsupportedSpotUseError(
                f"{location}: unsupported DeviceN subtype {subtype!r}", location=location
            )
        if subtype == "NChannel" and "None" in component_names:
            raise UnsupportedSpotUseError(
                f"{location}: /None is not allowed in NChannel component names", location=location
            )
        process = self._inspect_process(attributes, tuple(component_names), location)
        validate_colorants_dictionary(
            attributes.get(pikepdf.Name.Colorants, None),
            tuple(component_names),
            subtype,
            process,
            location,
        )
        has_individual = self._inspect_colorants(attributes, locations)
        if (not source_indices or subtype == "NChannel") and not has_individual:
            raise UnsupportedSpotUseError(
                f"{location}: target-related DeviceN attributes lack a matching "
                "/Colorants Separation",
                location=location,
            )
        self._inspect_mixing_hints(attributes, locations, tuple(component_names))

    def _inspect_process(
        self,
        attributes: pikepdf.Dictionary,
        component_names: tuple[str, ...],
        location: str,
    ) -> ProcessStructure | None:
        process = attributes.get(pikepdf.Name.Process, None)
        structure = validate_process_dictionary(process, component_names, location)
        if structure is None:
            return None
        if self.source in structure.names:
            raise UnsupportedSpotUseError(
                f"{location}: source is an NChannel process component, not a spot",
                location=location,
            )
        return structure

    def _inspect_colorants(
        self,
        attributes: pikepdf.Dictionary,
        locations: tuple[str, ...],
    ) -> bool:
        colorants = attributes.get(pikepdf.Name.Colorants, None)
        if colorants is None:
            return False
        if not isinstance(colorants, pikepdf.Dictionary):
            raise UnsupportedSpotUseError(
                f"{min(locations)}: malformed DeviceN /Colorants dictionary",
                location=min(locations),
            )
        colorant_locations = tuple(f"{item}[4] /Colorants" for item in locations)
        mismatched = [
            key
            for key, nested in colorants.items()
            if is_matching_separation(nested, self.source) and decode_pdf_name(key) != self.source
        ]
        if mismatched:
            raise UnsupportedSpotUseError(
                f"{min(locations)}: /Colorants key and nested Separation name do not match",
                location=min(locations),
            )
        if self.source_name not in colorants:
            return False
        if not is_matching_separation(colorants[self.source_name], self.source):
            raise UnsupportedSpotUseError(
                f"{min(locations)}: /Colorants key and nested Separation name do not match",
                location=min(locations),
            )
        dependency_locations = tuple(
            f"{item} {path_name(self.source_name)}" for item in colorant_locations
        )
        self._add_slot(
            colorants,
            self.source_name,
            SlotMode.DICTIONARY_KEY,
            dependency_locations,
            dependency_kind=NameDependencyKind.INDIVIDUAL_COLORANT,
        )
        self.supported_colorant_locations.update(dependency_locations)
        return True

    def _inspect_mixing_hints(
        self,
        attributes: pikepdf.Dictionary,
        locations: tuple[str, ...],
        component_names: tuple[str, ...],
    ) -> None:
        mixing = attributes.get(pikepdf.Name.MixingHints, None)
        mixing = validate_mixing_hints(mixing, component_names, min(locations))
        if mixing is None:
            return
        base_locations = tuple(f"{item}[4] /MixingHints" for item in locations)
        for key, kind in (
            (pikepdf.Name.Solidities, NameDependencyKind.SOLIDITY),
            (pikepdf.Name.DotGain, NameDependencyKind.DOT_GAIN),
        ):
            dictionary = mixing.get(key, None)
            if dictionary is None:
                continue
            if not isinstance(dictionary, pikepdf.Dictionary):
                raise UnsupportedSpotUseError(
                    f"{min(locations)}: malformed /MixingHints {key} dictionary",
                    location=min(locations),
                )
            if self.source_name not in dictionary:
                continue
            if self.destination_name == pikepdf.Name.Default:
                raise UnsupportedSpotUseError(
                    f"{min(locations)}: /Default has fallback MixingHints semantics",
                    location=min(locations),
                )
            field_locations = tuple(
                f"{item} {key} {path_name(self.source_name)}" for item in base_locations
            )
            self._add_slot(
                dictionary,
                self.source_name,
                SlotMode.DICTIONARY_KEY,
                field_locations,
                dependency_kind=kind,
            )

        order = mixing.get(pikepdf.Name.PrintingOrder, None)
        if order is None:
            return
        if not isinstance(order, pikepdf.Array) or any(name_value(item) is None for item in order):
            raise UnsupportedSpotUseError(
                f"{min(locations)}: malformed /PrintingOrder array", location=min(locations)
            )
        for index, item in enumerate(order):
            if name_value(item) == self.source:
                order_locations = tuple(
                    f"{location} /PrintingOrder[{index}]" for location in base_locations
                )
                self._add_slot(
                    order,
                    index,
                    SlotMode.ARRAY_NAME,
                    order_locations,
                    dependency_kind=NameDependencyKind.PRINTING_ORDER,
                )

    def _inspect_annotation(self, value: Any, locations: tuple[str, ...]) -> None:
        if not isinstance(value, pikepdf.Dictionary):
            return
        subtype = value.get(pikepdf.Name.Subtype, None)
        if subtype == pikepdf.Name.TrapNet:
            if subtree_mentions(value, frozenset({self.source, self.destination})):
                raise UnsupportedSpotUseError(
                    f"{min(locations)}: TrapNet spot dependencies are not supported",
                    location=min(locations),
                )
            return
        if subtype != pikepdf.Name.PrinterMark:
            return
        appearances = value.get(pikepdf.Name.AP, None)
        if not isinstance(appearances, pikepdf.Dictionary):
            return
        normal = appearances.get(pikepdf.Name.N, None)
        for form, form_locations in normal_appearance_forms(normal, locations):
            colorants = form.get(pikepdf.Name.Colorants, None)
            if not isinstance(colorants, pikepdf.Dictionary):
                if name_field_mentions(
                    colorants,
                    frozenset({self.source, self.destination}),
                ):
                    raise UnsupportedSpotUseError(
                        f"{min(form_locations)}: malformed PrinterMark /Colorants",
                        location=min(form_locations),
                    )
                continue
            colorant_locations = tuple(f"{item} /Colorants" for item in form_locations)
            if self.source_name not in colorants:
                if name_field_mentions(
                    colorants,
                    frozenset({self.source, self.destination}),
                ):
                    raise UnsupportedSpotUseError(
                        f"{min(form_locations)}: unsupported target in PrinterMark /Colorants",
                        location=min(form_locations),
                    )
                continue
            if not is_matching_separation(colorants[self.source_name], self.source):
                raise UnsupportedSpotUseError(
                    f"{min(form_locations)}: PrinterMark /Colorants definition is malformed",
                    location=min(form_locations),
                )
            dependency_locations = tuple(
                f"{item} {path_name(self.source_name)}" for item in colorant_locations
            )
            self._add_slot(
                colorants,
                self.source_name,
                SlotMode.DICTIONARY_KEY,
                dependency_locations,
                dependency_kind=NameDependencyKind.PRINTER_MARK_COLORANT,
            )
            self.supported_colorant_locations.update(dependency_locations)

    def _collect_colorants_candidate(self, value: Any, locations: tuple[str, ...]) -> None:
        if not isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
            return
        colorants = value.get(pikepdf.Name.Colorants, None)
        if not isinstance(colorants, pikepdf.Dictionary):
            return
        colorant_locations = tuple(f"{item} /Colorants" for item in locations)
        for key, nested in colorants.items():
            key_name = decode_pdf_name(key)
            nested_name = (
                name_value(nested[1])
                if isinstance(nested, pikepdf.Array) and len(nested) >= 2
                else None
            )
            if not ({key_name, nested_name} & {self.source, self.destination}):
                continue
            self.colorant_candidates.update(
                f"{item} {path_name(key)}" for item in colorant_locations
            )

    def _validate_coverage(self) -> None:
        unsupported = self.colorant_candidates - self.supported_colorant_locations
        if unsupported:
            raise UnsupportedSpotUseError(
                f"{min(unsupported)}: /Colorants name occurs outside supported "
                "DeviceN or PrinterMark AP/N context",
                location=min(unsupported),
            )
        expected_dependencies = {
            (dependency.kind, dependency.owner.label, dependency.location)
            for dependency in self.report.dependencies
            if dependency.name == self.source and dependency.kind in SUPPORTED_RENAME_DEPENDENCIES
        }
        missing = expected_dependencies - self.mapped_dependencies
        if missing:
            kinds = ", ".join(sorted({item[0].value for item in missing}))
            raise UnsupportedSpotUseError(
                f"inventory dependencies could not be mapped to rename slots: {kinds}"
            )
        expected = sum(
            definition.kind in {SpotKind.SEPARATION, SpotKind.DEVICEN}
            and any(component.name == self.source for component in definition.components)
            for definition in self.report.definitions.values()
        )
        if expected != len(self.mapped_definitions):
            raise UnsupportedSpotUseError(
                "inventory definitions could not be mapped one-to-one to rename slots "
                f"(expected {expected}, planned {len(self.mapped_definitions)})"
            )

    def _definition_identity(
        self,
        kind: SpotKind,
        locations: tuple[str, ...],
    ) -> tuple[Any, ...]:
        location_set = set(locations)
        matches = [
            definition
            for definition in self.report.definitions.values()
            if definition.kind is kind
            and any(component.name == self.source for component in definition.components)
            and location_set.intersection(definition.locations)
        ]
        if len(matches) != 1:
            raise UnsupportedSpotUseError(
                f"{min(locations)}: color-space definition has no unique inventory identity",
                location=min(locations),
            )
        return ("definition", matches[0].object_id)

    def _dependency_identity(
        self,
        kind: NameDependencyKind,
        locations: tuple[str, ...],
        member: int | pikepdf.Name,
    ) -> tuple[Any, ...]:
        location_set = set(locations)
        matches = [
            dependency
            for dependency in self.report.dependencies
            if dependency.name == self.source
            and dependency.kind is kind
            and dependency.location in location_set
        ]
        owners = {dependency.owner.label for dependency in matches}
        if not matches or len(owners) != 1:
            raise UnsupportedSpotUseError(
                f"{min(locations)}: exact-name dependency has no unique inventory owner",
                location=min(locations),
            )
        self.mapped_dependencies.update(
            (dependency.kind, dependency.owner.label, dependency.location) for dependency in matches
        )
        return ("dependency", owners.pop(), str(member))

    def _devicen_attributes_mention_source(self, value: pikepdf.Array) -> bool:
        if len(value) < 5 or not isinstance(value[4], pikepdf.Dictionary):
            return False
        attributes = value[4]
        process = attributes.get(pikepdf.Name.Process, None)
        colorants = attributes.get(pikepdf.Name.Colorants, None)
        mixing = attributes.get(pikepdf.Name.MixingHints, None)
        return any(
            (
                isinstance(process, pikepdf.Dictionary)
                and name_array_contains(process.get(pikepdf.Name.Components, None), self.source),
                isinstance(colorants, pikepdf.Dictionary) and self.source_name in colorants,
                isinstance(mixing, pikepdf.Dictionary)
                and mixing_hints_contain(mixing, self.source_name, self.source),
            )
        )

    def _add_slot(
        self,
        container: Any,
        member: int | pikepdf.Name,
        mode: SlotMode,
        locations: tuple[str, ...],
        *,
        definition: bool = False,
        dependency_kind: NameDependencyKind | None = None,
        physical_identity: tuple[Any, ...] | None = None,
    ) -> None:
        if mode is SlotMode.DICTIONARY_KEY and self.destination_name in container:
            raise InvalidPdfError(f"destination name collides in dictionary at {min(locations)}")
        identity = physical_identity or container_key(container, locations)
        if dependency_kind is not None:
            identity = self._dependency_identity(dependency_kind, locations, member)
        key = (*identity, mode, str(member))
        proposed = MutableNameSlot(
            container=container,
            member=member,
            mode=mode,
            source=self.source,
            destination=self.destination,
        )
        slot = self.slots.setdefault(key, proposed)
        slot.definition |= definition
        if dependency_kind is not None:
            slot.dependency_kinds.add(dependency_kind)
        slot.locations.update(locations)


def build_rename_plan(
    pdf: pikepdf.Pdf,
    report: InspectionReport,
    source: str,
    destination: str,
) -> RenamePlan:
    """Build a complete rename plan without changing the open PDF."""

    return _PlanBuilder(pdf, report, source, destination).build()


__all__ = ["RenamePlan", "build_rename_plan"]
