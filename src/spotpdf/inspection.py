"""Orchestrate role-aware structural and content inventory enrichment."""

from __future__ import annotations

import pikepdf

from .colors import SPECIAL_COLORANTS
from .inventory_content import inspect_content_once
from .inventory_hazards import collect_inventory_hazards
from .inventory_usage import InspectionMetrics
from .model import ColorantRole, InspectionReport


def enrich_inspection_report(
    pdf: pikepdf.Pdf,
    report: InspectionReport,
) -> InspectionMetrics:
    """Attach per-colorant removal status using one structural and content pass."""

    candidates = frozenset(report.colorants) - SPECIAL_COLORANTS
    metrics = InspectionMetrics()
    structural = collect_inventory_hazards(pdf, candidates, report, metrics=metrics)
    content = inspect_content_once(
        pdf,
        candidates - structural.keys(),
        metrics=metrics,
    )

    for name, summary in report.colorants.items():
        if name in SPECIAL_COLORANTS:
            summary.contexts.add("reserved separation")
            continue
        if ColorantRole.PROCESS in summary.roles:
            summary.contexts.add("process colorant; preserved by --all")

        unsupported = structural.get(name) or content.unsupported.get(name)
        if unsupported is not None:
            summary.contexts.add(f"unsupported: {unsupported}")
        usage = content.usage.get(name)
        if usage is not None:
            summary.pages.update(usage.pages)
            summary.paint_operations = usage.paint_operations
        summary.contexts.add("painted" if summary.paint_operations else "declared")
    return metrics
