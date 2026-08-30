# PDF compatibility

The guiding rule is conservative: a selected spot is changed only when every
reachable selected use and exact-name dependency can be handled without
guessing at PDF semantics.

## Supported rename

Rename aliases one exact spot-plate name to another:

```console
spotpdf rename input.pdf --spot "Old Name" --to "New Name" -o output.pdf
```

PDF Names are atomic and case-sensitive. `Old Name`, `old name`, and
`Old#20Name` are different names; no case folding or Unicode normalization is
performed. Spaces, `#`, `/`, and UTF-8 text are accepted as decoded command-line
names and escaped correctly when the PDF is written.

The source must be an unambiguous spot color and the destination must not
already occur as a colorant or exact-name dependency. Process components,
names used as both process and spot, canonical process names, and the reserved
names `/All` and `/None` are rejected as sources and destinations. A missing
source is an error rather than a copy operation, and rename never merges two
plates implicitly. The source must include at least one reachable true
`/Separation` definition. A DeviceN-only source is rejected; supported
DeviceN/NChannel occurrences are renamed only alongside that Separation.

One rename transaction can update all structurally consistent occurrences in:

- named `/Separation` definitions;
- role-aware DeviceN/NChannel spot-component arrays;
- matching `/Colorants` dictionary keys and their nested Separation names;
- `/MixingHints /Solidities` and `/DotGain` keys and `/PrintingOrder` arrays
  attached to a DeviceN occurrence that also declares the source component or
  supplies its matching `/Colorants` Separation;
- page `/SeparationInfo /DeviceColorant`, preserving whether it was represented
  as a PDF Name or string; and
- `/Colorants` dictionaries in every normal (`/AP /N`) PrinterMark appearance.

The command replaces names in place. Component order, alternate color spaces,
tint-transform objects, tint values, content-stream operands, and resource
aliases remain unchanged, so this operation is plate aliasing rather than color
conversion. The saved PDF is reopened and inventoried before atomic
publication; an existing `--force` destination remains unchanged if any
preflight, write, or verification step fails.

Some PDF/A producers use a valid XMP packet serialization that pikepdf rewrites
while saving, for example changing packet quote delimiters, line endings, or an
empty `begin` marker to U+FEFF. Rename accepts only those storage-equivalent
changes. The XML root, namespace bindings, comments, internal processing
instructions, packet identity, writable/read-only marker, and all RDF values
must remain equal. Malformed or DTD-bearing metadata is compared as raw decoded
bytes and therefore cannot use this normalization.

These semantics follow ISO 32000-1 sections 7.3.5, 8.6.6.4–5, and 14.11.3–4 in
the [official Adobe-hosted ISO 32000-1:2008 copy](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf).

## Rename fail-closed cases

Rename publishes no output when a source or destination participates in a
structure whose plate-name semantics cannot be updated completely. The initial
supported subset rejects:

- TrapNet annotations and `/SeparationColorNames`; trap networks are cached
  production data with their own validity state;
- type 5 halftone colorant keys;
- OPI dictionaries and external-image `/Inks` declarations;
- PrinterMark rollover or down appearances (`/AP /R` and `/AP /D`);
- mismatched `/Colorants` keys and nested Separation names; target-related
  invalid types, required fields, component dimensions, and name relationships
  in DeviceN/NChannel, Process, MixingHints, and SeparationInfo structures;
- DotGain values that cannot be verified as one-input/one-output PDF function
  types 0, 2, 3, or 4;
- extra `/MixingHints`-only colorants when that DeviceN occurrence neither
  declares the source component nor supplies a matching `/Colorants`
  Separation;
- other uncovered exact-name references; and
- encryption, modification restrictions, signatures, parser-detectable syntax
  errors or warnings, output symlinks, and in-place input/output paths.

This is a target-specific mutation preflight, not a complete ISO/PDF
conformance validator. Unrelated structures are not exhaustively schema-checked.

These refusals are intentional. Renaming only the visible Separation array
while leaving one prepress reference under the old name could split or merge
plates in downstream production.

## Supported alternate-preview changes

`set-alternate` replaces the composite fallback for one exact, case-sensitive
spot plate without converting that plate to process color:

```console
spotpdf set-alternate input.pdf --spot "Varnish" --cmyk 0,80,100,0 -o output.pdf
```

The CMYK components are four finite percentages in the inclusive range 0–100.
They are serialized with PDF number precision, so endpoint-adjacent values can
round to 0 or 100; the command reports the values actually stored.
Every reachable matching Separation is changed to this shape:

```text
[/Separation /Varnish /DeviceCMYK <<
  /FunctionType 2
  /Domain [0 1]
  /Range [0 1 0 1 0 1 0 1]
  /C0 [0 0 0 0]
  /C1 [0 0.8 1 0]
  /N 1
>>]
```

This linear tint transform maps tint `0` to no process ink and tint `1` to the
requested CMYK fallback. The Separation name, array identity, resource aliases,
page/Form streams, and existing tint operands are unchanged. Multiple target
definitions share one newly created indirect function; an old function shared
with an unrelated color is never mutated in place.

The target must be an unambiguous spot with at least one reachable Separation.
The reserved names `/All` and `/None`, canonical process names, arbitrary
NChannel process components, mixed process/spot roles, absent names, and every
target-related DeviceN/NChannel occurrence are rejected. Unrelated DeviceN
spaces do not block the command. Matching remains exact and case-sensitive, so
a true custom spot named lowercase `/black` is distinct from process `/Black`.
Target definitions embedded directly inside inline-image dictionaries fail
closed because changing them would require rewriting content streams. An inline
image may safely use a resource alias that resolves to a planned Separation.

Malformed target-bearing Separation or DeviceN name fields, invalid Separation
arrays, signatures, encryption, modification restrictions, parser warnings,
in-place paths, symlinks, and hard-link aliases all fail without publishing an
output. Before atomic replacement, the temporary PDF is reopened strictly and
checked for the exact function, unchanged inventory/content streams, and no
semantic graph changes outside the planned alternate/tint slots.

The Separation array and tint-function semantics follow ISO 32000-1 sections
8.6.6.4 and 7.10.3 in the
[official Adobe-hosted specification](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf).

## Supported removal

- Exact, case-sensitive named `/Separation` resources.
- Nonstroking fills and stroking paths.
- Combined fill-and-stroke operators while retaining the non-target paint.
- Text show operators when deleting or reducing paint does not require glyph
  widths or text-position reconstruction.
- Nested Form XObjects with resolvable resources and one consistent caller
  state.
- Balanced `q`/`Q` graphics-state scopes and pending clipping paths.

## Read-only inventory

`list` discovers reachable `/Separation`, `/DeviceN`/NChannel, and page
`/SeparationInfo` colorant names. `check --spot` is narrower: it reports only
spot/removal candidates and legacy standalone Separation targets, not
process-only DeviceN components. Discovery does not imply that removal is
supported. The `ROLE` column distinguishes spot, process, `/All`, and `/None`;
the `STATUS` column records removal-preflight context. `rename` and
`set-alternate` run their own target-specific structural and hazard preflights
after inventory.

Inventory scans removal hazards once and interprets every reached page or
compatible Form content stream once, collecting independent counters for all
active colorants. One colorant's first unsupported use freezes only that
colorant; already completed pages and paint counters remain visible while other
colorants continue. A compatible shared Form contributes paint operations once
and appears in every page that invokes it. Direct Form resource dictionaries use
a stable owner-bound identity, so repeated inspection does not depend on
temporary pikepdf wrapper objects. The reproducible work and memory contract is
in [performance.md](performance.md).

Only page-tree `/Parent` back-links are treated as redundant during semantic
inventory. A `/Parent` key in any non-page dictionary is still traversed and
can contribute named-color declarations or dependencies.

For NChannel spaces, names in `/Process /Components` are classified as process
components even when they are arbitrary. Canonical `Cyan`, `Magenta`, `Yellow`,
and `Black` components are automatically process only for a CMYK NChannel
process space (`DeviceCMYK` or four-component `ICCBased`). In an RGB or Lab
NChannel space, a canonical CMYK name not listed in `/Process /Components` is a
spot. Legacy non-NChannel DeviceN spaces retain the conservative canonical-name
classification. Nested individual `/Colorants` Separation definitions are
inventoried, but a redundant definition for a declared process component does
not turn that component into a spot.

The programmatic report records each Separation/DeviceN definition with an
indirect object number or deterministic direct-object path and all discovered
human-readable locations. It also records exact-name dependencies from
`/Process /Components`, `/Colorants`, `/MixingHints` (`/Solidities`, `/DotGain`,
and `/PrintingOrder`), page `/SeparationInfo`, printer-mark Form `/Colorants`,
and TrapNet `/SeparationColorNames`. The `Default` MixingHints entry is not a
colorant.

These rules follow the DeviceN attributes and process-component semantics in
ISO 32000-1 section 8.6.6.5 and the pre-separated page model in section 14.11.4
of the [official Adobe-hosted specification](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf).

Signed PDFs may be inventoried, but are never rewritten because a full save
would invalidate signatures.

## Fail-closed cases

Removal does not publish output when a selected color occurs in:

- DeviceN or NChannel color spaces;
- pre-separated page colorants declared through `/SeparationInfo`;
- supported exact-name prepress dependencies such as NChannel attributes,
  printer marks, and trap networks;
- raster or inline images;
- colored or uncolored patterns;
- shadings;
- Type 3 font programs;
- annotation appearances;
- soft masks;
- clipping text;
- image masks painted through an inherited selected color;
- Forms that require context-dependent rewriting.

An inline image anywhere in a page stream blocks removal for every selected
spot resource declared by that page, even when the alias is not painted. A Form
containing an inline image also fails during the dry run when its supported
rewrite would otherwise change that stream. This conservative rule prevents
unsafe content-stream reserialization.

Malformed content, unknown resources, cyclic Forms, Forms nested deeper than 64
levels, encryption, modification restrictions, and signatures also block
removal. Output symlinks, including dangling symlinks, are rejected rather
than followed or replaced.

For a new destination, publication uses an operating-system no-clobber
primitive. On POSIX this requires hard-link support in the destination file
system; an unsupported file system fails closed without publishing output.

## Process and reserved names

`remove --all` preserves NChannel process components and Separation colorants
named `Cyan`, `Magenta`, `Yellow`, and `Black`. It also preserves the special
PDF names `All` and `None`. The match is exact and case-sensitive. If one name
is used as both process and spot, automatic removal preserves it rather than
guessing which plate was intended.

`remove --spot NAME` is explicit: it can remove a Separation named `Black`, but
still refuses the reserved names `All` and `None`.

## Viewer differences

Browser or Poppler screenshots are useful visual regressions, not a complete
prepress proof. Spot plates, overprint, alternate color spaces, transparency,
and color management can render differently across engines. Structural
inventory and syntax checks remain part of every acceptance test.

## Test data policy

The committed suite generates minimal PDFs at runtime with pikepdf. The README
demo is generated by project-owned code. No customer PDFs or third-party PDF
fixtures are committed.

The release-only [public corpus gate](public-corpus.md) downloads six
commit-pinned, SHA-256-verified Ghostscript and veraPDF files into an ignored
cache, records their licenses and attribution, then checks them with qpdf,
Poppler, and Ghostscript `tiffsep`. A fixture is vendored only when its license
explicitly permits redistribution and a synthetic regression cannot cover the
same structure.
