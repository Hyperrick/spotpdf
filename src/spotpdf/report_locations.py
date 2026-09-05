"""Resolve structural provenance to original object identities and page hints."""

from __future__ import annotations

import re

from .diagnostics import identity
from .inventory_graph import walk_reachable


def resolve_locations(pdf, findings):
    wanted = {f.location for f in findings if f.location and not f.object_id}
    for visit in walk_reachable(pdf):
        for location in visit.locations:
            if location not in wanted:
                continue
            for finding in findings:
                if finding.location == location:
                    finding.object_id = identity(visit.value)
    for finding in findings:
        locations = [finding.location] + [o.get("location") for o in finding.occurrences]
        existing = {o.get("location") for o in finding.occurrences}
        for location in locations:
            if not location:
                continue
            match = re.match(r"page (\d+)\b", location)
            if match:
                for occurrence in finding.occurrences:
                    if occurrence.get("location") == location:
                        occurrence.setdefault("page", int(match[1]))
                if location not in existing:
                    finding.occurrences.append(
                        {"location": location, "page": int(match[1]), "accuracy": "page only"}
                    )
