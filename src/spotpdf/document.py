"""Document orchestration, atomic output, and post-write verification."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf

from .colors import (
    all_mode_targets,
    remove_spot_resource_aliases_for_spots,
    resource_aliases_for_spots,
    walk_pdf_object,
)
from .content import ContentRewriter
from .content_support import GraphicsState, operator_name
from .inspection import enrich_inspection_report
from .inventory import discover_spot_declarations
from .model import (
    BatchRemovalResult,
    InspectionReport,
    InvalidPdfError,
    RemovalStats,
    SpotPdfError,
    UnsupportedSpotUseError,
)
from .objects import ObjectTracker, anchored_object_key, object_key
from .publication import atomic_pdf_output, open_strict, save_pdf
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
class _ProcessedForm:
    signature: tuple[Any, ...]
    changed: bool


def inspect_pdf(path: Path) -> InspectionReport:
    """Inspect reachable spot declarations and supported paint usage."""

    with open_strict(path) as pdf:
        report = discover_spot_declarations(pdf)
        enrich_inspection_report(pdf, report)
        return report


def check_spot(path: Path, spot: str) -> bool:
    """Return whether a name is a reachable spot or legacy Separation target."""

    with open_strict(path) as pdf:
        return spot in discover_spot_declarations(pdf).spots


def remove_spot(
    input_path: Path,
    output_path: Path,
    spot: str,
    *,
    force: bool = False,
) -> RemovalStats:
    """Remove supported uses of one spot color and atomically write a clean PDF."""

    result = _remove_spots(
        input_path,
        output_path,
        requested=frozenset({spot}),
        force=force,
    )
    return result.stats


def remove_all_spots(
    input_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> BatchRemovalResult:
    """Remove supported spots while preserving process and special colorants."""

    return _remove_spots(input_path, output_path, requested=None, force=force)


def _remove_spots(
    input_path: Path,
    output_path: Path,
    *,
    requested: frozenset[str] | None,
    force: bool,
) -> BatchRemovalResult:
    """Apply one set-aware rewrite and publish it only after strict validation."""

    stats = RemovalStats()
    targets: frozenset[str] = frozenset()
    require_no_all_mode_targets = requested is None
    with atomic_pdf_output(input_path, output_path, force=force) as output:
        with open_strict(output.input_path) as pdf:
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
                _process_document(pdf, targets, apply=False, stats=RemovalStats())
                _process_document(pdf, targets, apply=True, stats=stats)
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
) -> None:
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

    if not apply:
        return
    for value in walk_pdf_object(pdf.Root, ObjectTracker()):
        stats.resources_removed += remove_spot_resource_aliases_for_spots(value, targets)


def _process_stream(
    context: _ProcessingContext,
    instructions: list[Any],
    resources: Any,
    resource_identity: tuple[Any, ...],
    initial_state: GraphicsState,
    label: str,
    form_depth: int = 0,
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
        stats=context.stats,
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
        raise InvalidPdfError(
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
    if previous is not None:
        if previous.signature != signature:
            raise UnsupportedSpotUseError(
                f"{parent_label}: a shared Form requires context-dependent rewriting"
            )
        if previous.changed:
            context.page_touched_by_form = True
            context.form_change_generation += 1
        return
    if form_key in context.processing_forms:
        raise UnsupportedSpotUseError(f"{parent_label}: cyclic Form XObjects are not supported")

    context.processing_forms.add(form_key)
    try:
        generation_before = context.form_change_generation
        instructions = pikepdf.parse_content_stream(form)
        label = f"{parent_label} Form {form_key}"
        result = _process_stream(
            context,
            instructions,
            resources,
            resource_key,
            inherited_state,
            label,
            form_depth,
        )
        if result.changed:
            if _contains_inline_image(instructions):
                raise UnsupportedSpotUseError(
                    f"{label}: rewriting a stream with inline images is not supported"
                )
            context.stats.forms_changed += 1
            context.form_change_generation += 1
            if context.apply:
                form.write(pikepdf.unparse_content_stream(result.instructions))
        subtree_changed = result.changed or context.form_change_generation != generation_before
        context.page_touched_by_form |= subtree_changed
        context.processed_forms[form_key] = _ProcessedForm(signature, subtree_changed)
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


def _verify_saved_pdf(
    path: Path,
    targets: frozenset[str],
    *,
    require_no_all_mode_targets: bool,
) -> None:
    with open_strict(path) as pdf:
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
