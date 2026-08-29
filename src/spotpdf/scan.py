"""Document-level safety checks for spot-color removal."""

from __future__ import annotations

from typing import Any

import pikepdf

from .colors import (
    SPECIAL_COLORANTS,
    discover_spot_declarations,
    parse_color_space,
    pdf_name,
    resolve_color_space,
)
from .model import (
    InspectionReport,
    InvalidPdfError,
    SpotKind,
    UnsupportedSpotUseError,
)
from .objects import ObjectTracker

MAX_FORM_NESTING = 64


def validate_document_for_change(pdf: pikepdf.Pdf, spot: str) -> None:
    """Reject an unsafe or unsupported exact-name removal."""

    validate_document_for_changes(pdf, frozenset({spot}))


def validate_document_for_changes(
    pdf: pikepdf.Pdf,
    spots: frozenset[str],
    *,
    declarations: InspectionReport | None = None,
) -> None:
    """Reject inputs whose selected spot colors cannot all be removed safely."""

    if pdf.is_encrypted:
        raise InvalidPdfError("encrypted PDFs are not supported")
    if not pdf.allow.modify_other:
        raise InvalidPdfError("the PDF permissions do not allow content modification")
    if _contains_signature(pdf.Root):
        raise InvalidPdfError(
            "signed PDFs are not modified because rewriting invalidates signatures"
        )
    reserved = spots & SPECIAL_COLORANTS
    if reserved:
        names = ", ".join(repr(name) for name in sorted(reserved))
        raise InvalidPdfError(f"{names} are reserved PDF separation names")

    validate_spot_uses_for_removal(pdf, spots, declarations=declarations)


def validate_spot_uses_for_removal(
    pdf: pikepdf.Pdf,
    spots: frozenset[str],
    *,
    declarations: InspectionReport | None = None,
) -> None:
    """Reject unsupported target uses without applying mutation restrictions."""

    report = declarations or discover_spot_declarations(pdf)
    devicen_targets = sorted(
        name
        for name in spots
        if name in report.spots and SpotKind.DEVICEN in report.spots[name].kinds
    )
    if devicen_targets:
        names = ", ".join(repr(name) for name in devicen_targets)
        raise UnsupportedSpotUseError(
            f"document: reachable DeviceN declarations contain target spot colors: {names}"
        )

    seen_forms = ObjectTracker()
    for page_number, page in enumerate(pdf.pages, start=1):
        annotations = page.obj.get(pikepdf.Name.Annots, None)
        if annotations is not None and _subtree_contains_spots(annotations, spots, ObjectTracker()):
            raise UnsupportedSpotUseError(
                f"page {page_number}: spot color in annotation appearances is not supported"
            )
        resources = page.obj.get(pikepdf.Name.Resources, pikepdf.Dictionary())
        _validate_resources(resources, spots, f"page {page_number}", seen_forms)


def _validate_resources(
    resources: Any,
    spots: frozenset[str],
    context: str,
    seen_forms: ObjectTracker,
    form_depth: int = 0,
) -> None:
    if not isinstance(resources, (pikepdf.Dictionary, pikepdf.Stream)):
        return

    color_spaces = resources.get(pikepdf.Name.ColorSpace, None)
    if isinstance(color_spaces, pikepdf.Dictionary):
        for value in color_spaces.values():
            info = parse_color_space(value)
            if info.kind is SpotKind.DEVICEN and info.contains_any(spots):
                raise UnsupportedSpotUseError(
                    f"{context}: DeviceN use of target spot colors is not supported"
                )
            if (
                isinstance(value, pikepdf.Array)
                and value
                and pdf_name(value[0]) == "Pattern"
                and len(value) > 1
                and _color_value_contains_spots(value[1], resources, spots)
            ):
                raise UnsupportedSpotUseError(
                    f"{context}: uncolored patterns based on target spots are not supported"
                )

    for category, label in (
        (pikepdf.Name.Shading, "shading"),
        (pikepdf.Name.Pattern, "pattern"),
    ):
        entries = resources.get(category, None)
        if not isinstance(entries, pikepdf.Dictionary):
            continue
        for name, value in entries.items():
            if _subtree_contains_spots(value, spots, ObjectTracker()):
                raise UnsupportedSpotUseError(
                    f"{context}: spot color in {label} {pdf_name(name)!r} is not supported"
                )

    fonts = resources.get(pikepdf.Name.Font, None)
    if isinstance(fonts, pikepdf.Dictionary):
        for name, font in fonts.items():
            subtype = pdf_name(font.get(pikepdf.Name.Subtype, pikepdf.Name("/Unknown")))
            if subtype == "Type3" and _subtree_contains_spots(font, spots, ObjectTracker()):
                raise UnsupportedSpotUseError(
                    f"{context}: spot color in Type3 font {pdf_name(name)!r} is not supported"
                )

    ext_gstates = resources.get(pikepdf.Name.ExtGState, None)
    if isinstance(ext_gstates, pikepdf.Dictionary):
        for name, state in ext_gstates.items():
            soft_mask = state.get(pikepdf.Name.SMask, None)
            if soft_mask is not None and _subtree_contains_spots(soft_mask, spots, ObjectTracker()):
                raise UnsupportedSpotUseError(
                    f"{context}: spot color in soft mask {pdf_name(name)!r} is not supported"
                )

    xobjects = resources.get(pikepdf.Name.XObject, None)
    if not isinstance(xobjects, pikepdf.Dictionary):
        return
    for name, xobject in xobjects.items():
        subtype = pdf_name(xobject.get(pikepdf.Name.Subtype, pikepdf.Name("/Unknown")))
        if subtype == "Image":
            color_space = xobject.get(pikepdf.Name.ColorSpace, None)
            if color_space is not None and _color_value_contains_spots(
                color_space, resources, spots
            ):
                raise UnsupportedSpotUseError(
                    f"{context}: spot-color image {pdf_name(name)!r} is not supported"
                )
            continue
        if subtype != "Form":
            continue
        if not seen_forms.visit(xobject):
            continue
        next_depth = form_depth + 1
        if next_depth > MAX_FORM_NESTING:
            raise InvalidPdfError(
                f"{context}: Form nesting exceeds the supported limit of {MAX_FORM_NESTING}"
            )
        form_resources = xobject.get(pikepdf.Name.Resources, resources)
        _validate_resources(
            form_resources,
            spots,
            f"{context} Form {pdf_name(name)!r}",
            seen_forms,
            next_depth,
        )


def _contains_signature(value: Any) -> bool:
    tracker = ObjectTracker()
    stack = [value]
    while stack:
        current = stack.pop()
        if not isinstance(current, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            continue
        if not tracker.visit(current):
            continue
        if isinstance(current, pikepdf.Array):
            stack.extend(current)
            continue
        if pikepdf.Name.ByteRange in current:
            return True
        if current.get(pikepdf.Name.Type, None) == pikepdf.Name.Sig:
            return True
        if current.get(pikepdf.Name.FT, None) == pikepdf.Name.Sig:
            return True
        stack.extend(current.values())
    return False


def _subtree_contains_spots(value: Any, spots: frozenset[str], tracker: ObjectTracker) -> bool:
    stack = [value]
    while stack:
        current = stack.pop()
        if not isinstance(current, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            continue
        if not tracker.visit(current):
            continue
        if parse_color_space(current).contains_any(spots):
            return True
        children = current if isinstance(current, pikepdf.Array) else current.values()
        stack.extend(children)
    return False


def _color_value_contains_spots(value: Any, resources: Any, spots: frozenset[str]) -> bool:
    tracker = ObjectTracker()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, pikepdf.Name):
            if resolve_color_space(resources, current).contains_any(spots):
                return True
            continue
        if not isinstance(current, pikepdf.Array) or not tracker.visit(current):
            continue
        if parse_color_space(current).contains_any(spots):
            return True
        stack.extend(current)
    return False
