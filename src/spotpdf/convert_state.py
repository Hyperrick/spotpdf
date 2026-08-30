"""Graphics-state subset required for deterministic spot-to-CMYK conversion."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .model import ColorSpaceInfo


@dataclass
class ConversionChannelState:
    """Current color-space identity for one stroking or nonstroking channel."""

    color_space: ColorSpaceInfo = field(default_factory=ColorSpaceInfo)
    target_selected: bool = False

    def clone(self) -> ConversionChannelState:
        return ConversionChannelState(self.color_space, self.target_selected)


@dataclass
class ConversionGraphicsState:
    """Inherited graphics state that can change process-conversion semantics."""

    nonstroking: ConversionChannelState = field(default_factory=ConversionChannelState)
    stroking: ConversionChannelState = field(default_factory=ConversionChannelState)
    text_render_mode: int = 0
    font_name: str | None = None
    font_is_type3: bool = False
    nonstroking_overprint: bool = False
    stroking_overprint: bool = False
    overprint_mode: int = 0
    nonstroking_alpha: Decimal = Decimal(1)
    stroking_alpha: Decimal = Decimal(1)
    normal_blend_mode: bool = True
    soft_mask_active: bool = False
    transparency_group: bool = False
    text_knockout: bool = True

    def clone(self) -> ConversionGraphicsState:
        return ConversionGraphicsState(
            nonstroking=self.nonstroking.clone(),
            stroking=self.stroking.clone(),
            text_render_mode=self.text_render_mode,
            font_name=self.font_name,
            font_is_type3=self.font_is_type3,
            nonstroking_overprint=self.nonstroking_overprint,
            stroking_overprint=self.stroking_overprint,
            overprint_mode=self.overprint_mode,
            nonstroking_alpha=self.nonstroking_alpha,
            stroking_alpha=self.stroking_alpha,
            normal_blend_mode=self.normal_blend_mode,
            soft_mask_active=self.soft_mask_active,
            transparency_group=self.transparency_group,
            text_knockout=self.text_knockout,
        )

    @property
    def uses_target(self) -> bool:
        return self.nonstroking.target_selected or self.stroking.target_selected


__all__ = ["ConversionChannelState", "ConversionGraphicsState"]
