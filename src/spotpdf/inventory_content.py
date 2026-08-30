"""Single-pass read-only interpretation of named-color content usage."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pikepdf

from .colors import pdf_name, resolve_color_space, resource_aliases_for_spots
from .content_support import (
    CLIP_TEXT_MODES,
    FILL_TEXT_MODES,
    STROKE_TEXT_MODES,
    TEXT_SHOW_OPERATORS,
    GraphicsState,
    color_object_colorants,
    find_text_end,
    named_resource,
    operator_name,
    resource_dictionary,
)
from .inventory_usage import (
    ColorantUsage,
    ContentInventory,
    FormScan,
    InspectionMetrics,
    TextSummary,
)
from .model import ColorSpaceInfo, InvalidPdfError, NestingLimitExceededError, SpotKind
from .objects import anchored_object_key, object_key
from .scan import MAX_FORM_NESTING


class _ContentInventoryScanner:
    """Interpret each reached page and Form stream at most once."""

    def __init__(
        self,
        candidates: frozenset[str],
        metrics: InspectionMetrics | None = None,
    ) -> None:
        normalized = frozenset(candidates)
        self.candidates = normalized
        self._active_names = set(normalized)
        self._active_snapshot = normalized
        self._active_generation = 0
        self._eligible_cache: dict[frozenset[str], tuple[int, frozenset[str]]] = {}
        self.result = ContentInventory(
            usage={name: ColorantUsage() for name in normalized},
            metrics=metrics or InspectionMetrics(),
        )
        self.processed_forms: dict[tuple[Any, ...], FormScan] = {}
        self.processing_forms: set[tuple[Any, ...]] = set()

    @property
    def active(self) -> frozenset[str]:
        return self._active_snapshot

    def scan(self, pdf: pikepdf.Pdf) -> ContentInventory:
        for page_number, page in enumerate(pdf.pages, start=1):
            participants = self.active
            if not participants:
                break
            resources = page.Resources
            resource_identity = anchored_object_key(
                resources,
                ("page", page_number, "Resources"),
            )
            instructions = pikepdf.parse_content_stream(page)
            self.result.metrics.page_streams_parsed += 1
            self.result.metrics.instructions_visited += len(instructions)

            if _contains_inline_image(instructions):
                aliases = resource_aliases_for_spots(resources, participants)
                hits = frozenset(
                    colorant
                    for info in aliases.values()
                    for colorant in info.colorants
                    if colorant in participants
                )
                self._reject(
                    hits,
                    f"page {page_number}: inline images with target spot resources "
                    "are not supported",
                    participants,
                )

            changed = self._scan_stream(
                instructions,
                resources,
                resource_identity,
                GraphicsState(),
                f"page {page_number}",
                participants,
            )
            for name in changed & self.active:
                self.result.usage[name].pages.add(page_number)
        return self.result

    def _scan_stream(
        self,
        instructions: Sequence[Any],
        resources: Any,
        resource_identity: tuple[Any, ...],
        initial_state: GraphicsState,
        label: str,
        participants: frozenset[str],
        form_depth: int = 0,
    ) -> frozenset[str]:
        state = initial_state.clone()
        stack: list[GraphicsState] = []
        changed: set[str] = set()
        index = 0

        while index < len(instructions) and self._eligible(participants):
            item = instructions[index]
            operator = operator_name(item)
            if operator == "BT":
                end = find_text_end(instructions, index)
                block_changed, state = self._scan_text_block(
                    instructions[index : end + 1],
                    state,
                    resources,
                    label,
                    participants,
                )
                changed.update(block_changed)
                index = end + 1
                continue

            handled, state_changed = self._state_instruction(
                item,
                state,
                stack,
                resources,
                label,
                participants,
            )
            if handled:
                changed.update(state_changed)
                index += 1
                continue

            if operator in {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*"}:
                changed.update(self._record_path_paint(operator, state, participants))
                index += 1
                continue

            if operator == "Do":
                changed.update(
                    self._inspect_xobject(
                        item,
                        state,
                        resources,
                        resource_identity,
                        label,
                        participants,
                        form_depth,
                    )
                )
            elif operator == "sh":
                self._inspect_shading(item, resources, label, participants)
            elif operator == "ET":
                raise InvalidPdfError(f"{label}: ET without matching BT")
            elif operator in {"BX", "EX"}:
                hits = _state_colorants(state) & self._eligible(participants)
                self._reject(
                    hits,
                    f"{label}: compatibility sections under target color are not supported",
                    participants,
                )
            elif operator == "INLINE IMAGE":
                hits = _state_colorants(state) & self._eligible(participants)
                self._reject(
                    hits,
                    f"{label}: target-colored inline images are not supported",
                    participants,
                )
            index += 1

        if stack and self._eligible(participants):
            raise InvalidPdfError(f"{label}: unbalanced q/Q graphics-state operators")
        return frozenset(changed)

    def _state_instruction(
        self,
        item: Any,
        state: GraphicsState,
        stack: list[GraphicsState],
        resources: Any,
        label: str,
        participants: frozenset[str],
    ) -> tuple[bool, frozenset[str]]:
        operator = operator_name(item)
        operands = list(item.operands)

        if operator == "q":
            stack.append(state.clone())
            return True, frozenset()
        if operator == "Q":
            if not stack:
                raise InvalidPdfError(f"{label}: Q without matching q")
            restored = stack.pop()
            state.nonstroking = restored.nonstroking
            state.stroking = restored.stroking
            state.text_render_mode = restored.text_render_mode
            return True, frozenset()
        if operator in {"cs", "CS"}:
            if len(operands) != 1 or not isinstance(operands[0], pikepdf.Name):
                raise InvalidPdfError(f"{label}: malformed {operator} operator")
            info = resolve_color_space(resources, operands[0])
            if not info.resolved:
                raise InvalidPdfError(f"{label}: unresolved color space {pdf_name(operands[0])!r}")
            hits = frozenset(info.colorants) & self._eligible(participants)
            if info.kind is SpotKind.DEVICEN and hits:
                self._reject(
                    hits,
                    f"{label}: DeviceN use of target spot colors is not supported",
                    participants,
                )
                hits = frozenset()
            if operator == "cs":
                state.nonstroking = info
            else:
                state.stroking = info
            return True, hits
        if operator in {"sc", "scn", "SC", "SCN"}:
            selected = state.nonstroking if operator.islower() else state.stroking
            pattern_names = [value for value in operands if isinstance(value, pikepdf.Name)]
            if pattern_names:
                if operator in {"sc", "SC"}:
                    raise InvalidPdfError(f"{label}: malformed {operator} pattern operands")
                patterns = resource_dictionary(resources, "/Pattern")
                for name in pattern_names:
                    if named_resource(patterns, name) is None:
                        raise InvalidPdfError(f"{label}: unresolved pattern {pdf_name(name)!r}")
            hits = frozenset(selected.colorants) & self._eligible(participants)
            if hits and pattern_names:
                self._reject(
                    hits,
                    f"{label}: target-colored patterns are not supported",
                    participants,
                )
                hits = frozenset()
            return True, hits
        if operator in {"g", "rg", "k"}:
            state.nonstroking = ColorSpaceInfo()
            return True, frozenset()
        if operator in {"G", "RG", "K"}:
            state.stroking = ColorSpaceInfo()
            return True, frozenset()
        if operator == "Tr":
            if len(operands) != 1:
                raise InvalidPdfError(f"{label}: malformed Tr operator")
            try:
                mode = int(operands[0])
            except (TypeError, ValueError, OverflowError) as error:
                raise InvalidPdfError(f"{label}: malformed Tr operator") from error
            if mode not in range(8):
                raise InvalidPdfError(f"{label}: invalid text rendering mode {mode}")
            state.text_render_mode = mode
            return True, frozenset()
        return False, frozenset()

    def _scan_text_block(
        self,
        block: Sequence[Any],
        initial_state: GraphicsState,
        resources: Any,
        label: str,
        participants: frozenset[str],
    ) -> tuple[frozenset[str], GraphicsState]:
        state = initial_state.clone()
        stack: list[GraphicsState] = []
        changed: set[str] = set()
        summaries: dict[str, TextSummary] = {}
        show_count = 0

        for item in block:
            if not self._eligible(participants):
                break
            operator = operator_name(item)
            handled, state_changed = self._state_instruction(
                item,
                state,
                stack,
                resources,
                label,
                participants,
            )
            changed.update(state_changed)
            if handled or operator not in TEXT_SHOW_OPERATORS:
                continue
            show_count += 1
            self._record_text_show(operator, state, summaries, label, participants)

        if stack and self._eligible(participants):
            raise InvalidPdfError(f"{label}: unbalanced q/Q inside a text object")

        for name, summary in summaries.items():
            if name not in self._eligible(participants):
                continue
            if summary.target_only and summary.retained:
                self._reject(
                    frozenset({name}),
                    f"{label}: mixed target-only and retained paint in one text object",
                    participants,
                )
            elif summary.target_only and show_count > summary.target_only + summary.retained:
                self._reject(
                    frozenset({name}),
                    f"{label}: mixed target and non-target text requires font metrics",
                    participants,
                )

        committed = self._eligible(participants)
        for name, summary in summaries.items():
            if name not in committed:
                continue
            usage = self.result.usage[name]
            usage.text_show_operations += summary.text_shows
            usage.fills += summary.fills
            usage.strokes += summary.strokes
        return frozenset(changed) & committed, state

    def _record_text_show(
        self,
        operator: str,
        state: GraphicsState,
        summaries: dict[str, TextSummary],
        label: str,
        participants: frozenset[str],
    ) -> None:
        fill = state.text_render_mode in FILL_TEXT_MODES
        stroke = state.text_render_mode in STROKE_TEXT_MODES
        eligible = self._eligible(participants)
        fill_names = frozenset(state.nonstroking.colorants) & eligible if fill else frozenset()
        stroke_names = frozenset(state.stroking.colorants) & eligible if stroke else frozenset()

        for name in fill_names | stroke_names:
            target_fill = name in fill_names
            target_stroke = name in stroke_names
            summary = summaries.setdefault(name, TextSummary())
            if state.text_render_mode in CLIP_TEXT_MODES:
                self._reject(
                    frozenset({name}),
                    f"{label}: target-colored clipping text is not supported",
                    participants,
                )
                continue
            remaining_fill = fill and not target_fill
            remaining_stroke = stroke and not target_stroke
            if not remaining_fill and not remaining_stroke:
                if operator in {"'", '"'}:
                    self._reject(
                        frozenset({name}),
                        f"{label}: target-only quote text operators are not supported",
                        participants,
                    )
                    continue
                summary.target_only += 1
                summary.text_shows += 1
            else:
                summary.retained += 1
                summary.fills += int(target_fill)
                summary.strokes += int(target_stroke)

    def _record_path_paint(
        self,
        operator: str,
        state: GraphicsState,
        participants: frozenset[str],
    ) -> frozenset[str]:
        eligible = self._eligible(participants)
        fill_names = frozenset(state.nonstroking.colorants) & eligible
        stroke_names = frozenset(state.stroking.colorants) & eligible
        fills = fill_names if operator in {"f", "F", "f*", "B", "B*", "b", "b*"} else set()
        strokes = stroke_names if operator in {"S", "s", "B", "B*", "b", "b*"} else set()
        for name in fills:
            self.result.usage[name].fills += 1
        for name in strokes:
            self.result.usage[name].strokes += 1
        return frozenset(fills | strokes)

    def _inspect_xobject(
        self,
        item: Any,
        state: GraphicsState,
        resources: Any,
        resource_identity: tuple[Any, ...],
        label: str,
        participants: frozenset[str],
        form_depth: int,
    ) -> frozenset[str]:
        if len(item.operands) != 1 or not isinstance(item.operands[0], pikepdf.Name):
            raise InvalidPdfError(f"{label}: malformed Do operator")
        xobjects = resource_dictionary(resources, "/XObject")
        xobject = named_resource(xobjects, item.operands[0])
        if xobject is None:
            raise InvalidPdfError(f"{label}: unresolved XObject {pdf_name(item.operands[0])!r}")
        subtype = pdf_name(xobject.get(pikepdf.Name.Subtype, pikepdf.Name("/Unknown")))
        if subtype == "Image":
            image_space = xobject.get(pikepdf.Name.ColorSpace, None)
            if image_space is not None:
                hits = color_object_colorants(image_space, resources) & self._eligible(participants)
                self._reject(
                    hits,
                    f"{label}: spot-color images are not supported",
                    participants,
                )
            if bool(xobject.get(pikepdf.Name.ImageMask, False)):
                hits = frozenset(state.nonstroking.colorants) & self._eligible(participants)
                self._reject(
                    hits,
                    f"{label}: target-colored image masks are not supported",
                    participants,
                )
            return frozenset()
        if subtype != "Form":
            return frozenset()

        inherited_hits = _state_colorants(state) & self._eligible(participants)
        self._reject(
            inherited_hits,
            f"{label}: Forms invoked with inherited target color are not supported",
            participants,
        )
        return self._inspect_form(
            xobject,
            resources,
            resource_identity,
            state,
            label,
            participants,
            form_depth + 1,
        )

    def _inspect_form(
        self,
        form: Any,
        parent_resources: Any,
        parent_resource_identity: tuple[Any, ...],
        inherited_state: GraphicsState,
        parent_label: str,
        participants: frozenset[str],
        form_depth: int,
    ) -> frozenset[str]:
        eligible = self._eligible(participants)
        if not eligible:
            return frozenset()
        if form_depth > MAX_FORM_NESTING:
            raise NestingLimitExceededError(
                f"{parent_label}: Form nesting exceeds the supported limit of {MAX_FORM_NESTING}"
            )
        form_key = object_key(form)
        if pikepdf.Name.Resources in form:
            resources = form.get(pikepdf.Name.Resources, None)
            resource_identity = anchored_object_key(
                resources,
                ("form", *form_key, "Resources"),
            )
        else:
            resources = parent_resources
            resource_identity = parent_resource_identity

        nonstroking = frozenset(inherited_state.nonstroking.colorants)
        stroking = frozenset(inherited_state.stroking.colorants)
        previous = self.processed_forms.get(form_key)
        if previous is not None:
            if (
                previous.resource_identity != resource_identity
                or previous.text_render_mode != inherited_state.text_render_mode
            ):
                incompatible = eligible
            else:
                incompatible = (
                    previous.nonstroking.symmetric_difference(nonstroking)
                    | previous.stroking.symmetric_difference(stroking)
                ) & eligible
            self._reject(
                frozenset(incompatible),
                f"{parent_label}: a shared Form requires context-dependent rewriting",
                participants,
            )
            return previous.changed & self._eligible(participants)

        fresh = set(eligible)
        if form_key in self.processing_forms:
            self._reject(
                frozenset(fresh),
                f"{parent_label}: cyclic Form XObjects are not supported",
                participants,
            )
            return frozenset()

        self.processing_forms.add(form_key)
        try:
            instructions = pikepdf.parse_content_stream(form)
            self.result.metrics.form_streams_parsed += 1
            self.result.metrics.instructions_visited += len(instructions)
            label = f"{parent_label} Form {form_key}"
            changed = self._scan_stream(
                instructions,
                resources,
                resource_identity,
                inherited_state,
                label,
                frozenset(fresh),
                form_depth,
            )
            if _contains_inline_image(instructions):
                self._reject(
                    changed,
                    f"{label}: rewriting a stream with inline images is not supported",
                    frozenset(fresh),
                )
            changed &= self._eligible(frozenset(fresh))
            self.processed_forms[form_key] = FormScan(
                resource_identity=resource_identity,
                nonstroking=nonstroking,
                stroking=stroking,
                text_render_mode=inherited_state.text_render_mode,
                changed=changed,
            )
            return changed
        finally:
            self.processing_forms.remove(form_key)

    def _inspect_shading(
        self,
        item: Any,
        resources: Any,
        label: str,
        participants: frozenset[str],
    ) -> None:
        if len(item.operands) != 1 or not isinstance(item.operands[0], pikepdf.Name):
            raise InvalidPdfError(f"{label}: malformed sh operator")
        shadings = resource_dictionary(resources, "/Shading")
        shading = named_resource(shadings, item.operands[0])
        if shading is None:
            raise InvalidPdfError(f"{label}: unresolved shading {pdf_name(item.operands[0])!r}")
        color_space = shading.get(pikepdf.Name.ColorSpace, None)
        if color_space is not None:
            hits = color_object_colorants(color_space, resources) & self._eligible(participants)
            self._reject(
                hits,
                f"{label}: spot-color shadings are not supported",
                participants,
            )

    def _eligible(self, participants: frozenset[str]) -> frozenset[str]:
        cached = self._eligible_cache.get(participants)
        if cached is not None and cached[0] == self._active_generation:
            return cached[1]
        eligible = participants & self._active_snapshot
        self._eligible_cache[participants] = (self._active_generation, eligible)
        return eligible

    def _reject(
        self,
        names: frozenset[str],
        message: str,
        participants: frozenset[str],
    ) -> None:
        rejected = names & participants & self._active_snapshot
        if not rejected:
            return
        for name in rejected:
            self.result.unsupported[name] = message
        self._active_names.difference_update(rejected)
        self._active_snapshot = frozenset(self._active_names)
        self._active_generation += 1


def inspect_content_once(
    pdf: pikepdf.Pdf,
    candidates: frozenset[str],
    *,
    metrics: InspectionMetrics | None = None,
) -> ContentInventory:
    """Inspect all selected colorants while parsing each reached stream once."""

    return _ContentInventoryScanner(candidates, metrics).scan(pdf)


def _state_colorants(state: GraphicsState) -> frozenset[str]:
    return frozenset(state.nonstroking.colorants) | frozenset(state.stroking.colorants)


def _contains_inline_image(instructions: Sequence[Any]) -> bool:
    return any(operator_name(item) == "INLINE IMAGE" for item in instructions)
