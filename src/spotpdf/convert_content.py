"""Plan one content-stream rewrite from a Separation to explicit DeviceCMYK."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import pikepdf

from .cmyk import NormalizedCmyk, scale_cmyk_tint
from .colors import pdf_name, resolve_color_space
from .content_support import (
    FILL_TEXT_MODES,
    STROKE_TEXT_MODES,
    TEXT_SHOW_OPERATORS,
    color_object_colorants,
    instruction,
    named_resource,
    operator_name,
    resource_dictionary,
)
from .convert_operators import (
    FILL_PATH_OPERATORS,
    STROKE_PATH_OPERATORS,
    validate_conversion_operator,
)
from .convert_state import ConversionGraphicsState
from .diagnostics import trace_rewrite
from .model import InvalidPdfError, SpotKind, UnsupportedSpotUseError
from .numeric_values import finite_number

FormHandler = Callable[[Any, ConversionGraphicsState], bool]


@dataclass(frozen=True)
class StreamConversionResult:
    """Replacement instructions and deterministic conversion counters."""

    instructions: tuple[Any, ...]
    changed: bool
    subtree_changed: bool
    color_operators_rewritten: int
    target_paint_operations: int


class ConversionContentPlanner:
    """Interpret one stream and rewrite only target color-state operators."""

    def __init__(
        self,
        resources: Any,
        spot: str,
        cmyk: NormalizedCmyk,
        context: str,
        *,
        form_handler: FormHandler | None = None,
    ) -> None:
        self.resources = resources
        self.spot = spot
        self.cmyk = cmyk
        self.context = context
        self.form_handler = form_handler

    @trace_rewrite
    def rewrite(
        self,
        instructions: Sequence[Any],
        initial_state: ConversionGraphicsState | None = None,
    ) -> StreamConversionResult:
        state = initial_state.clone() if initial_state else ConversionGraphicsState()
        stack: list[ConversionGraphicsState] = []
        output: list[Any] = []
        changed = False
        subtree_changed = False
        rewritten = 0
        target_paint = 0
        inside_text = False
        compatibility_depth = 0
        has_inline_image = False

        for index, item in enumerate(instructions):
            self.diagnostic_index = index
            operator = operator_name(item)
            replacement: list[Any] | None = None

            validate_conversion_operator(
                operator,
                inside_text=inside_text,
                compatibility_depth=compatibility_depth,
                context=self.context,
            )

            if operator == "q":
                stack.append(state.clone())
            elif operator == "Q":
                if not stack:
                    raise InvalidPdfError(f"{self.context}: Q without matching q")
                state = stack.pop()
            elif operator == "BT":
                if inside_text:
                    raise InvalidPdfError(f"{self.context}: nested BT text object")
                inside_text = True
            elif operator == "ET":
                if not inside_text:
                    raise InvalidPdfError(f"{self.context}: ET without matching BT")
                inside_text = False
            elif operator in {"cs", "CS"}:
                replacement = self._select_color_space(
                    item,
                    state,
                    compatibility_depth=compatibility_depth,
                )
            elif operator in {"scn", "SCN", "sc", "SC"}:
                replacement = self._set_color(
                    item,
                    state,
                    compatibility_depth=compatibility_depth,
                )
            elif operator in {"g", "rg", "k", "G", "RG", "K"}:
                self._select_direct_color(item, state)
            elif operator == "Tr":
                self._set_text_render_mode(item, state)
            elif operator == "Tf":
                self._set_font(item, state)
            elif operator == "gs":
                self._apply_ext_gstate(item, state)
            elif operator in FILL_PATH_OPERATORS | STROKE_PATH_OPERATORS:
                target_paint += self._validate_path_paint(operator, state)
            elif operator in TEXT_SHOW_OPERATORS:
                if not inside_text:
                    raise InvalidPdfError(
                        f"{self.context}: {operator} text-show operator outside BT/ET"
                    )
                target_paint += self._validate_text_paint(state)
            elif operator == "Do":
                subtree_changed |= self._process_xobject(item, state)
            elif operator == "sh":
                self._reject_target_shading(item)
            elif operator == "INLINE IMAGE":
                has_inline_image = True
                self._reject_target_inline_image(item, state)
            elif operator == "BX":
                if state.uses_target:
                    raise UnsupportedSpotUseError(
                        f"{self.context}: compatibility sections under target color "
                        "are not supported",
                        location=self.context,
                    )
                compatibility_depth += 1
            elif operator == "EX":
                if compatibility_depth == 0:
                    raise InvalidPdfError(f"{self.context}: EX without matching BX")
                if state.uses_target:
                    raise UnsupportedSpotUseError(
                        f"{self.context}: compatibility sections under target color "
                        "are not supported",
                        location=self.context,
                    )
                compatibility_depth -= 1

            if replacement is None:
                output.append(item)
                continue
            output.extend(replacement)
            if replacement != [item]:
                changed = True
                rewritten += 1

        if stack:
            raise InvalidPdfError(f"{self.context}: unbalanced q/Q graphics-state operators")
        if inside_text:
            raise InvalidPdfError(f"{self.context}: BT without matching ET")
        if compatibility_depth:
            raise InvalidPdfError(f"{self.context}: BX without matching EX")
        if changed and has_inline_image:
            raise UnsupportedSpotUseError(
                f"{self.context}: rewriting a stream with inline images is not supported",
                location=self.context,
            )
        return StreamConversionResult(
            instructions=tuple(output),
            changed=changed,
            subtree_changed=changed or subtree_changed,
            color_operators_rewritten=rewritten,
            target_paint_operations=target_paint,
        )

    def _select_color_space(
        self,
        item: Any,
        state: ConversionGraphicsState,
        *,
        compatibility_depth: int,
    ) -> list[Any]:
        operator = operator_name(item)
        operands = list(item.operands)
        if len(operands) != 1 or not isinstance(operands[0], pikepdf.Name):
            raise InvalidPdfError(f"{self.context}: malformed {operator} operator")
        info = resolve_color_space(self.resources, operands[0])
        if not info.resolved:
            raise InvalidPdfError(
                f"{self.context}: unresolved color space {pdf_name(operands[0])!r}"
            )
        if info.kind is SpotKind.DEVICEN and info.contains(self.spot):
            raise UnsupportedSpotUseError(
                f"{self.context}: DeviceN use of target spot color is not supported",
                location=self.context,
            )
        self._reject_target_pattern_space(operands[0])
        channel = state.nonstroking if operator == "cs" else state.stroking
        channel.color_space = info
        channel.target_selected = info.contains(self.spot)
        if not channel.target_selected:
            return [item]
        if compatibility_depth:
            raise UnsupportedSpotUseError(
                f"{self.context}: target color inside a compatibility section is not supported",
                location=self.context,
            )
        self._reject_default_cmyk()
        process_operator = "k" if operator == "cs" else "K"
        return [instruction(process_operator, *scale_cmyk_tint(1, self.cmyk))]

    def _set_color(
        self,
        item: Any,
        state: ConversionGraphicsState,
        *,
        compatibility_depth: int,
    ) -> list[Any]:
        operator = operator_name(item)
        channel = state.nonstroking if operator.islower() else state.stroking
        if not channel.target_selected:
            self._validate_non_target_pattern_operands(item)
            return [item]
        if compatibility_depth:
            raise UnsupportedSpotUseError(
                f"{self.context}: target color inside a compatibility section is not supported",
                location=self.context,
            )
        if operator in {"sc", "SC"}:
            raise UnsupportedSpotUseError(
                f"{self.context}: target Separation requires {operator}n, not {operator}",
                location=self.context,
            )
        self._reject_default_cmyk()
        operands = list(item.operands)
        if len(operands) != 1:
            raise InvalidPdfError(
                f"{self.context}: target {operator} requires exactly one numeric tint"
            )
        process_operator = "k" if operator == "scn" else "K"
        try:
            cmyk = scale_cmyk_tint(operands[0], self.cmyk)
        except InvalidPdfError as error:
            raise InvalidPdfError(f"{self.context}: {error}") from error
        return [instruction(process_operator, *cmyk)]

    def _select_direct_color(self, item: Any, state: ConversionGraphicsState) -> None:
        operator = operator_name(item)
        component_counts = {"g": 1, "G": 1, "rg": 3, "RG": 3, "k": 4, "K": 4}
        operands = list(item.operands)
        if len(operands) != component_counts[operator] or any(
            not finite_number(value) for value in operands
        ):
            raise InvalidPdfError(f"{self.context}: malformed {operator} operator")
        names = {
            "g": pikepdf.Name.DeviceGray,
            "G": pikepdf.Name.DeviceGray,
            "rg": pikepdf.Name.DeviceRGB,
            "RG": pikepdf.Name.DeviceRGB,
            "k": pikepdf.Name.DeviceCMYK,
            "K": pikepdf.Name.DeviceCMYK,
        }
        channel = state.nonstroking if operator.islower() else state.stroking
        channel.color_space = resolve_color_space(self.resources, names[operator])
        channel.target_selected = False

    def _set_text_render_mode(self, item: Any, state: ConversionGraphicsState) -> None:
        operands = list(item.operands)
        if len(operands) != 1:
            raise InvalidPdfError(f"{self.context}: malformed Tr operator")
        try:
            mode = int(operands[0])
        except (OverflowError, TypeError, ValueError) as error:
            raise InvalidPdfError(f"{self.context}: malformed Tr operator") from error
        if mode not in range(8) or operands[0] != mode:
            raise InvalidPdfError(f"{self.context}: invalid text rendering mode {operands[0]!r}")
        state.text_render_mode = mode

    def _set_font(self, item: Any, state: ConversionGraphicsState) -> None:
        operands = list(item.operands)
        if (
            len(operands) != 2
            or not isinstance(operands[0], pikepdf.Name)
            or not finite_number(operands[1])
        ):
            raise InvalidPdfError(f"{self.context}: malformed Tf operator")
        fonts = resource_dictionary(self.resources, "/Font")
        font = named_resource(fonts, operands[0])
        if not isinstance(font, pikepdf.Dictionary):
            raise InvalidPdfError(f"{self.context}: unresolved font {pdf_name(operands[0])!r}")
        subtype = font.get(pikepdf.Name.Subtype, None)
        if not isinstance(subtype, pikepdf.Name):
            raise InvalidPdfError(f"{self.context}: malformed font {pdf_name(operands[0])!r}")
        state.font_name = pdf_name(operands[0])
        state.font_is_type3 = subtype == pikepdf.Name.Type3

    def _apply_ext_gstate(self, item: Any, state: ConversionGraphicsState) -> None:
        operands = list(item.operands)
        if len(operands) != 1 or not isinstance(operands[0], pikepdf.Name):
            raise InvalidPdfError(f"{self.context}: malformed gs operator")
        states = resource_dictionary(self.resources, "/ExtGState")
        parameters = named_resource(states, operands[0])
        if not isinstance(parameters, pikepdf.Dictionary):
            raise InvalidPdfError(f"{self.context}: unresolved ExtGState {pdf_name(operands[0])!r}")

        has_stroking = pikepdf.Name.OP in parameters
        has_nonstroking = pikepdf.Name.op in parameters
        if has_stroking:
            value = self._boolean(parameters[pikepdf.Name.OP], "OP")
            state.stroking_overprint = value
            if not has_nonstroking:
                state.nonstroking_overprint = value
        if has_nonstroking:
            state.nonstroking_overprint = self._boolean(parameters[pikepdf.Name.op], "op")
        if pikepdf.Name.OPM in parameters:
            mode = parameters[pikepdf.Name.OPM]
            if isinstance(mode, bool) or not isinstance(mode, int) or mode not in {0, 1}:
                raise InvalidPdfError(f"{self.context}: malformed ExtGState /OPM")
            state.overprint_mode = mode
        if pikepdf.Name.Font in parameters:
            self._set_ext_gstate_font(
                parameters[pikepdf.Name.Font],
                state,
                pdf_name(operands[0]),
            )
        if pikepdf.Name.CA in parameters:
            state.stroking_alpha = self._unit_number(parameters[pikepdf.Name.CA], "CA")
        if pikepdf.Name.ca in parameters:
            state.nonstroking_alpha = self._unit_number(parameters[pikepdf.Name.ca], "ca")
        if pikepdf.Name.BM in parameters:
            state.normal_blend_mode = self._normal_blend_mode(parameters[pikepdf.Name.BM])
        if pikepdf.Name.SMask in parameters:
            mask = parameters[pikepdf.Name.SMask]
            if isinstance(mask, pikepdf.Name) and mask == pikepdf.Name("/None"):
                state.soft_mask_active = False
            elif isinstance(mask, pikepdf.Dictionary):
                state.soft_mask_active = True
            else:
                raise InvalidPdfError(f"{self.context}: malformed ExtGState /SMask")
        if pikepdf.Name.TK in parameters:
            state.text_knockout = self._boolean(parameters[pikepdf.Name.TK], "TK")

        # BG*, UCR*, TR*, and HT remain verbatim downstream device-rendering
        # controls; the requested recipe is an explicit pre-render DeviceCMYK
        # value. FL, SM, and SA affect rasterization/geometry, while AIS can
        # matter only with alpha or masks that target-paint validation rejects.

    def _set_ext_gstate_font(
        self,
        value: Any,
        state: ConversionGraphicsState,
        resource_name: str,
    ) -> None:
        if (
            not isinstance(value, pikepdf.Array)
            or len(value) != 2
            or not isinstance(value[0], pikepdf.Dictionary)
            or not finite_number(value[1])
        ):
            raise InvalidPdfError(f"{self.context}: malformed ExtGState /Font")
        subtype = value[0].get(pikepdf.Name.Subtype, None)
        if not isinstance(subtype, pikepdf.Name):
            raise InvalidPdfError(f"{self.context}: malformed ExtGState /Font")
        state.font_name = f"ExtGState {resource_name!r} /Font"
        state.font_is_type3 = subtype == pikepdf.Name.Type3

    def _validate_path_paint(self, operator: str, state: ConversionGraphicsState) -> int:
        count = 0
        if operator in FILL_PATH_OPERATORS and state.nonstroking.target_selected:
            self._validate_channel_paint(state, stroking=False)
            count += 1
        if operator in STROKE_PATH_OPERATORS and state.stroking.target_selected:
            self._validate_channel_paint(state, stroking=True)
            count += 1
        return count

    def _validate_text_paint(self, state: ConversionGraphicsState) -> int:
        fill = state.text_render_mode in FILL_TEXT_MODES
        stroke = state.text_render_mode in STROKE_TEXT_MODES
        uses_target = (fill and state.nonstroking.target_selected) or (
            stroke and state.stroking.target_selected
        )
        if uses_target and state.font_is_type3:
            raise UnsupportedSpotUseError(
                f"{self.context}: target-colored Type 3 text is not supported",
                location=self.context,
            )
        if uses_target and state.font_name is None:
            raise InvalidPdfError(f"{self.context}: target-colored text has no valid font")
        if uses_target and not state.text_knockout:
            raise UnsupportedSpotUseError(
                f"{self.context}: non-knockout target text is not supported", location=self.context
            )
        count = 0
        if fill and state.nonstroking.target_selected:
            self._validate_channel_paint(state, stroking=False)
            count += 1
        if stroke and state.stroking.target_selected:
            self._validate_channel_paint(state, stroking=True)
            count += 1
        return count

    def _validate_channel_paint(
        self,
        state: ConversionGraphicsState,
        *,
        stroking: bool,
    ) -> None:
        label = "stroking" if stroking else "nonstroking"
        overprint = state.stroking_overprint if stroking else state.nonstroking_overprint
        alpha = state.stroking_alpha if stroking else state.nonstroking_alpha
        if overprint:
            raise UnsupportedSpotUseError(
                f"{self.context}: effective {label} overprint on target paint is not supported",
                location=self.context,
            )
        if alpha != Decimal(1):
            raise UnsupportedSpotUseError(
                f"{self.context}: non-opaque {label} target paint is not supported",
                location=self.context,
            )
        if not state.normal_blend_mode:
            raise UnsupportedSpotUseError(
                f"{self.context}: non-Normal blend mode on target paint is not supported",
                location=self.context,
            )
        if state.soft_mask_active:
            raise UnsupportedSpotUseError(
                f"{self.context}: soft-masked target paint is not supported", location=self.context
            )
        if state.transparency_group:
            raise UnsupportedSpotUseError(
                f"{self.context}: target paint in a transparency group is not supported",
                location=self.context,
            )

    def _process_xobject(self, item: Any, state: ConversionGraphicsState) -> bool:
        operands = list(item.operands)
        if len(operands) != 1 or not isinstance(operands[0], pikepdf.Name):
            raise InvalidPdfError(f"{self.context}: malformed Do operator")
        xobjects = resource_dictionary(self.resources, "/XObject")
        xobject = named_resource(xobjects, operands[0])
        if xobject is None:
            raise InvalidPdfError(f"{self.context}: unresolved XObject {pdf_name(operands[0])!r}")
        if not isinstance(xobject, pikepdf.Stream):
            raise InvalidPdfError(f"{self.context}: malformed XObject {pdf_name(operands[0])!r}")
        subtype = pdf_name(xobject.get(pikepdf.Name.Subtype, pikepdf.Name("/Unknown")))
        if subtype == "Image":
            image_space = xobject.get(pikepdf.Name.ColorSpace, None)
            if image_space is not None and self.spot in color_object_colorants(
                image_space, self.resources
            ):
                raise UnsupportedSpotUseError(
                    f"{self.context}: spot-color images are not supported",
                    location=self.context,
                    pdf_object=xobject,
                    rule="spot_image",
                )
            if bool(xobject.get(pikepdf.Name.ImageMask, False)) and (
                state.nonstroking.target_selected
            ):
                raise UnsupportedSpotUseError(
                    f"{self.context}: target-colored image masks are not supported",
                    location=self.context,
                )
            return False
        if subtype == "Form" and self.form_handler is not None:
            return self.form_handler(xobject, state.clone())
        if subtype == "Form" or state.uses_target:
            raise UnsupportedSpotUseError(
                f"{self.context}: unsupported XObject subtype {subtype!r} under target color",
                location=self.context,
            )
        return False

    def _reject_target_shading(self, item: Any) -> None:
        operands = list(item.operands)
        if len(operands) != 1 or not isinstance(operands[0], pikepdf.Name):
            raise InvalidPdfError(f"{self.context}: malformed sh operator")
        shadings = resource_dictionary(self.resources, "/Shading")
        shading = named_resource(shadings, operands[0])
        if shading is None:
            raise InvalidPdfError(f"{self.context}: unresolved shading {pdf_name(operands[0])!r}")
        if not isinstance(shading, (pikepdf.Dictionary, pikepdf.Stream)):
            raise InvalidPdfError(f"{self.context}: malformed shading {pdf_name(operands[0])!r}")
        color_space = shading.get(pikepdf.Name.ColorSpace, None)
        if color_space is not None and self.spot in color_object_colorants(
            color_space, self.resources
        ):
            raise UnsupportedSpotUseError(
                f"{self.context}: spot-color shadings are not supported", location=self.context
            )

    def _reject_target_inline_image(
        self,
        item: Any,
        state: ConversionGraphicsState,
    ) -> None:
        color_space = item.iimage.obj.get(pikepdf.Name.ColorSpace, None)
        if state.nonstroking.target_selected or (
            color_space is not None
            and self.spot in color_object_colorants(color_space, self.resources)
        ):
            raise UnsupportedSpotUseError(
                f"{self.context}: target-colored inline images are not supported",
                location=self.context,
            )

    def _validate_non_target_pattern_operands(self, item: Any) -> None:
        operator = operator_name(item)
        names = [value for value in item.operands if isinstance(value, pikepdf.Name)]
        if not names:
            return
        if operator in {"sc", "SC"}:
            raise InvalidPdfError(f"{self.context}: malformed {operator} pattern operands")
        patterns = resource_dictionary(self.resources, "/Pattern")
        for name in names:
            if named_resource(patterns, name) is None:
                raise InvalidPdfError(f"{self.context}: unresolved pattern {pdf_name(name)!r}")

    def _reject_default_cmyk(self) -> None:
        color_spaces = resource_dictionary(self.resources, "/ColorSpace")
        if (
            isinstance(color_spaces, pikepdf.Dictionary)
            and pikepdf.Name.DefaultCMYK in color_spaces
        ):
            raise UnsupportedSpotUseError(
                f"{self.context}: /DefaultCMYK would remap the requested process values",
                location=self.context,
            )

    def _reject_target_pattern_space(self, name: pikepdf.Name) -> None:
        color_spaces = resource_dictionary(self.resources, "/ColorSpace")
        value = named_resource(color_spaces, name)
        if not isinstance(value, pikepdf.Array) or len(value) < 2:
            return
        if pdf_name(value[0]) != "Pattern":
            return
        if self.spot in color_object_colorants(value[1], self.resources):
            raise UnsupportedSpotUseError(
                f"{self.context}: uncolored patterns based on the target spot are not supported",
                location=self.context,
            )

    def _boolean(self, value: Any, name: str) -> bool:
        if not isinstance(value, bool):
            raise InvalidPdfError(f"{self.context}: malformed ExtGState /{name}")
        return value

    def _unit_number(self, value: Any, name: str) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise InvalidPdfError(f"{self.context}: malformed ExtGState /{name}")
        try:
            number = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise InvalidPdfError(f"{self.context}: malformed ExtGState /{name}") from error
        if not number.is_finite() or not Decimal(0) <= number <= Decimal(1):
            raise InvalidPdfError(f"{self.context}: malformed ExtGState /{name}")
        return number

    def _normal_blend_mode(self, value: Any) -> bool:
        if isinstance(value, pikepdf.Name):
            return value in {pikepdf.Name.Normal, pikepdf.Name.Compatible}
        if (
            isinstance(value, pikepdf.Array)
            and value
            and all(isinstance(item, pikepdf.Name) for item in value)
        ):
            return all(item in {pikepdf.Name.Normal, pikepdf.Name.Compatible} for item in value)
        raise InvalidPdfError(f"{self.context}: malformed ExtGState /BM")


def is_transparency_group(value: Any) -> bool:
    """Return whether a Page/Form group activates the transparency model."""

    if not isinstance(value, pikepdf.Dictionary):
        return False
    return value.get(pikepdf.Name.S, None) == pikepdf.Name.Transparency


__all__ = [
    "ConversionContentPlanner",
    "StreamConversionResult",
    "is_transparency_group",
]
