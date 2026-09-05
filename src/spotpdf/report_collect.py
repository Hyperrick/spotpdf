"""Read-only operation-specific diagnostics using the mutation validators."""

from __future__ import annotations

from .diagnostics import DiagnosticLimit, Finding, collect_findings
from .inventory import discover_spot_declarations
from .model import SpotPdfError


def collect(pdf, request, findings, gaps):
    from .alternate_plan import build_alternate_plan
    from .cmyk import normalized_cmyk
    from .convert_plan import build_conversion_plan
    from .rename_plan import build_rename_plan
    from .scan import validate_document_for_mutation, validate_spot_uses_for_removal

    inventory = discover_spot_declarations(pdf)
    targets = inventory.spot_names if request.get("all_spots") else {request["spot"]}
    try:
        validate_document_for_mutation(pdf)
    except SpotPdfError as error:
        append_error(findings, error, targets)
        return inventory
    try:
        if request["command"] in {"remove", "convert"}:
            with collect_findings(findings, request["max_findings"]):
                validate_spot_uses_for_removal(pdf, frozenset(targets), declarations=inventory)
        for spot in sorted(targets):
            try:
                if request["command"] == "rename":
                    build_rename_plan(pdf, inventory, spot, request["destination"])
                elif request["command"] == "set-alternate":
                    build_alternate_plan(pdf, inventory, spot, normalized_cmyk(request["cmyk"]))
                elif request["command"] == "convert":
                    build_conversion_plan(pdf, inventory, spot, normalized_cmyk(request["to_cmyk"]))
            except SpotPdfError as error:
                append_error(findings, error, [spot])
                gaps.append(f"{spot}: detailed operation planning stopped at its first refusal")
                from .report_plan_checks import check_planner

                try:
                    check_planner(pdf, inventory, request, spot, findings, gaps, append_error)
                except SpotPdfError as further_error:
                    append_error(findings, further_error, [spot])
                    gaps.append(f"{spot}: independent planner checks could not continue")
        if request["command"] == "remove":
            from .document import _process_page, _ProcessingContext
            from .model import RemovalStats

            for number, page in enumerate(pdf.pages, 1):
                if len(findings) >= request["max_findings"]:
                    raise DiagnosticLimit("Finding limit reached")
                context = _ProcessingContext(pdf, frozenset(targets), False, RemovalStats())
                try:
                    _process_page(context, page, number)
                except SpotPdfError as error:
                    append_error(findings, error, targets)
                    gaps.append(f"Page {number}: content analysis stopped at its first refusal")
            gaps.append(
                "Additional content checks are per page; cross-page rewrite and "
                "post-save invariants are represented by the actual operation result"
            )
    except (SpotPdfError, DiagnosticLimit) as error:
        if isinstance(error, SpotPdfError):
            append_error(findings, error, targets)
        gaps.append(f"Additional validation stopped: {error}")
    return inventory


def append_error(findings, error, spots):
    from .cli_output import _classify_error

    code, message, _ = _classify_error(error)
    additions = getattr(error, "findings", []) or [Finding(code, message, sorted(spots))]
    for finding in additions:
        if not finding.spots:
            finding.spots = sorted(spots)
        findings.append(finding)


def consolidate(findings, inventory, limit):
    """Merge identical refusals and attach exact inventory identities where available."""
    result = []
    seen = {}
    for finding in findings:
        key = (
            finding.code,
            finding.rule or finding.message,
            finding.object_id,
            finding.location if finding.object_id is None else None,
        )
        if key in seen:
            seen[key].primary |= finding.primary
            seen[key].occurrences.extend(
                o for o in finding.occurrences if o not in seen[key].occurrences
            )
            if finding.location != seen[key].location:
                seen[key].occurrences.append(
                    {
                        "location": finding.location,
                        "object_id": finding.object_id,
                        "accuracy": "structure",
                    }
                )
            continue
        if len(result) >= limit:
            break
        if finding.location == "document":
            # Document-wide prepress failures retain their actual definition locations.
            for definition in inventory.definitions.values():
                names = {c.name for c in definition.components}
                if names.intersection(finding.spots):
                    for location in definition.locations:
                        finding.occurrences.append(
                            {
                                "object_id": definition.object_id,
                                "location": location,
                                "accuracy": "structure",
                            }
                        )
        seen[key] = finding
        result.append(finding)
    return result
