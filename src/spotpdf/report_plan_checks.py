"""Continue operation-specific checks across independent resource/page boundaries."""

from __future__ import annotations

from .diagnostics import DiagnosticLimit
from .inventory_graph import walk_reachable
from .model import SpotPdfError


def check_planner(pdf, inventory, request, spot, findings, gaps, append_error):
    """Never apply a partial plan; continue only independent diagnostic units."""
    from .alternate_plan import _PlanBuilder as AlternateBuilder
    from .cmyk import normalized_cmyk
    from .convert_streams import _StreamPlanBuilder as ConversionBuilder
    from .rename_plan import _PlanBuilder as RenameBuilder
    from .rename_request import validate_rename_request

    def inspect(action, label):
        if len(findings) >= request["max_findings"]:
            raise DiagnosticLimit("Finding limit reached")
        try:
            action()
        except SpotPdfError as error:
            append_error(findings, error, [spot])
            gaps.append(f"{label}: analysis stopped within this resource or content sequence")

    command = request["command"]
    if command == "rename":
        validate_rename_request(inventory, spot, request["destination"])
        builder = RenameBuilder(pdf, inventory, spot, request["destination"])
        for visit in walk_reachable(pdf):
            inspect(lambda visit=visit: builder.inspect_visit(visit), min(visit.locations))
    elif command == "set-alternate":
        builder = AlternateBuilder(pdf, inventory, spot, normalized_cmyk(request["cmyk"]))
        builder._validate_request()
        for visit in walk_reachable(pdf):
            inspect(lambda visit=visit: builder.inspect_visit(visit), min(visit.locations))
    elif command == "convert":
        builder = ConversionBuilder(pdf, spot, normalized_cmyk(request["to_cmyk"]), frozenset())
        for number, page in enumerate(pdf.pages, 1):
            inspect(
                lambda number=number, page=page: builder._process_page(number, page),
                f"Page {number}",
            )
    gaps.append(
        "Additional diagnostics check independent resources/pages; plan coverage, "
        "cross-resource consistency and post-save invariants are represented by "
        "the actual operation result"
    )
