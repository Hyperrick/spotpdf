"""Complete atomic mutation plan for one Separation-to-DeviceCMYK conversion."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pikepdf

from .cmyk import NormalizedCmyk, canonicalize_normalized_cmyk
from .colors import pdf_name
from .convert_aliases import reject_remaining_alias_dependencies
from .convert_resources import ColorSpaceRemoval, collect_target_resource_removals
from .convert_stream_owners import reject_unsafe_planned_stream_owners
from .convert_streams import ConversionStreamPlan, build_conversion_stream_plan
from .model import InspectionReport, SpotPdfError
from .rename_slots import semantic_pdf_fingerprint
from .separation_targets import SeparationTargetSet, collect_separation_targets


@dataclass(frozen=True)
class ConversionPlan:
    """Every allowed content and resource mutation for one exact spot."""

    spot: str
    cmyk: NormalizedCmyk
    targets: SeparationTargetSet
    streams: ConversionStreamPlan
    resource_removals: tuple[ColorSpaceRemoval, ...]

    @property
    def definitions_removed(self) -> int:
        return len(self.targets.definitions)

    @property
    def resources_removed(self) -> int:
        return len(self.resource_removals)

    def verify_original(self) -> None:
        self.targets.verify_original()
        self.streams.verify_original()
        for removal in self.resource_removals:
            removal.verify_original(self.spot)

    def apply(self) -> None:
        """Verify all originals, then apply only the planned writes and deletions."""

        self.verify_original()
        self.streams.apply()
        for removal in self.resource_removals:
            del removal.color_spaces[removal.key]
        self.verify_applied()

    def verify_applied(self) -> None:
        self.streams.verify_applied()
        for removal in self.resource_removals:
            removal.verify_removed()

    def normalized_document_fingerprint(
        self,
        pdf: pikepdf.Pdf,
        *,
        applied: bool,
    ) -> tuple[Any, ...]:
        """Mask exactly the planned stream contents and resource members."""

        stream_markers = {
            write.key: f"__spotpdf_convert_stream_{index}__".encode("ascii")
            for index, write in enumerate(self.streams.writes)
        }
        with self._masked_resource_members(applied=applied):
            return semantic_pdf_fingerprint(pdf, masked_streams=stream_markers)

    @contextmanager
    def _masked_resource_members(self, *, applied: bool) -> Iterator[None]:
        originals: list[tuple[ColorSpaceRemoval, Any, bool]] = []
        try:
            for index, removal in enumerate(self.resource_removals):
                if applied:
                    removal.verify_removed()
                    original = None
                    existed = False
                else:
                    removal.verify_original(self.spot)
                    original = removal.color_spaces[removal.key]
                    existed = True
                originals.append((removal, original, existed))
                removal.color_spaces[removal.key] = pikepdf.Name(
                    f"/__spotpdf_convert_resource_{index}__"
                )
            yield
        finally:
            for removal, original, existed in reversed(originals):
                if existed:
                    removal.color_spaces[removal.key] = original
                elif removal.key in removal.color_spaces:
                    del removal.color_spaces[removal.key]


def build_conversion_plan(
    pdf: pikepdf.Pdf,
    report: InspectionReport,
    spot: str,
    cmyk: NormalizedCmyk,
) -> ConversionPlan:
    """Build a complete conversion plan without mutating the document."""

    normalized = canonicalize_normalized_cmyk(cmyk)
    targets = collect_separation_targets(pdf, report, spot)
    removals = collect_target_resource_removals(pdf, targets)
    reject_remaining_alias_dependencies(pdf, spot, removals)
    aliases = frozenset(pdf_name(removal.key) for removal in removals)
    streams = build_conversion_stream_plan(pdf, spot, normalized, aliases)
    reject_unsafe_planned_stream_owners(pdf, streams.writes)
    if not removals:
        raise SpotPdfError("conversion plan has no target resource aliases")
    return ConversionPlan(
        spot=spot,
        cmyk=normalized,
        targets=targets,
        streams=streams,
        resource_removals=removals,
    )


__all__ = ["ConversionPlan", "build_conversion_plan"]
