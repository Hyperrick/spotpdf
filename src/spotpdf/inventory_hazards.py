"""Single-pass attribution of removal-preflight hazards for inventory."""

from __future__ import annotations

from typing import Any

import pikepdf

from .colors import parse_color_space, pdf_name, resolve_color_space
from .inventory_usage import InspectionMetrics
from .model import InspectionReport, InvalidPdfError, NameDependencyKind, SpotKind
from .objects import ObjectKey, ObjectTracker, object_key
from .scan import MAX_FORM_NESTING


def collect_inventory_hazards(
    pdf: pikepdf.Pdf,
    candidates: frozenset[str],
    declarations: InspectionReport,
    *,
    metrics: InspectionMetrics | None = None,
) -> dict[str, str]:
    """Return the first structural removal hazard for every affected colorant."""

    hazards: dict[str, str] = {}
    devicen = {
        name
        for name in candidates
        if name in declarations.spots and SpotKind.DEVICEN in declarations.spots[name].kinds
    }
    _record(
        hazards,
        devicen,
        lambda name: (
            f"document: reachable DeviceN declarations contain target spot colors: {name!r}"
        ),
    )

    preseparated = {
        dependency.name
        for dependency in declarations.dependencies
        if dependency.kind is NameDependencyKind.SEPARATION_INFO
    }
    _record(
        hazards,
        candidates & preseparated,
        lambda name: f"document: page SeparationInfo metadata contains target colorants: {name!r}",
    )

    dependencies = {dependency.name for dependency in declarations.dependencies}
    _record(
        hazards,
        candidates & dependencies,
        lambda name: (
            f"document: exact-name prepress dependencies contain target colorants: {name!r}"
        ),
    )

    scanner = _ResourceHazardScanner(
        candidates,
        hazards,
        metrics or InspectionMetrics(),
    )
    scanner.scan(pdf)
    return hazards


class _ResourceHazardScanner:
    """Traverse page mutation hazards once while preserving validation order."""

    def __init__(
        self,
        candidates: frozenset[str],
        hazards: dict[str, str],
        metrics: InspectionMetrics,
    ) -> None:
        self.candidates = candidates
        self.hazards = hazards
        self.metrics = metrics
        self._active = set(candidates) - hazards.keys()
        self.seen_forms = ObjectTracker()
        self._subtree_cache: dict[ObjectKey, frozenset[str]] = {}
        self._color_value_cache: dict[tuple[ObjectKey, ObjectKey], frozenset[str]] = {}
        self._retained_direct_objects: list[Any] = []

    @property
    def active(self) -> set[str]:
        return self._active

    def scan(self, pdf: pikepdf.Pdf) -> None:
        for page_number, page in enumerate(pdf.pages, start=1):
            if not self.active:
                return
            annotations = page.obj.get(pikepdf.Name.Annots, None)
            if annotations is not None:
                hits = self._subtree_colorants(annotations) & self.active
                self._reject(
                    hits,
                    f"page {page_number}: spot color in annotation appearances is not supported",
                )
                if not self.active:
                    return
            resources = page.obj.get(pikepdf.Name.Resources, pikepdf.Dictionary())
            self._scan_resources(resources, f"page {page_number}")

    def _scan_resources(
        self,
        resources: Any,
        context: str,
        form_depth: int = 0,
    ) -> None:
        if not self.active or not isinstance(resources, (pikepdf.Dictionary, pikepdf.Stream)):
            return
        self.metrics.resource_contexts_scanned += 1

        color_spaces = resources.get(pikepdf.Name.ColorSpace, None)
        if isinstance(color_spaces, pikepdf.Dictionary):
            for value in color_spaces.values():
                info = parse_color_space(value)
                if info.kind is SpotKind.DEVICEN:
                    self._reject(
                        frozenset(info.colorants) & self.active,
                        f"{context}: DeviceN use of target spot colors is not supported",
                    )
                if (
                    isinstance(value, pikepdf.Array)
                    and value
                    and pdf_name(value[0]) == "Pattern"
                    and len(value) > 1
                ):
                    hits = self._color_value_colorants(value[1], resources) & self.active
                    self._reject(
                        hits,
                        f"{context}: uncolored patterns based on target spots are not supported",
                    )
                if not self.active:
                    return

        for category, label in (
            (pikepdf.Name.Shading, "shading"),
            (pikepdf.Name.Pattern, "pattern"),
        ):
            entries = resources.get(category, None)
            if not isinstance(entries, pikepdf.Dictionary):
                continue
            for name, value in entries.items():
                hits = self._subtree_colorants(value) & self.active
                self._reject(
                    hits,
                    f"{context}: spot color in {label} {pdf_name(name)!r} is not supported",
                )
                if not self.active:
                    return

        fonts = resources.get(pikepdf.Name.Font, None)
        if isinstance(fonts, pikepdf.Dictionary):
            for name, font in fonts.items():
                subtype = pdf_name(font.get(pikepdf.Name.Subtype, pikepdf.Name("/Unknown")))
                if subtype == "Type3":
                    hits = self._subtree_colorants(font) & self.active
                    self._reject(
                        hits,
                        f"{context}: spot color in Type3 font {pdf_name(name)!r} is not supported",
                    )
                if not self.active:
                    return

        ext_gstates = resources.get(pikepdf.Name.ExtGState, None)
        if isinstance(ext_gstates, pikepdf.Dictionary):
            for name, state in ext_gstates.items():
                soft_mask = state.get(pikepdf.Name.SMask, None)
                if soft_mask is not None:
                    hits = self._subtree_colorants(soft_mask) & self.active
                    self._reject(
                        hits,
                        f"{context}: spot color in soft mask {pdf_name(name)!r} is not supported",
                    )
                if not self.active:
                    return

        xobjects = resources.get(pikepdf.Name.XObject, None)
        if not isinstance(xobjects, pikepdf.Dictionary):
            return
        for name, xobject in xobjects.items():
            subtype = pdf_name(xobject.get(pikepdf.Name.Subtype, pikepdf.Name("/Unknown")))
            if subtype == "Image":
                color_space = xobject.get(pikepdf.Name.ColorSpace, None)
                if color_space is not None:
                    hits = self._color_value_colorants(color_space, resources) & self.active
                    self._reject(
                        hits,
                        f"{context}: spot-color image {pdf_name(name)!r} is not supported",
                    )
                    if not self.active:
                        return
                continue
            if subtype != "Form" or not self.seen_forms.visit(xobject):
                continue
            next_depth = form_depth + 1
            if next_depth > MAX_FORM_NESTING:
                raise InvalidPdfError(
                    f"{context}: Form nesting exceeds the supported limit of {MAX_FORM_NESTING}"
                )
            form_resources = xobject.get(pikepdf.Name.Resources, resources)
            self._scan_resources(
                form_resources,
                f"{context} Form {pdf_name(name)!r}",
                next_depth,
            )
            if not self.active:
                return

    def _subtree_colorants(self, value: Any) -> frozenset[str]:
        if not isinstance(value, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            return frozenset()
        key = object_key(value)
        cached = self._subtree_cache.get(key)
        if cached is not None:
            return cached
        self._retain_direct(value, key)
        result = _subtree_colorants(value)
        self._subtree_cache[key] = result
        return result

    def _color_value_colorants(self, value: Any, resources: Any) -> frozenset[str]:
        value_key = object_key(value)
        resource_key = object_key(resources)
        key = (value_key, resource_key)
        cached = self._color_value_cache.get(key)
        if cached is not None:
            return cached
        self._retain_direct(value, value_key)
        self._retain_direct(resources, resource_key)
        result = _color_value_colorants(value, resources)
        self._color_value_cache[key] = result
        return result

    def _retain_direct(self, value: Any, key: ObjectKey) -> None:
        if key[0] == "direct":
            self._retained_direct_objects.append(value)

    def _reject(self, names: frozenset[str], message: str) -> None:
        rejected = names & self._active
        for name in rejected:
            self.hazards[name] = message
        self._active.difference_update(rejected)


def _record(
    hazards: dict[str, str],
    names: set[str] | frozenset[str],
    message_for_name,
) -> None:
    for name in names:
        hazards.setdefault(name, message_for_name(name))


def _subtree_colorants(value: Any) -> frozenset[str]:
    tracker = ObjectTracker()
    names: set[str] = set()
    stack = [value]
    while stack:
        current = stack.pop()
        if not isinstance(current, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            continue
        if not tracker.visit(current):
            continue
        names.update(parse_color_space(current).colorants)
        children = current if isinstance(current, pikepdf.Array) else current.values()
        stack.extend(children)
    return frozenset(names)


def _color_value_colorants(value: Any, resources: Any) -> frozenset[str]:
    tracker = ObjectTracker()
    names: set[str] = set()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, pikepdf.Name):
            names.update(resolve_color_space(resources, current).colorants)
            continue
        if not isinstance(current, pikepdf.Array) or not tracker.visit(current):
            continue
        names.update(parse_color_space(current).colorants)
        stack.extend(current)
    return frozenset(names)
