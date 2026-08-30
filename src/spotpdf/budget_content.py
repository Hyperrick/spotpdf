"""Decoded Page/Form content and lexical operator budget accounting."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import pikepdf

from .limits import ProcessingBudgetExceeded, ProcessingLimits, enforce_limit


@dataclass(frozen=True)
class ContentBudgetResult:
    """Content work measured during one source preflight."""

    decoded_content_bytes: int
    operators: int


def audit_content(
    pdf: pikepdf.Pdf,
    forms: Sequence[Any],
    limits: ProcessingLimits,
) -> ContentBudgetResult:
    """Measure decoded content first, then count operator tokens incrementally."""

    pages = tuple(pdf.pages)
    decoded_content_bytes = _count_decoded_bytes(pages, forms, limits)
    operators = _count_operators(pages, forms, limits)
    return ContentBudgetResult(decoded_content_bytes, operators)


def _count_decoded_bytes(
    pages: Sequence[pikepdf.Page],
    forms: Sequence[Any],
    limits: ProcessingLimits,
) -> int:
    total = 0
    for page in pages:
        for stream in _page_content_streams(page):
            buffer = stream.get_stream_buffer(pikepdf.StreamDecodeLevel.specialized)
            total += len(buffer)
            del buffer
            enforce_limit(limits, "decoded_content_bytes", total)
    for form in forms:
        buffer = form.get_stream_buffer(pikepdf.StreamDecodeLevel.specialized)
        total += len(buffer)
        del buffer
        enforce_limit(limits, "decoded_content_bytes", total)
    return total


def _page_content_streams(page: pikepdf.Page) -> Iterator[Any]:
    contents = page.obj.get(pikepdf.Name.Contents, None)
    if isinstance(contents, pikepdf.Stream):
        yield contents
        return
    if isinstance(contents, pikepdf.Array):
        for item in contents:
            if isinstance(item, pikepdf.Stream):
                yield item


class _OperatorCounter:
    def __init__(self, limits: ProcessingLimits) -> None:
        self.limits = limits
        self.count = 0
        self.failure: ProcessingBudgetExceeded | None = None

    def add(self) -> None:
        self.count += 1
        try:
            enforce_limit(self.limits, "operators", self.count)
        except ProcessingBudgetExceeded as error:
            self.failure = error
            raise _OperatorLimitStop from None


class _OperatorLimitStop(RuntimeError):
    """Private callback sentinel; qpdf may replace callback exceptions."""


class _OperatorFilter(pikepdf.TokenFilter):
    """Discard token output while counting every lexical PDF operator word."""

    def __init__(self, counter: _OperatorCounter) -> None:
        super().__init__()
        self.counter = counter

    def handle_token(self, token: pikepdf.Token) -> None:
        if token.type_ == pikepdf.TokenType.word:
            self.counter.add()
        return None


def _count_operators(
    pages: Sequence[pikepdf.Page],
    forms: Sequence[Any],
    limits: ProcessingLimits,
) -> int:
    counter = _OperatorCounter(limits)
    for page in pages:
        _filter_contents(page, counter)
    for form in forms:
        wrapper = pikepdf.Page(pikepdf.Dictionary(Contents=form))
        _filter_contents(wrapper, counter)
    return counter.count


def _filter_contents(page: pikepdf.Page, counter: _OperatorCounter) -> None:
    try:
        page.get_filtered_contents(_OperatorFilter(counter))
    except Exception:
        if counter.failure is not None:
            raise counter.failure from None
        raise
    if counter.failure is not None:  # pragma: no cover - defensive qpdf behavior
        raise counter.failure


__all__ = ["ContentBudgetResult", "audit_content"]
