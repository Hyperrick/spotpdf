"""Inspect and safely mutate named spot colors in PDF content."""

from .limits import (
    DEFAULT_PROCESSING_LIMITS,
    ProcessingBudgetExceeded,
    ProcessingLimits,
)
from .model import __version__

__all__ = [
    "DEFAULT_PROCESSING_LIMITS",
    "ProcessingBudgetExceeded",
    "ProcessingLimits",
    "__version__",
]
