"""Serializable failure provenance and bounded diagnostic collection."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Finding:
    code: str
    message: str
    spots: list[str] = field(default_factory=list)
    object_id: str | None = None
    location: str | None = None
    occurrences: list[dict[str, Any]] = field(default_factory=list)
    primary: bool = False
    rule: str | None = None

    def wire(self) -> dict[str, Any]:
        return asdict(self)


def identity(value: Any) -> str | None:
    number, generation = getattr(value, "objgen", (0, 0))
    return f"{number} {generation} R" if number else None


_collector: ContextVar[list[Finding] | None] = ContextVar("diagnostic_collector", default=None)
_limit: ContextVar[int] = ContextVar("diagnostic_limit", default=1000)


class DiagnosticLimit(Exception):
    """Stop additional diagnostics without affecting the original operation."""


@contextmanager
def collect_findings(findings: list[Finding], limit: int):
    token = _collector.set(findings)
    limit_token = _limit.set(limit)
    try:
        yield
    finally:
        _collector.reset(token)
        _limit.reset(limit_token)


def reject(message: str, **provenance: Any) -> None:
    """Use the same refusal predicate for execution and independent diagnostics."""
    from .model import UnsupportedSpotUseError

    error = UnsupportedSpotUseError(message, **provenance)
    collector = _collector.get()
    if collector is None:
        raise error
    if len(collector) >= _limit.get():
        raise DiagnosticLimit("Finding limit reached; additional areas were not examined")
    if len(collector) + len(error.findings) > _limit.get():
        collector.extend(error.findings[: _limit.get() - len(collector)])
        raise DiagnosticLimit("Finding limit reached")
    collector.extend(error.findings)


def trace_rewrite(method):
    """Attach the active source instruction without parsing an exception message."""
    from functools import wraps

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        from .model import SpotPdfError, UnsupportedSpotUseError

        self.diagnostic_index = None
        try:
            return method(self, *args, **kwargs)
        except SpotPdfError as error:
            code = (
                "unsupported_spot_use"
                if isinstance(error, UnsupportedSpotUseError)
                else "validation_error"
            )
            if not error.findings:
                error.findings = [Finding(code, str(error), location=self.context)]
            for finding in error.findings:
                if finding.location == self.context and self.diagnostic_index is not None:
                    finding.occurrences.append(
                        {
                            "location": self.context,
                            "sequence_index": self.diagnostic_index,
                            "accuracy": "structure",
                        }
                    )
            raise

    return wrapped


def definition_findings(report, spot, kind, message):
    """Preserve definition locations for request-level semantic refusals."""
    return [
        Finding("unsupported_spot_use", message, [spot], definition.object_id, location)
        for definition in report.definitions.values()
        if definition.kind == kind and any(c.name == spot for c in definition.components)
        for location in definition.locations
    ]
