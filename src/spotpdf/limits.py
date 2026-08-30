"""Public processing-limit configuration and deterministic budget failures."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Final

from .model import SpotPdfError


@dataclass(frozen=True)
class ProcessingLimits:
    """Per-call limits for work performed on one untrusted input PDF."""

    max_input_bytes: int | None = 805_306_368
    max_pages: int | None = 10_000
    max_reachable_objects: int | None = 1_000_000
    max_decoded_content_bytes: int | None = 268_435_456
    max_operators: int | None = 5_000_000

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{item.name} must be a positive integer or None")


DEFAULT_PROCESSING_LIMITS: Final = ProcessingLimits()


@dataclass(frozen=True)
class _Metric:
    field: str
    label: str
    option: str


_METRICS: Final = {
    "input_bytes": _Metric("max_input_bytes", "input bytes", "--max-input-bytes"),
    "pages": _Metric("max_pages", "pages", "--max-pages"),
    "reachable_objects": _Metric(
        "max_reachable_objects",
        "reachable graph entries",
        "--max-reachable-objects",
    ),
    "decoded_content_bytes": _Metric(
        "max_decoded_content_bytes",
        "decoded content bytes",
        "--max-decoded-content-bytes",
    ),
    "operators": _Metric("max_operators", "content operators", "--max-operators"),
}


class ProcessingBudgetExceeded(SpotPdfError):
    """Raised when one deterministic application-level limit is exceeded."""

    def __init__(self, metric: str, observed: int, limit: int) -> None:
        specification = _METRICS[metric]
        self.metric = metric
        self.observed = observed
        self.limit = limit
        self.option = specification.option
        self.field = specification.field
        super().__init__(
            "processing budget exceeded: "
            f"{specification.label} {observed} > {limit} "
            f"(raise this limit with {specification.option} or "
            f"ProcessingLimits({specification.field}=...) for a trusted large job)"
        )

    def __reduce__(
        self,
    ) -> tuple[type[ProcessingBudgetExceeded], tuple[str, int, int]]:
        """Preserve structured fields across worker-process serialization."""

        return type(self), (self.metric, self.observed, self.limit)


def require_processing_limits(value: object) -> ProcessingLimits:
    """Reject accidental disabling or malformed programmatic configuration."""

    if not isinstance(value, ProcessingLimits):
        raise TypeError("limits must be a ProcessingLimits instance")
    return value


def enforce_limit(limits: ProcessingLimits, metric: str, observed: int) -> None:
    """Raise the public budget error when one observed value is too large."""

    specification = _METRICS[metric]
    limit = getattr(limits, specification.field)
    if limit is not None and observed > limit:
        raise ProcessingBudgetExceeded(metric, observed, limit)


__all__ = [
    "DEFAULT_PROCESSING_LIMITS",
    "ProcessingBudgetExceeded",
    "ProcessingLimits",
]
