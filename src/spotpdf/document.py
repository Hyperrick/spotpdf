"""Document orchestration, atomic output, and post-write verification."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any

import pikepdf

from .colors import (
    all_mode_targets,
    resource_aliases_for_spots,
)
from .content import ContentRewriter
from .content_support import GraphicsState, operator_name
from .convert_aliases import reject_remaining_alias_dependencies_for_spots
from .convert_resource_contexts import build_content_resource_graph
from .convert_stream_owners import reject_unsafe_planned_stream_owners
from .inspection import enrich_inspection_report
from .inventory import discover_spot_declarations
from .limits import DEFAULT_PROCESSING_LIMITS, ProcessingLimits, require_processing_limits
from .model import (
    BatchRemovalResult,
    InspectionReport,
    NestingLimitExceededError,
    RemovalStats,
    SpotPdfError,
    UnsupportedSpotUseError,
)
from .objects import ObjectKey, anchored_object_key, object_key
from .publication import atomic_pdf_output, open_strict, save_pdf
from .removal_resources import collect_removal_resource_aliases, rewriter_resource_key
from .scan import (
    MAX_FORM_NESTING,
    validate_document_for_changes,
)


@dataclass
class _ProcessingContext:
    pdf: pikepdf.Pdf
    targets: frozenset[str]
    apply: bool
    stats: RemovalStats
    processed_forms: dict[tuple[Any, ...], _ProcessedForm] = field(default_factory=dict)
    processing_forms: set[tuple[Any, ...]] = field(default_factory=set)
    page_touched_by_form: bool = False
    form_change_generation: int = 0


@dataclass(frozen=True)
class _FormProposal:
    """One context-specific result for a shared Form stream."""

    resource_key: tuple[Any, ...]
    subtree_changed: bool


@dataclass
class _ProcessedForm:
    form: pikepdf.Stream
    original_bytes: bytes
    replacement_bytes: bytes
    stream_changed: bool
    label: str
    proposals: dict[tuple[Any, ...], _FormProposal] = field(default_factory=dict)


@dataclass(frozen=True)
class _RemovalFormWrite:
    """One in-place Form stream write planned by the removal dry run."""

    key: ObjectKey
    kind: str
    label: str
    replacement_digest: bytes
    approved_inherited_contexts: frozenset[tuple[Any, ...]]


@dataclass(frozen=True)
class _RemovalContentPlan:
    """Resource contexts and in-place Form writes proven by one traversal."""

    form_resources: dict[ObjectKey, frozenset[tuple[Any, ...]]]
    form_writes: tuple[_RemovalFormWrite, ...]


def inspect_pdf(
    path: str | PathLike[str],
    *,
    limits: ProcessingLimits = DEFAULT_PROCESSING_LIMITS,
) -> InspectionReport:
    """Inspect reachable spot declarations and supported paint usage."""

    limits = require_processing_limits(limits)
    with open_strict(path, limits=limits) as pdf:
        report = discover_spot_declarations(pdf)
        enrich_inspection_report(pdf, report)
        return report


def check_spot(
    path: str | PathLike[str],
    spot: str,
    *,
    limits: ProcessingLimits = DEFAULT_PROCESSING_LIMITS,
) -> bool:
    """Return whether a name is a reachable spot or legacy Separation target."""

    limits = require_processing_limits(limits)
    with open_strict(path, limits=limits) as pdf:
        return spot in discover_spot_declarations(pdf).spots


def remove_spot(
    input_path: str | PathLike[str],
    output_path: str | PathLike[str],
    spot: str,
    *,
    force: bool = False,
    limits: ProcessingLimits = DEFAULT_PROCESSING_LIMITS,
) -> RemovalStats:
    """Remove supported uses of one spot color and atomically write a clean PDF."""

    input_path = Path(input_path)
    output_path = Path(output_path)
    result = _remove_spots(
        input_path,
        output_path,
        requested=frozenset({spot}),
        force=force,
        limits=require_processing_limits(limits),
    )
    return result.stats


def remove_all_spots(
    input_path: str | PathLike[str],
    output_path: str | PathLike[str],
    *,
    force: bool = False,
    limits: ProcessingLimits = DEFAULT_PROCESSING_LIMITS,
) -> BatchRemovalResult:
    """Remove supported spots while preserving process and special colorants."""

    input_path = Path(input_path)
    output_path = Path(output_path)
    return _remove_spots(
        input_path,
        output_path,
        requested=None,
        force=force,
        limits=require_processing_limits(limits),
    )


def _remove_spots(
    input_path: Path,
    output_path: Path,
    *,
    requested: frozenset[str] | None,
    force: bool,
    limits: ProcessingLimits,
) -> BatchRemovalResult:
    """Apply one set-aware rewrite and publish it only after strict validation."""

    stats = RemovalStats()
    targets: frozenset[str] = frozenset()
    require_no_all_mode_targets = requested is None
    with atomic_pdf_output(input_path, output_path, force=force, limits=limits) as output:
        with open_strict(output.input_path, limits=limits) as pdf:
            declarations = discover_spot_declarations(pdf)
            declared_names = frozenset(declarations.spots)
            if requested is None:
                targets = all_mode_targets(declarations)
            else:
                targets = requested & declared_names

            if not targets:
                shutil.copyfile(output.input_path, output.temp_path)
            else:
                validate_document_for_changes(
                    pdf,
                    targets,
                    declarations=declarations,
                )
                content_plan = _process_document(
                    pdf,
                    targets,
                    apply=False,
                    stats=RemovalStats(),
                )
                resource_removals = collect_removal_resource_aliases(
                    pdf,
                    declarations,
                    targets,
                    content_plan.form_resources,
                )
                reject_remaining_alias_dependencies_for_spots(
                    pdf,
                    targets,
                    resource_removals,
                )
                reject_unsafe_planned_stream_owners(pdf, content_plan.form_writes)
                applied_content_plan = _process_document(
                    pdf,
                    targets,
                    apply=True,
                    stats=stats,
                )
                if applied_content_plan != content_plan:
                    raise SpotPdfError("removal content plan changed between plan and apply")
                for removal in resource_removals:
                    removal.apply()
                    stats.resources_removed += 1
                remaining = discover_spot_declarations(pdf)
                remaining_targets = targets & remaining.spots.keys()
                if remaining_targets:
                    names = ", ".join(repr(name) for name in sorted(remaining_targets))
                    raise SpotPdfError(
                        f"post-rewrite validation found remaining color spaces: {names}"
                    )
                if require_no_all_mode_targets:
                    remaining_targets = all_mode_targets(remaining)
                    if remaining_targets:
                        names = ", ".join(repr(name) for name in sorted(remaining_targets))
                        raise SpotPdfError(f"remove-all left removable spot colors behind: {names}")
                save_pdf(pdf, output.temp_path)

        _verify_saved_pdf(
            output.temp_path,
            targets,
            require_no_all_mode_targets=require_no_all_mode_targets,
        )
    names = tuple(sorted(targets, key=lambda name: (name.casefold(), name)))
    return BatchRemovalResult(spots=names, stats=stats)


def _process_document(
    pdf: pikepdf.Pdf,
    targets: frozenset[str],
    *,
    apply: bool,
    stats: RemovalStats,
) -> _RemovalContentPlan:
    context = _ProcessingContext(pdf=pdf, targets=targets, apply=apply, stats=stats)
    for page_number, page in enumerate(pdf.pages, start=1):
        context.page_touched_by_form = False
        resources = page.Resources
        resource_identity = anchored_object_key(
            resources,
            ("page", page_number, "Resources"),
        )
        instructions = pikepdf.parse_content_stream(page)
        if _contains_inline_image(instructions) and resource_aliases_for_spots(resources, targets):
            raise UnsupportedSpotUseError(
                f"page {page_number}: inline images with target spot resources are not supported"
            )
        changes_before = _change_counter(stats)
        result = _process_stream(
            context,
            instructions,
            resources,
            resource_identity,
            GraphicsState(),
            f"page {page_number}",
        )
        if (
            result.changed
            or context.page_touched_by_form
            or _change_counter(stats) != changes_before
        ):
            stats.pages_changed.add(page_number)
            if apply and result.changed:
                if _contains_inline_image(instructions):
                    raise UnsupportedSpotUseError(
                        f"page {page_number}: rewriting a stream with inline images "
                        "is not supported"
                    )
                page.obj[pikepdf.Name.Contents] = pdf.make_stream(
                    pikepdf.unparse_content_stream(result.instructions)
                )

    _process_uninvoked_forms(context)

    if apply:
        for processed in sorted(
            context.processed_forms.values(),
            key=lambda item: item.label,
        ):
            if not processed.stream_changed:
                continue
            if processed.form.read_bytes() != processed.original_bytes:
                raise SpotPdfError(f"Form stream changed before apply at {processed.label}")
            processed.form.write(processed.replacement_bytes)

    return _RemovalContentPlan(
        form_resources={
            form_key: frozenset(proposal.resource_key for proposal in processed.proposals.values())
            for form_key, processed in context.processed_forms.items()
        },
        form_writes=tuple(
            sorted(
                (
                    _RemovalFormWrite(
                        form_key,
                        "Form",
                        processed.label,
                        hashlib.sha256(processed.replacement_bytes).digest(),
                        frozenset(
                            proposal.resource_key for proposal in processed.proposals.values()
                        ),
                    )
                    for form_key, processed in context.processed_forms.items()
                    if processed.stream_changed
                ),
                key=lambda item: item.label,
            )
        ),
    )


def _process_uninvoked_forms(context: _ProcessingContext) -> None:
    """Analyze every genuine but uncalled Form before any resource alias is removed."""

    graph = build_content_resource_graph(context.pdf)
    resources_by_key = {item.key: item for item in graph.contexts}
    target_resource_keys = {
        rewriter_resource_key(item.key)
        for item in graph.contexts
        if resource_aliases_for_spots(item.resources, context.targets)
    }
    relevant_form_keys = {
        owner.form_key
        for owner in graph.form_owners
        if rewriter_resource_key(resources_by_key[owner.effective_resource_key].key)
        in target_resource_keys
    }
    relevant_form_keys.update(
        form_key
        for form_key, processed in context.processed_forms.items()
        if any(
            proposal.resource_key in target_resource_keys
            for proposal in processed.proposals.values()
        )
    )
    for owner in graph.form_owners:
        if owner.form_key not in relevant_form_keys:
            continue
        resource_context = resources_by_key[owner.effective_resource_key]
        expected_resource_key = rewriter_resource_key(resource_context.key)
        previous = context.processed_forms.get(owner.form_key)
        previous_resource_keys = (
            {proposal.resource_key for proposal in previous.proposals.values()}
            if previous is not None
            else set()
        )
        if expected_resource_key in previous_resource_keys:
            continue
        _process_form(
            context,
            owner.form,
            resource_context.resources,
            expected_resource_key,
            GraphicsState(),
            f"Form owner at {owner.location}",
            1,
        )


def _process_stream(
    context: _ProcessingContext,
    instructions: list[Any],
    resources: Any,
    resource_identity: tuple[Any, ...],
    initial_state: GraphicsState,
    label: str,
    form_depth: int = 0,
    *,
    stats: RemovalStats | None = None,
):
    def handle_form(form: Any, inherited_state: GraphicsState) -> None:
        _process_form(
            context,
            form,
            resources,
            resource_identity,
            inherited_state,
            label,
            form_depth + 1,
        )

    rewriter = ContentRewriter(
        resources=resources,
        targets=context.targets,
        stats=stats or context.stats,
        context=label,
        form_handler=handle_form,
    )
    return rewriter.rewrite(instructions, initial_state)


def _process_form(
    context: _ProcessingContext,
    form: Any,
    parent_resources: Any,
    parent_resource_identity: tuple[Any, ...],
    inherited_state: GraphicsState,
    parent_label: str,
    form_depth: int,
) -> None:
    if form_depth > MAX_FORM_NESTING:
        raise NestingLimitExceededError(
            f"{parent_label}: Form nesting exceeds the supported limit of {MAX_FORM_NESTING}"
        )
    form_key = object_key(form)
    if pikepdf.Name.Resources in form:
        resources = form.get(pikepdf.Name.Resources, None)
        resource_key = anchored_object_key(
            resources,
            ("form", *form_key, "Resources"),
        )
    else:
        resources = parent_resources
        resource_key = parent_resource_identity
    signature = (
        resource_key,
        inherited_state.nonstroking.contains_any(context.targets),
        inherited_state.stroking.contains_any(context.targets),
        inherited_state.text_render_mode,
    )
    previous = context.processed_forms.get(form_key)
    if previous is not None and signature in previous.proposals:
        if previous.proposals[signature].subtree_changed:
            context.page_touched_by_form = True
            context.form_change_generation += 1
        return
    if form_key in context.processing_forms:
        raise UnsupportedSpotUseError(f"{parent_label}: cyclic Form XObjects are not supported")

    context.processing_forms.add(form_key)
    try:
        generation_before = context.form_change_generation
        label = f"{parent_label} Form {form_key}"
        original_bytes = form.read_bytes()
        instructions = pikepdf.parse_content_stream(form)
        contains_inline_image = _contains_inline_image(instructions)
        if contains_inline_image and resource_aliases_for_spots(resources, context.targets):
            raise UnsupportedSpotUseError(
                f"{label}: inline images with target spot resources are not supported"
            )
        local_stats = RemovalStats()
        result = _process_stream(
            context,
            instructions,
            resources,
            resource_key,
            inherited_state,
            label,
            form_depth,
            stats=local_stats,
        )
        if result.changed:
            if contains_inline_image:
                raise UnsupportedSpotUseError(
                    f"{label}: rewriting a stream with inline images is not supported"
                )
            context.form_change_generation += 1
        replacement_bytes = (
            pikepdf.unparse_content_stream(result.instructions)
            if result.changed
            else original_bytes
        )
        subtree_changed = result.changed or context.form_change_generation != generation_before
        if previous is None:
            previous = _ProcessedForm(
                form,
                original_bytes,
                replacement_bytes,
                result.changed,
                label,
            )
            context.processed_forms[form_key] = previous
            if result.changed:
                _merge_form_stats(context.stats, local_stats)
                context.stats.forms_changed += 1
        elif (
            previous.replacement_bytes != replacement_bytes
            or previous.stream_changed != result.changed
        ):
            raise UnsupportedSpotUseError(
                f"{parent_label}: a shared Form requires context-dependent rewriting"
            )
        previous.proposals[signature] = _FormProposal(resource_key, subtree_changed)
        context.page_touched_by_form |= subtree_changed
    finally:
        context.processing_forms.remove(form_key)


def _contains_inline_image(instructions: list[Any]) -> bool:
    return any(operator_name(item) == "INLINE IMAGE" for item in instructions)


def _change_counter(stats: RemovalStats) -> tuple[int, ...]:
    return (
        stats.forms_changed,
        stats.text_blocks,
        stats.text_show_operations,
        stats.fills_removed,
        stats.strokes_removed,
    )


def _merge_form_stats(target: RemovalStats, source: RemovalStats) -> None:
    target.text_blocks += source.text_blocks
    target.text_show_operations += source.text_show_operations
    target.fills_removed += source.fills_removed
    target.strokes_removed += source.strokes_removed


def _verify_saved_pdf(
    path: Path,
    targets: frozenset[str],
    *,
    require_no_all_mode_targets: bool,
) -> None:
    with open_strict(path, limits=None) as pdf:
        remaining = discover_spot_declarations(pdf)
        remaining_targets = targets & remaining.spots.keys()
        if remaining_targets:
            names = ", ".join(repr(name) for name in sorted(remaining_targets))
            raise SpotPdfError(f"saved PDF still declares spot colors: {names}")
        if require_no_all_mode_targets:
            remaining_targets = all_mode_targets(remaining)
            if remaining_targets:
                names = ", ".join(repr(name) for name in sorted(remaining_targets))
                raise SpotPdfError(f"saved PDF still has removable spot colors: {names}")
        for page in pdf.pages:
            pikepdf.parse_content_stream(page)
