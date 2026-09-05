"""Document-wide, non-mutating planning of Separation content rewrites."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import pikepdf

from .cmyk import NormalizedCmyk
from .colors import pdf_name
from .content_support import operator_name
from .convert_content import ConversionContentPlanner, is_transparency_group
from .convert_resource_contexts import build_content_resource_graph
from .convert_state import ConversionGraphicsState
from .inventory_graph import walk_reachable
from .model import (
    InvalidPdfError,
    NestingLimitExceededError,
    SpotPdfError,
    UnsupportedSpotUseError,
)
from .objects import ObjectKey, anchored_object_key, object_key
from .scan import MAX_FORM_NESTING


@dataclass(frozen=True)
class StreamWrite:
    """One exact decoded stream-content replacement."""

    stream: pikepdf.Stream
    key: ObjectKey
    original_digest: bytes
    replacement_bytes: bytes
    replacement_digest: bytes
    label: str
    kind: str

    def verify_original(self) -> None:
        if _stream_digest(self.stream, self.label) != self.original_digest:
            raise SpotPdfError(f"content stream changed before apply at {self.label}")

    def verify_applied(self) -> None:
        if _stream_digest(self.stream, self.label) != self.replacement_digest:
            raise SpotPdfError(f"requested content rewrite is absent at {self.label}")


@dataclass(frozen=True)
class ConversionStreamPlan:
    """All deterministic stream writes plus public conversion counters."""

    writes: tuple[StreamWrite, ...]
    page_content_sequences_changed: int
    forms_changed: int
    color_operators_rewritten: int
    pages_affected: tuple[int, ...]

    @property
    def masked_stream_keys(self) -> frozenset[ObjectKey]:
        return frozenset(write.key for write in self.writes)

    def verify_original(self) -> None:
        for write in self.writes:
            write.verify_original()

    def apply(self) -> None:
        for write in self.writes:
            write.stream.write(write.replacement_bytes)

    def verify_applied(self) -> None:
        for write in self.writes:
            write.verify_applied()


@dataclass(frozen=True)
class _FormProposal:
    replacement_bytes: bytes
    changed: bool
    subtree_changed: bool


@dataclass
class _FormRecord:
    canonical_bytes: bytes
    changed: bool
    proposals: dict[tuple[Any, ...], _FormProposal] = field(default_factory=dict)


class _StreamPlanBuilder:
    def __init__(
        self,
        pdf: pikepdf.Pdf,
        spot: str,
        cmyk: NormalizedCmyk,
        removed_aliases: frozenset[str],
    ) -> None:
        self.pdf = pdf
        self.spot = spot
        self.cmyk = cmyk
        self.removed_aliases = removed_aliases
        self.writes: dict[ObjectKey, StreamWrite] = {}
        self.form_records: dict[ObjectKey, _FormRecord] = {}
        self.processing_forms: set[ObjectKey] = set()
        self.page_content_sequences_changed = 0
        self.forms_changed = 0
        self.color_operators_rewritten = 0
        self.pages_affected: set[int] = set()
        self.page_stream_usage = self._page_stream_usage()
        self.resource_graph = build_content_resource_graph(pdf)
        self.resource_contexts = {context.key: context for context in self.resource_graph.contexts}

    def build(self) -> ConversionStreamPlan:
        for page_number, page in enumerate(self.pdf.pages, start=1):
            self._process_page(page_number, page)
        self._process_uninvoked_forms()
        return ConversionStreamPlan(
            writes=tuple(sorted(self.writes.values(), key=lambda item: item.label)),
            page_content_sequences_changed=self.page_content_sequences_changed,
            forms_changed=self.forms_changed,
            color_operators_rewritten=self.color_operators_rewritten,
            pages_affected=tuple(sorted(self.pages_affected)),
        )

    def _process_page(self, page_number: int, page: pikepdf.Page) -> None:
        label = f"page {page_number}"
        resources = page.obj.get(pikepdf.Name.Resources, None)
        if resources is None:
            resources = pikepdf.Dictionary()
        if not isinstance(resources, pikepdf.Dictionary):
            raise InvalidPdfError(f"{label}: malformed /Resources dictionary")
        resource_key = anchored_object_key(resources, ("page", page_number, "Resources"))
        initial_state = ConversionGraphicsState(
            transparency_group=is_transparency_group(page.obj.get(pikepdf.Name.Group, None))
        )
        instructions = pikepdf.parse_content_stream(page)
        result = self._rewrite_stream(
            instructions,
            resources,
            resource_key,
            initial_state,
            label,
            0,
        )
        if result.subtree_changed:
            self.pages_affected.add(page_number)
        if not result.changed:
            return
        streams = _page_content_streams(page, label)
        if not streams:
            raise InvalidPdfError(f"{label}: parsed content has no source stream")
        keys = tuple(object_key(stream) for stream in streams)
        if len(set(keys)) != len(keys):
            raise UnsupportedSpotUseError(
                f"{label}: repeated page content streams cannot be rewritten safely", location=label
            )
        shared = sorted(key for key in keys if len(self.page_stream_usage.get(key, ())) > 1)
        if shared:
            raise UnsupportedSpotUseError(
                f"{label}: shared page content streams cannot be rewritten safely", location=label
            )
        replacement = pikepdf.unparse_content_stream(result.instructions)
        for index, stream in enumerate(streams):
            requested = replacement if index == 0 else b""
            self._add_write(stream, requested, f"{label} /Contents[{index}]", "Page")
        self.page_content_sequences_changed += 1
        self.color_operators_rewritten += result.color_operators_rewritten

    def _rewrite_stream(
        self,
        instructions: list[Any] | tuple[Any, ...],
        resources: pikepdf.Dictionary,
        resource_key: tuple[Any, ...],
        initial_state: ConversionGraphicsState,
        label: str,
        form_depth: int,
    ):
        def handle_form(form: Any, inherited_state: ConversionGraphicsState) -> bool:
            return self._process_form(
                form,
                resources,
                resource_key,
                inherited_state,
                label,
                form_depth + 1,
            )

        planner = ConversionContentPlanner(
            resources,
            self.spot,
            self.cmyk,
            label,
            form_handler=handle_form,
        )
        return planner.rewrite(instructions, initial_state)

    def _process_form(
        self,
        form: pikepdf.Stream,
        parent_resources: pikepdf.Dictionary,
        parent_resource_key: tuple[Any, ...],
        inherited_state: ConversionGraphicsState,
        parent_label: str,
        form_depth: int,
    ) -> bool:
        if form_depth > MAX_FORM_NESTING:
            raise NestingLimitExceededError(
                f"{parent_label}: Form nesting exceeds the supported limit of {MAX_FORM_NESTING}"
            )
        key = object_key(form)
        if key in self.processing_forms:
            raise UnsupportedSpotUseError(
                f"{parent_label}: cyclic Form XObjects are not supported", location=parent_label
            )
        resources, resource_key = self._form_resources(
            form,
            parent_resources,
            parent_resource_key,
            key,
            parent_label,
        )
        state = inherited_state.clone()
        state.transparency_group |= is_transparency_group(form.get(pikepdf.Name.Group, None))
        signature = (resource_key, _state_signature(state))
        record = self.form_records.get(key)
        if record is not None and signature in record.proposals:
            return record.proposals[signature].subtree_changed

        self.processing_forms.add(key)
        try:
            label = f"{parent_label} Form {key}"
            original = _stream_bytes(form, label)
            instructions = pikepdf.parse_content_stream(form)
            result = self._rewrite_stream(
                instructions,
                resources,
                resource_key,
                state,
                label,
                form_depth,
            )
            replacement = (
                pikepdf.unparse_content_stream(result.instructions) if result.changed else original
            )
            proposal = _FormProposal(replacement, result.changed, result.subtree_changed)
            if record is None:
                record = _FormRecord(replacement, result.changed)
                self.form_records[key] = record
                if result.changed:
                    self._add_write(form, replacement, label, "Form")
                    self.forms_changed += 1
                    self.color_operators_rewritten += result.color_operators_rewritten
            elif record.canonical_bytes != replacement or record.changed != result.changed:
                raise UnsupportedSpotUseError(
                    f"{parent_label}: a shared Form requires context-dependent rewriting",
                    location=parent_label,
                )
            record.proposals[signature] = proposal
            return result.subtree_changed
        finally:
            self.processing_forms.remove(key)

    def _form_resources(
        self,
        form: pikepdf.Stream,
        parent_resources: pikepdf.Dictionary,
        parent_resource_key: tuple[Any, ...],
        form_key: ObjectKey,
        label: str,
    ) -> tuple[pikepdf.Dictionary, tuple[Any, ...]]:
        if pikepdf.Name.Resources not in form:
            return parent_resources, parent_resource_key
        resources = form.get(pikepdf.Name.Resources, None)
        if not isinstance(resources, pikepdf.Dictionary):
            raise InvalidPdfError(f"{label}: malformed Form /Resources dictionary")
        return resources, anchored_object_key(resources, ("form", *form_key, "Resources"))

    def _process_uninvoked_forms(self) -> None:
        approved_keys = {owner.form_key for owner in self.resource_graph.form_owners}
        for owner in self.resource_graph.form_owners:
            context = self.resource_contexts[owner.effective_resource_key]
            self._process_form(
                owner.form,
                context.resources,
                context.key,
                ConversionGraphicsState(),
                f"Form owner at {owner.location}",
                1,
            )

        inspected: set[ObjectKey] = set()
        for visit in walk_reachable(self.pdf):
            form = visit.value
            if not isinstance(form, pikepdf.Stream):
                continue
            if form.get(pikepdf.Name.Subtype, None) != pikepdf.Name.Form:
                continue
            key = object_key(form)
            if key in approved_keys or key in inspected:
                continue
            inspected.add(key)
            label = f"uninvoked Form at {min(visit.locations)}"
            instructions = pikepdf.parse_content_stream(form)
            resources = form.get(pikepdf.Name.Resources, None)
            if resources is None:
                if _references_alias(instructions, self.removed_aliases):
                    raise UnsupportedSpotUseError(
                        f"{label}: inherited target resources have no invocation context",
                        location=label,
                    )
            elif not isinstance(resources, pikepdf.Dictionary):
                raise InvalidPdfError(f"{label}: malformed /Resources dictionary")

    def _add_write(
        self,
        stream: pikepdf.Stream,
        replacement: bytes,
        label: str,
        kind: str,
    ) -> None:
        key = object_key(stream)
        if key[0] != "indirect":
            raise UnsupportedSpotUseError(
                f"{label}: direct content streams cannot be rewritten safely", location=label
            )
        original = _stream_bytes(stream, label)
        proposed = StreamWrite(
            stream=stream,
            key=key,
            original_digest=hashlib.sha256(original).digest(),
            replacement_bytes=replacement,
            replacement_digest=hashlib.sha256(replacement).digest(),
            label=label,
            kind=kind,
        )
        current = self.writes.setdefault(key, proposed)
        if current.replacement_bytes != replacement:
            raise UnsupportedSpotUseError(
                f"{label}: one shared stream requires conflicting replacements", location=label
            )

    def _page_stream_usage(self) -> dict[ObjectKey, set[int]]:
        usage: dict[ObjectKey, set[int]] = {}
        for page_number, page in enumerate(self.pdf.pages, start=1):
            for stream in _page_content_streams(page, f"page {page_number}"):
                usage.setdefault(object_key(stream), set()).add(page_number)
        return usage


def _page_content_streams(page: pikepdf.Page, label: str) -> tuple[pikepdf.Stream, ...]:
    contents = page.obj.get(pikepdf.Name.Contents, None)
    if contents is None:
        return ()
    if isinstance(contents, pikepdf.Stream):
        return (contents,)
    if not isinstance(contents, pikepdf.Array) or not all(
        isinstance(item, pikepdf.Stream) for item in contents
    ):
        raise InvalidPdfError(f"{label}: malformed /Contents")
    return tuple(contents)


def _stream_bytes(stream: pikepdf.Stream, label: str) -> bytes:
    try:
        return bytes(stream.read_bytes(pikepdf.StreamDecodeLevel.specialized))
    except (pikepdf.DataDecodingError, pikepdf.PdfError) as error:
        raise InvalidPdfError(f"{label}: content stream cannot be decoded") from error


def _stream_digest(stream: pikepdf.Stream, label: str) -> bytes:
    return hashlib.sha256(_stream_bytes(stream, label)).digest()


def _state_signature(state: ConversionGraphicsState) -> tuple[Any, ...]:
    def channel(value: Any) -> tuple[Any, ...]:
        info = value.color_space
        return (
            info.kind.value if info.kind is not None else None,
            info.colorants,
            info.resource_name,
            info.resolved,
            value.target_selected,
        )

    return (
        channel(state.nonstroking),
        channel(state.stroking),
        state.text_render_mode,
        state.font_name,
        state.font_is_type3,
        state.nonstroking_overprint,
        state.stroking_overprint,
        state.overprint_mode,
        state.nonstroking_alpha,
        state.stroking_alpha,
        state.normal_blend_mode,
        state.soft_mask_active,
        state.transparency_group,
        state.text_knockout,
    )


def _references_alias(instructions: list[Any], aliases: frozenset[str]) -> bool:
    for item in instructions:
        operator = operator_name(item)
        if operator == "INLINE IMAGE":
            color_space = item.iimage.obj.get(pikepdf.Name.ColorSpace, None)
            if isinstance(color_space, pikepdf.Name) and pdf_name(color_space) in aliases:
                return True
            continue
        if operator not in {"cs", "CS"} or len(item.operands) != 1:
            continue
        operand = item.operands[0]
        if isinstance(operand, pikepdf.Name) and pdf_name(operand) in aliases:
            return True
    return False


def build_conversion_stream_plan(
    pdf: pikepdf.Pdf,
    spot: str,
    cmyk: NormalizedCmyk,
    removed_aliases: frozenset[str],
) -> ConversionStreamPlan:
    """Plan every supported page/Form rewrite without mutating the document."""

    return _StreamPlanBuilder(pdf, spot, cmyk, removed_aliases).build()


__all__ = ["ConversionStreamPlan", "StreamWrite", "build_conversion_stream_plan"]
