"""Inspect and safely mutate named spot colors in PDF content."""

from .alternate import set_alternate_cmyk
from .convert import convert_spot_to_cmyk
from .document import check_spot, inspect_pdf, remove_all_spots, remove_spot
from .limits import (
    DEFAULT_PROCESSING_LIMITS,
    ProcessingBudgetExceeded,
    ProcessingLimits,
)
from .model import (
    AlternateResult,
    BatchRemovalResult,
    ColorantRole,
    ConversionResult,
    InspectionReport,
    InvalidPdfError,
    NestingLimitExceededError,
    RemovalStats,
    RenameResult,
    SpotKind,
    SpotPdfError,
    SpotSummary,
    UnsupportedSpotUseError,
    __version__,
)
from .rename import rename_spot

__all__ = [
    "DEFAULT_PROCESSING_LIMITS",
    "AlternateResult",
    "BatchRemovalResult",
    "ColorantRole",
    "ConversionResult",
    "InspectionReport",
    "InvalidPdfError",
    "NestingLimitExceededError",
    "ProcessingBudgetExceeded",
    "ProcessingLimits",
    "RemovalStats",
    "RenameResult",
    "SpotKind",
    "SpotPdfError",
    "SpotSummary",
    "UnsupportedSpotUseError",
    "__version__",
    "check_spot",
    "convert_spot_to_cmyk",
    "inspect_pdf",
    "remove_all_spots",
    "remove_spot",
    "rename_spot",
    "set_alternate_cmyk",
]
