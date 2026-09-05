"""Instrument a private PDF copy with original stream/operator provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pikepdf

from .content_support import instruction, operator_name
from .convert_operators import STANDARD_CONTENT_OPERATORS
from .diagnostics import identity
from .objects import object_key

PAINT = frozenset(
    {
        "S",
        "s",
        "f",
        "F",
        "f*",
        "B",
        "B*",
        "b",
        "b*",
        "Tj",
        "TJ",
        "'",
        '"',
        "Do",
        "sh",
        "INLINE IMAGE",
    }
)


@dataclass
class Trace:
    tag_key: str = field(default_factory=lambda: "D" + uuid4().hex)
    entries: dict[int, dict[str, Any]] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    max_operations: int = 100000
    visited: int = 0

    def instrument(self, pdf: pikepdf.Pdf, pages: list[int]) -> None:
        for number in pages:
            page = pdf.pages[number - 1]
            resources = pikepdf.Dictionary(page.obj.get("/Resources", {}))
            page.obj.Resources = resources
            contents = page.obj.get("/Contents", [])
            streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
            state: dict[str, list[str]] = {}
            stack: list[dict[str, list[str]]] = []
            output = []
            offset = 0
            for stream in streams:
                output.append(
                    self._stream(pdf, stream, resources, number, [], set(), state, stack, offset)
                )
                offset += len(pikepdf.parse_content_stream(stream))
            page.obj.Contents = pikepdf.Array(output)

    def _references(self, value: Any, path: str) -> list[str]:
        """Retain resource identity and direct paths, without following page back-links."""
        found = [path]
        seen = set()
        pending = [(value, path)]
        while pending:
            item, location = pending.pop()
            if not isinstance(item, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
                continue
            key = object_key(item)
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > 10000:
                raise ValueError("Resource provenance exceeds 10000 objects")
            found.append(location)
            if object_id := identity(item):
                found.append(object_id)
            if isinstance(item, pikepdf.Array):
                pending.extend((v, f"{location}[{i}]") for i, v in enumerate(item))
            else:
                pending.extend(
                    (v, location + str(k))
                    for k, v in item.items()
                    if str(k) not in {"/Parent", "/P", "/Resources"}
                )
        return sorted(set(found))

    def _stream(self, pdf, stream, resources, page, chain, ancestors, state, stack, offset=0):
        stream_id = identity(stream)
        root = f"page {page}" + "".join(f" Form {c['name']!r}" for c in chain)
        try:
            items = pikepdf.parse_content_stream(stream)
            result = []
            for index, item in enumerate(items):
                self.visited += 1
                if self.visited > self.max_operations:
                    raise ValueError("Diagnostic operator limit reached")
                op = operator_name(item)
                if op not in STANDARD_CONTENT_OPERATORS:
                    raise ValueError(f"Unknown operator {op!r}; geometry not inferred")
                operands = list(getattr(item, "operands", []))
                if op == "q":
                    stack.append(dict(state))
                elif op == "Q":
                    if not stack:
                        raise ValueError("Unbalanced graphics state")
                    state.clear()
                    state.update(stack.pop())
                category = {
                    "cs": "ColorSpace",
                    "CS": "ColorSpace",
                    "Tf": "Font",
                    "gs": "ExtGState",
                    "sh": "Shading",
                    "Do": "XObject",
                }.get(op)
                refs = []
                if category and operands:
                    name = str(operands[0])
                    value = resources.get("/" + category, {}).get(name)
                    refs = self._references(value, f"{root}/Resources/{category}{name}")
                    refs.append(f"{root}#instruction{offset + index}")
                    if op in {"cs", "CS", "Tf", "gs"}:
                        state[op] = refs
                if op == "Tr" and operands:
                    state["Tr"] = [str(operands[0])]
                if op in {"cs", "g", "rg", "k"}:
                    state.pop("scn", None)
                if op in {"CS", "G", "RG", "K"}:
                    state.pop("SCN", None)
                if op in {"g", "rg", "k"}:
                    state.pop("cs", None)
                if op in {"G", "RG", "K"}:
                    state.pop("CS", None)
                if op in {"scn", "SCN"} and operands and isinstance(operands[-1], pikepdf.Name):
                    name = str(operands[-1])
                    value = resources.get("/Pattern", {}).get(name)
                    state[op] = self._references(value, f"{root}/Resources/Pattern{name}")
                if op not in PAINT:
                    result.append(item)
                    continue
                # A form invocation has its own unique cloned resource and marked descendants.
                if op == "Do" and operands:
                    value = resources.get("/XObject", {}).get(str(operands[0]))
                    if isinstance(value, pikepdf.Stream) and value.get("/Subtype") == "/Form":
                        key = object_key(value)
                        if key in ancestors or len(chain) >= 64:
                            raise ValueError("Cyclic or excessively nested Form")
                        call = {
                            "name": str(operands[0])[1:],
                            "key": list(object_key(value)),
                            "object_id": identity(value),
                            "stream": stream_id,
                            "operator_index": index,
                        }
                        form_resources = pikepdf.Dictionary(value.get("/Resources", resources))
                        clone = self._stream(
                            pdf,
                            value,
                            form_resources,
                            page,
                            [*chain, call],
                            ancestors | {key},
                            dict(state),
                            [],
                        )
                        for k, v in value.items():
                            if str(k) not in {"/Length", "/Filter", "/DecodeParms", "/Resources"}:
                                clone[k] = v
                        clone.Resources = form_resources
                        xobjects = pikepdf.Dictionary(resources.get("/XObject", {}))
                        alias = f"/SpotpdfDiagnostic{len(self.entries)}_{index}"
                        while alias in xobjects:
                            alias += "_"
                        xobjects[alias] = clone
                        resources.XObject = xobjects
                        item = instruction("Do", pikepdf.Name(alias))
                mark = len(self.entries) + 1
                active = list(refs)
                for key, values in state.items():
                    if key == "Tr":
                        continue
                    if op == "Do" and key in {"cs", "CS", "scn", "SCN", "Tf"}:
                        target = resources.get("/XObject", {}).get(str(operands[0]))
                        if not (
                            isinstance(target, pikepdf.Stream)
                            and target.get("/ImageMask", False)
                            and key in {"cs", "scn"}
                        ):
                            continue
                    if op in {"Tj", "TJ", "'", '"'}:
                        mode = int(state.get("Tr", ["0"])[0])
                        if key in {"cs", "scn"} and mode not in {0, 2, 4, 6}:
                            continue
                        if key in {"CS", "SCN"} and mode not in {1, 2, 5, 6}:
                            continue
                    # Exclude opposite path color and irrelevant fonts from path provenance.
                    if key == "Tf" and op not in {"Tj", "TJ", "'", '"'}:
                        continue
                    if key in {"cs", "scn"} and op in {"S", "s"}:
                        continue
                    if key in {"CS", "SCN"} and op in {"f", "F", "f*"}:
                        continue
                    active.extend(values)
                active.extend(c["object_id"] for c in chain if c["object_id"])
                self.entries[mark] = {
                    "page": page,
                    "stream": stream_id,
                    "operator_index": index,
                    "sequence_index": index + offset,
                    "operator": op,
                    "form_chain": chain,
                    "location": root,
                    "legacy_location": f"page {page}"
                    + "".join(f" Form {tuple(c['key'])}" for c in chain),
                    "references": sorted(set(active)),
                }
                result.extend(
                    [
                        instruction(
                            "BDC",
                            pikepdf.Name("/SpotpdfDiagnostic"),
                            pikepdf.Dictionary({"/" + self.tag_key: mark}),
                        ),
                        item,
                        instruction("EMC"),
                    ]
                )
            return pdf.make_stream(pikepdf.unparse_content_stream(result))
        except (pikepdf.PdfError, ValueError, TypeError, AttributeError) as error:
            self.gaps.append(f"{root}, stream {stream_id}: {error}")
            # Never infer geometry from a stream whose instrumentation failed.
            return pdf.make_stream(stream.read_bytes())
