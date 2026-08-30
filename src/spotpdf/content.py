"""Stateful interpretation and rewriting of PDF content-stream paint operators."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pikepdf

from .colors import pdf_name, resolve_color_space
from .content_support import (
    CLIP_TEXT_MODES,
    FILL_TEXT_MODES,
    STROKE_TEXT_MODES,
    TEXT_SHOW_OPERATORS,
    GraphicsState,
    color_object_colorants,
)
from .content_support import (
    find_text_end as _find_text_end,
)
from .content_support import (
    instruction as _instruction,
)
from .content_support import (
    named_resource as _named_resource,
)
from .content_support import (
    operator_name as _operator_name,
)
from .content_support import (
    render_mode as _render_mode,
)
from .content_support import (
    resource_dictionary as _resource_dictionary,
)
from .model import (
    ColorSpaceInfo,
    InvalidPdfError,
    RemovalStats,
    SpotKind,
    UnsupportedSpotUseError,
)

FormHandler = Callable[[Any, "GraphicsState"], None]


@dataclass
class StreamResult:
    instructions: list[Any]
    changed: bool = False
    target_paint_operations: int = 0


class ContentRewriter:
    """Rewrite one page or Form content stream for selected spot colors."""

    def __init__(
        self,
        resources: Any,
        targets: frozenset[str],
        stats: RemovalStats,
        context: str,
        form_handler: FormHandler | None = None,
    ) -> None:
        self.resources = resources
        self.targets = targets
        self.stats = stats
        self.context = context
        self.form_handler = form_handler

    def rewrite(
        self,
        instructions: Sequence[Any],
        initial_state: GraphicsState | None = None,
    ) -> StreamResult:
        state = initial_state.clone() if initial_state else GraphicsState()
        stack: list[GraphicsState] = []
        output: list[Any] = []
        changed = False
        target_paint_operations = 0
        index = 0

        while index < len(instructions):
            item = instructions[index]
            operator = _operator_name(item)
            if operator == "BT":
                end = _find_text_end(instructions, index)
                block = instructions[index : end + 1]
                block_result, state = self._rewrite_text_block(block, state)
                output.extend(block_result.instructions)
                changed |= block_result.changed
                target_paint_operations += block_result.target_paint_operations
                index = end + 1
                continue

            replacement, state_changed = self._state_instruction(item, state, stack)
            if state_changed:
                output.extend(replacement)
                changed |= replacement != [item]
                index += 1
                continue

            if operator in {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*"}:
                replacement = self._rewrite_path_paint(item, operator, state)
                output.extend(replacement)
                if replacement != [item]:
                    changed = True
                    target_paint_operations += 1
                index += 1
                continue

            if operator == "Do":
                self._process_xobject(item, state)
            elif operator == "sh":
                self._reject_target_shading(item)
            elif operator == "ET":
                raise InvalidPdfError(f"{self.context}: ET without matching BT")
            elif operator in {"BX", "EX"} and _state_uses_target(state, self.targets):
                raise UnsupportedSpotUseError(
                    f"{self.context}: compatibility sections under target color are not supported"
                )
            elif operator == "INLINE IMAGE" and _state_uses_target(state, self.targets):
                raise UnsupportedSpotUseError(
                    f"{self.context}: target-colored inline images are not supported"
                )

            output.append(item)
            index += 1

        if stack:
            raise InvalidPdfError(f"{self.context}: unbalanced q/Q graphics-state operators")
        return StreamResult(output, changed, target_paint_operations)

    def _state_instruction(
        self,
        item: Any,
        state: GraphicsState,
        stack: list[GraphicsState],
    ) -> tuple[list[Any], bool]:
        operator = _operator_name(item)
        operands = list(item.operands)

        if operator == "q":
            stack.append(state.clone())
            return [item], True
        if operator == "Q":
            if not stack:
                raise InvalidPdfError(f"{self.context}: Q without matching q")
            restored = stack.pop()
            state.nonstroking = restored.nonstroking
            state.stroking = restored.stroking
            state.text_render_mode = restored.text_render_mode
            return [item], True
        if operator in {"cs", "CS"}:
            if len(operands) != 1 or not isinstance(operands[0], pikepdf.Name):
                raise InvalidPdfError(f"{self.context}: malformed {operator} operator")
            info = resolve_color_space(self.resources, operands[0])
            if not info.resolved:
                raise InvalidPdfError(
                    f"{self.context}: unresolved color space {pdf_name(operands[0])!r}"
                )
            if info.kind is SpotKind.DEVICEN and info.contains_any(self.targets):
                raise UnsupportedSpotUseError(
                    f"{self.context}: DeviceN use of target spot colors is not supported"
                )
            if operator == "cs":
                state.nonstroking = info
            else:
                state.stroking = info
            if info.contains_any(self.targets):
                return [_instruction(operator, pikepdf.Name.DeviceGray)], True
            return [item], True
        if operator in {"sc", "scn", "SC", "SCN"}:
            selected = state.nonstroking if operator.islower() else state.stroking
            pattern_names = [value for value in operands if isinstance(value, pikepdf.Name)]
            if pattern_names:
                if operator in {"sc", "SC"}:
                    raise InvalidPdfError(f"{self.context}: malformed {operator} pattern operands")
                patterns = _resource_dictionary(self.resources, "/Pattern")
                for name in pattern_names:
                    if _named_resource(patterns, name) is None:
                        raise InvalidPdfError(
                            f"{self.context}: unresolved pattern {pdf_name(name)!r}"
                        )
            if selected.contains_any(self.targets):
                if pattern_names:
                    raise UnsupportedSpotUseError(
                        f"{self.context}: target-colored patterns are not supported"
                    )
                return [_instruction(operator, 1)], True
            return [item], True
        if operator in {"g", "rg", "k"}:
            state.nonstroking = ColorSpaceInfo()
            return [item], True
        if operator in {"G", "RG", "K"}:
            state.stroking = ColorSpaceInfo()
            return [item], True
        if operator == "Tr":
            if len(operands) != 1:
                raise InvalidPdfError(f"{self.context}: malformed Tr operator")
            try:
                mode = int(operands[0])
            except (TypeError, ValueError, OverflowError) as error:
                raise InvalidPdfError(f"{self.context}: malformed Tr operator") from error
            if mode not in range(8):
                raise InvalidPdfError(f"{self.context}: invalid text rendering mode {mode}")
            state.text_render_mode = mode
            return [item], True
        return [item], False

    def _rewrite_text_block(
        self, block: Sequence[Any], initial_state: GraphicsState
    ) -> tuple[StreamResult, GraphicsState]:
        actions: dict[int, int | None] = {}
        state = initial_state.clone()
        stack: list[GraphicsState] = []
        target_only = 0
        target_with_remaining_paint = 0

        for index, item in enumerate(block):
            operator = _operator_name(item)
            _, handled = self._state_instruction(item, state, stack)
            if handled:
                continue
            if operator not in TEXT_SHOW_OPERATORS:
                continue
            action = _text_action(state, self.targets)
            if action is _NO_TARGET:
                continue
            if state.text_render_mode in CLIP_TEXT_MODES:
                raise UnsupportedSpotUseError(
                    f"{self.context}: target-colored clipping text is not supported"
                )
            if action is None:
                if operator in {"'", '"'}:
                    raise UnsupportedSpotUseError(
                        f"{self.context}: target-only quote text operators are not supported"
                    )
                actions[index] = None
                target_only += 1
            else:
                actions[index] = action
                target_with_remaining_paint += 1

        if stack:
            raise InvalidPdfError(f"{self.context}: unbalanced q/Q inside a text object")
        if target_only and target_with_remaining_paint:
            raise UnsupportedSpotUseError(
                f"{self.context}: mixed target-only and retained paint in one text object"
            )
        if target_only:
            non_target_shows = sum(
                1
                for index, item in enumerate(block)
                if _operator_name(item) in TEXT_SHOW_OPERATORS and index not in actions
            )
            if non_target_shows:
                raise UnsupportedSpotUseError(
                    f"{self.context}: mixed target and non-target text requires font metrics"
                )

        output: list[Any] = []
        state = initial_state.clone()
        stack = []
        changed = False
        for index, item in enumerate(block):
            replacement, handled = self._state_instruction(item, state, stack)
            if index in actions:
                changed = True
                new_mode = actions[index]
                if new_mode is None:
                    self.stats.text_show_operations += 1
                    continue
                original_mode = state.text_render_mode
                output.append(_instruction("Tr", new_mode))
                output.append(item)
                output.append(_instruction("Tr", original_mode))
                if state.nonstroking.contains_any(self.targets):
                    self.stats.fills_removed += 1
                if state.stroking.contains_any(self.targets):
                    self.stats.strokes_removed += 1
                continue
            output.extend(replacement if handled else [item])
            changed |= handled and replacement != [item]

        if actions:
            self.stats.text_blocks += 1
        return StreamResult(output, changed, len(actions)), state

    def _rewrite_path_paint(self, item: Any, operator: str, state: GraphicsState) -> list[Any]:
        fill_target = state.nonstroking.contains_any(self.targets)
        stroke_target = state.stroking.contains_any(self.targets)
        original = [item]

        if operator in {"f", "F", "f*"}:
            if not fill_target:
                return original
            self.stats.fills_removed += 1
            return [_instruction("n")]
        if operator == "S":
            if not stroke_target:
                return original
            self.stats.strokes_removed += 1
            return [_instruction("n")]
        if operator == "s":
            if not stroke_target:
                return original
            self.stats.strokes_removed += 1
            return [_instruction("h"), _instruction("n")]

        even_odd = operator in {"B*", "b*"}
        closes = operator in {"b", "b*"}
        if not fill_target and not stroke_target:
            return original
        if fill_target:
            self.stats.fills_removed += 1
        if stroke_target:
            self.stats.strokes_removed += 1

        prefix = [_instruction("h")] if closes else []
        if fill_target and stroke_target:
            return prefix + [_instruction("n")]
        if fill_target:
            return [_instruction("s" if closes else "S")]
        fill_operator = "f*" if even_odd else "f"
        return prefix + [_instruction(fill_operator)]

    def _process_xobject(self, item: Any, state: GraphicsState) -> None:
        if len(item.operands) != 1 or not isinstance(item.operands[0], pikepdf.Name):
            raise InvalidPdfError(f"{self.context}: malformed Do operator")
        xobjects = _resource_dictionary(self.resources, "/XObject")
        xobject = _named_resource(xobjects, item.operands[0])
        if xobject is None:
            raise InvalidPdfError(
                f"{self.context}: unresolved XObject {pdf_name(item.operands[0])!r}"
            )
        subtype = pdf_name(xobject.get(pikepdf.Name.Subtype, pikepdf.Name("/Unknown")))
        if subtype == "Image":
            image_space = xobject.get(pikepdf.Name.ColorSpace, None)
            if image_space is not None and _color_object_contains(
                image_space, self.resources, self.targets
            ):
                raise UnsupportedSpotUseError(
                    f"{self.context}: spot-color images are not supported"
                )
            if bool(xobject.get(pikepdf.Name.ImageMask, False)) and (
                state.nonstroking.contains_any(self.targets)
            ):
                raise UnsupportedSpotUseError(
                    f"{self.context}: target-colored image masks are not supported"
                )
            return
        if subtype == "Form" and self.form_handler is not None:
            if _state_uses_target(state, self.targets):
                raise UnsupportedSpotUseError(
                    f"{self.context}: Forms invoked with inherited target color are not supported"
                )
            self.form_handler(xobject, state.clone())

    def _reject_target_shading(self, item: Any) -> None:
        if len(item.operands) != 1 or not isinstance(item.operands[0], pikepdf.Name):
            raise InvalidPdfError(f"{self.context}: malformed sh operator")
        shadings = _resource_dictionary(self.resources, "/Shading")
        shading = _named_resource(shadings, item.operands[0])
        if shading is None:
            raise InvalidPdfError(
                f"{self.context}: unresolved shading {pdf_name(item.operands[0])!r}"
            )
        color_space = shading.get(pikepdf.Name.ColorSpace, None)
        if color_space is not None and _color_object_contains(
            color_space, self.resources, self.targets
        ):
            raise UnsupportedSpotUseError(f"{self.context}: spot-color shadings are not supported")


_NO_TARGET = object()


def _text_action(state: GraphicsState, targets: frozenset[str]) -> object | int | None:
    mode = state.text_render_mode
    fill = mode in FILL_TEXT_MODES
    stroke = mode in STROKE_TEXT_MODES
    target_fill = fill and state.nonstroking.contains_any(targets)
    target_stroke = stroke and state.stroking.contains_any(targets)
    if not target_fill and not target_stroke:
        return _NO_TARGET
    remaining_fill = fill and not target_fill
    remaining_stroke = stroke and not target_stroke
    if not remaining_fill and not remaining_stroke:
        return None
    return _render_mode(remaining_fill, remaining_stroke, False)


def _state_uses_target(state: GraphicsState, targets: frozenset[str]) -> bool:
    return state.nonstroking.contains_any(targets) or state.stroking.contains_any(targets)


def _color_object_contains(value: Any, resources: Any, targets: frozenset[str]) -> bool:
    return not targets.isdisjoint(color_object_colorants(value, resources))
