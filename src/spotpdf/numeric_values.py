"""Finite numeric validation shared by content processing and diagnostics."""

import math
from decimal import Decimal
from typing import Any


def finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False
