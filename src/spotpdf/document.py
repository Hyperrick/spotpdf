"""Document orchestration, atomic output, and post-write verification."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf

from .colors import (
    SPECIAL_COLORANTS,
    all_mode_targets,
    remove_spot_resource_aliases_for_spots,
    resource_aliases_for_spots,
    walk_pdf_object,
)
from .content import ContentRewriter, GraphicsState
from .inventory import discover_spot_declarations
from .model import (
    BatchRemovalResult,
    ColorantRole,
    InspectionReport,
    InvalidPdfError,
    RemovalStats,
    SpotPdfError,
    UnsupportedSpotUseError,
)
from .objects import ObjectTracker, object_key
from .scan import (
    MAX_FORM_NESTING,
    validate_document_for_changes,
    validate_spot_uses_for_removal,
)


@dataclass
class _ProcessingContext:
    pdf: pikepdf.Pdf
    targets: frozenset[str]
    apply: bool
    stats: RemovalStats
    processed_forms: dict[tuple[Any, ...], tuple[Any, ...]] = field(default_factory=dict)
    processing_forms: set[tuple[Any, ...]] = field(default_factory=set)


def inspect_pdf(path: Path) -> InspectionReport:
    """Inspect reachable spot declarations and supported paint usage."""

    with _open_strict(path) as pdf:
        report = discover_spot_declarations(pdf)
        for name, summary in report.colorants.items():
            if name in SPECIAL_COLORANTS:
                summary.contexts.add("reserved separation")
                continue
            if ColorantRole.PROCESS in summary.roles:
                summary.contexts.add("process colorant; preserved by --all")
            stats = RemovalStats()
            try:
                validate_spot_uses_for_removal(
                    pdf,
                    frozenset({name}),
                    declarations=report,
                )
                _process_document(pdf, frozenset({name}), apply=False, stats=stats)
            except UnsupportedSpotUseError as error:
                summary.contexts.add(f"unsupported: {error}")
            summary.pages.update(stats.pages_changed)
            summary.paint_operations = (
                stats.text_show_operations + stats.fills_removed + stats.strokes_removed
            )
            if summary.paint_operations:
                summary.contexts.add("painted")
            else:
                summary.contexts.add("declared")
        return report


def check_spot(path: Path, spot: str) -> bool:
    """Return whether a name is a reachable spot or legacy Separation target."""

    with _open_strict(path) as pdf:
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

    input_path = input_path.resolve()
    if not input_path.is_file():
        raise InvalidPdfError(f"input PDF does not exist: {input_path}")
    output_path = _output_path_without_final_symlink_resolution(output_path)
    if input_path == output_path:
        raise InvalidPdfError("input and output paths must be different")
    if output_path.is_symlink():
        raise InvalidPdfError(f"output path must not be a symbolic link: {output_path}")
    if output_path.exists() and not force:
        raise InvalidPdfError(f"output already exists (use --force): {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = _temporary_output_path(output_path)
    stats = RemovalStats()
    targets: frozenset[str] = frozenset()
    require_no_all_mode_targets = requested is None
    try:
        with _open_strict(input_path) as pdf:
            declarations = discover_spot_declarations(pdf)
            declared_names = frozenset(declarations.spots)
            if requested is None:
                targets = all_mode_targets(declarations)
            else:
                targets = requested & declared_names

            if not targets:
                shutil.copyfile(input_path, temp_path)
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
                pdf.save(
                    temp_path,
                    force_version=pdf.pdf_version,
                    preserve_pdfa=True,
                    compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.preserve,
                    linearize=pdf.is_linearized,
                )

        shutil.copymode(input_path, temp_path)
        _verify_saved_pdf(
            temp_path,
            targets,
            require_no_all_mode_targets=require_no_all_mode_targets,
        )
        os.replace(temp_path, output_path)
        names = tuple(sorted(targets, key=lambda name: (name.casefold(), name)))
        return BatchRemovalResult(spots=names, stats=stats)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _process_document(
    pdf: pikepdf.Pdf,
    targets: frozenset[str],
    *,
    apply: bool,
    stats: RemovalStats,
) -> None:
    context = _ProcessingContext(pdf=pdf, targets=targets, apply=apply, stats=stats)
    for page_number, page in enumerate(pdf.pages, start=1):
        resources = page.Resources
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
            GraphicsState(),
            f"page {page_number}",
        )
        if result.changed or _change_counter(stats) != changes_before:
            stats.pages_changed.add(page_number)
            if apply:
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
    initial_state: GraphicsState,
    label: str,
    form_depth: int = 0,
):
    def handle_form(form: Any, inherited_state: GraphicsState) -> None:
        _process_form(
            context,
            form,
            resources,
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
    inherited_state: GraphicsState,
    parent_label: str,
    form_depth: int,
) -> None:
    if form_depth > MAX_FORM_NESTING:
        raise InvalidPdfError(
            f"{parent_label}: Form nesting exceeds the supported limit of {MAX_FORM_NESTING}"
        )
    form_key = object_key(form)
    resources = form.get(pikepdf.Name.Resources, parent_resources)
    resource_key = object_key(resources)
    signature = (
        resource_key,
        inherited_state.nonstroking.contains_any(context.targets),
        inherited_state.stroking.contains_any(context.targets),
        inherited_state.text_render_mode,
    )
    previous = context.processed_forms.get(form_key)
    if previous is not None:
        if previous != signature:
            raise UnsupportedSpotUseError(
                f"{parent_label}: a shared Form requires context-dependent rewriting"
            )
        return
    if form_key in context.processing_forms:
        raise UnsupportedSpotUseError(f"{parent_label}: cyclic Form XObjects are not supported")

    context.processing_forms.add(form_key)
    try:
        instructions = pikepdf.parse_content_stream(form)
        label = f"{parent_label} Form {form_key}"
        result = _process_stream(
            context,
            instructions,
            resources,
            inherited_state,
            label,
            form_depth,
        )
        if result.changed:
            context.stats.forms_changed += 1
            if context.apply:
                if _contains_inline_image(instructions):
                    raise UnsupportedSpotUseError(
                        f"{label}: rewriting a stream with inline images is not supported"
                    )
                form.write(pikepdf.unparse_content_stream(result.instructions))
        context.processed_forms[form_key] = signature
    finally:
        context.processing_forms.remove(form_key)


def _contains_inline_image(instructions: list[Any]) -> bool:
    return any(not hasattr(item, "operator") for item in instructions)


def _change_counter(stats: RemovalStats) -> tuple[int, ...]:
    return (
        stats.forms_changed,
        stats.text_blocks,
        stats.text_show_operations,
        stats.fills_removed,
        stats.strokes_removed,
    )


def _open_strict(path: Path) -> pikepdf.Pdf:
    try:
        pdf = pikepdf.open(
            path,
            attempt_recovery=False,
            suppress_warnings=False,
            inherit_page_attributes=True,
        )
    except (pikepdf.PdfError, pikepdf.PasswordError) as error:
        raise InvalidPdfError(f"cannot open PDF safely: {error}") from error
    syntax_errors = pdf.check_pdf_syntax()
    warnings = pdf.get_warnings()
    if syntax_errors or warnings:
        pdf.close()
        details = "; ".join(str(item) for item in [*syntax_errors, *warnings])
        raise InvalidPdfError(f"PDF syntax warnings are not accepted: {details}")
    return pdf


def _verify_saved_pdf(
    path: Path,
    targets: frozenset[str],
    *,
    require_no_all_mode_targets: bool,
) -> None:
    with _open_strict(path) as pdf:
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


def _temporary_output_path(output_path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{output_path.stem}-", suffix=".tmp.pdf", dir=output_path.parent
    )
    os.close(descriptor)
    return Path(name)


def _output_path_without_final_symlink_resolution(path: Path) -> Path:
    """Resolve the parent while preserving the final path component verbatim."""

    if path.name in {"", ".", ".."}:
        raise InvalidPdfError(f"output must name a PDF file: {path}")
    return path.parent.resolve() / path.name
