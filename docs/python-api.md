# Python API

> [!NOTE]
> This guide documents the package-root API on `main`, first shipping in
> v0.7.0. Stable v0.6.0 does not expose these operations from `spotpdf` yet.

`spotpdf` exposes a small synchronous Python API for the same inspected and
verified operations as the CLI. Import supported operations, result records,
limits, and controlled processing errors from the package root:

```python
from spotpdf import (
    ProcessingLimits,
    SpotPdfError,
    inspect_pdf,
    remove_all_spots,
)
```

The names in `spotpdf.__all__` are the canonical compatibility surface. New
code does not need to import implementation modules such as `spotpdf.document`
or `spotpdf.convert`. Planner, parser, resource, fingerprint, pikepdf, and
CLI-output helpers are deliberately not root exports.

## Operations and results

Every path accepts either `str` or `os.PathLike[str]`, including
`pathlib.Path`. Matching of spot names is exact and case-sensitive.

| Operation | Result | Purpose |
| --- | --- | --- |
| `inspect_pdf(path, *, limits=...)` | `InspectionReport` | Inventory reachable named colorants and supported paint use. |
| `check_spot(path, spot, *, limits=...)` | `bool` | Test whether one exact name is a reachable spot/removal candidate. |
| `remove_spot(input_path, output_path, spot, *, force=False, limits=...)` | `RemovalStats` | Remove supported paint for one exact name. |
| `remove_all_spots(input_path, output_path, *, force=False, limits=...)` | `BatchRemovalResult` | Remove every supported spot while preserving process and special colorants. |
| `rename_spot(input_path, output_path, source, destination, *, force=False, limits=...)` | `RenameResult` | Rename one supported plate and its supported exact-name dependencies. |
| `set_alternate_cmyk(input_path, output_path, spot, cmyk, *, force=False, limits=...)` | `AlternateResult` | Replace only a Separation's composite CMYK preview. |
| `convert_spot_to_cmyk(input_path, output_path, spot, cmyk, *, force=False, limits=...)` | `ConversionResult` | Replace supported Separation paint with an explicit CMYK recipe. |

The four CMYK values are percentages from 0 through 100. They may be integers
or finite numeric values; `spotpdf` validates and reports the values that can
actually be stored in the PDF.

Read a report without opening pikepdf objects yourself:

```python
from spotpdf import ColorantRole, inspect_pdf

report = inspect_pdf("input.pdf")
for name, summary in sorted(report.colorants.items()):
    roles = ", ".join(sorted(role.value for role in summary.roles))
    pages = ", ".join(str(page) for page in sorted(summary.pages)) or "none"
    print(name, roles, pages, summary.paint_operations)

process_names = {
    name for name, summary in report.colorants.items() if ColorantRole.PROCESS in summary.roles
}
```

Apply separate atomic mutations to separate output files:

```python
from spotpdf import (
    convert_spot_to_cmyk,
    remove_spot,
    rename_spot,
    set_alternate_cmyk,
)

removed = remove_spot("input.pdf", "without-varnish.pdf", "Varnish")
renamed = rename_spot("input.pdf", "renamed.pdf", "Varnish", "Finish")
preview = set_alternate_cmyk(
    "input.pdf",
    "cyan-preview.pdf",
    "Varnish",
    (100, 0, 0, 0),
)
converted = convert_spot_to_cmyk(
    "input.pdf",
    "process.pdf",
    "Varnish",
    (0, 80, 100, 0),
)
```

Each mutation uses a private candidate, strictly reopens and verifies it, and
only then publishes the destination atomically. The input and output must be
different. An existing destination requires `force=True`; a handled failure
does not replace it. If one requested name is absent, `remove_spot` publishes
an unchanged copy and returns zeroed `RemovalStats`.

## Result records

The canonical result and inventory types are:

- `InspectionReport` and its `SpotSummary` values;
- `RemovalStats` and `BatchRemovalResult`;
- `RenameResult`;
- `AlternateResult`; and
- `ConversionResult`.

`SpotKind` describes where a named colorant was declared. `ColorantRole`
distinguishes spot, process, `/All`, and `/None` semantics. The inventory and
single-removal records contain mutable dictionaries or sets because they are
assembled during one inspection. Treat returned values as snapshots; mutating
them never changes the source PDF. The batch, rename, alternate, and conversion
wrappers are frozen dataclasses, although `BatchRemovalResult.stats` refers to
the mutable `RemovalStats` snapshot.

The advanced `InspectionReport.definitions` and `.dependencies` collections
remain available for diagnostics. Their nested PDF-location helper classes are
not package-root imports and are not required for ordinary inspection or
mutation workflows.

## Controlled errors

Catch `SpotPdfError` for anticipated spotpdf processing failures, or catch one
of its public specializations when recovery differs:

```text
SpotPdfError
├── InvalidPdfError
│   └── NestingLimitExceededError
├── UnsupportedSpotUseError
└── ProcessingBudgetExceeded
```

- `InvalidPdfError` covers unsafe input, invalid requests, invalid CMYK values,
  output collisions, and PDFs that cannot be opened and validated strictly.
- `UnsupportedSpotUseError` means the requested change cannot preserve the
  semantics of a reached PDF construct within the supported subset.
- `NestingLimitExceededError` is the specific invalid-input error for fixed
  recursive-structure limits.
- `ProcessingBudgetExceeded` exposes `metric`, `field`, `observed`, `limit`,
  and `option` attributes. See [processing budgets](processing-budgets.md).

The controlled hierarchy does not replace Python's filesystem exceptions and
does not promise that every unexpected PDF-backend exception is converted.
Callers that own file access should handle `OSError` separately and treat any
other unexpected exception as a failed operation. Atomic publication still
protects an existing destination when processing fails.

## Typing and compatibility

The wheel and source archive include the PEP 561 `py.typed` marker, so type
checkers use the inline annotations. `ProcessingLimits` is frozen and every
operation accepts it only as the keyword-only `limits=` argument. Passing
`None` accidentally is rejected; individual fields can be set to `None` only
through an explicit `ProcessingLimits` instance for a trusted large job.

Within one released `0.x` minor line, patch releases do not remove or rename
root exports, change required parameters or defaults, change result-field
types, or change the controlled error inheritance. An intentional incompatible
change before 1.0 requires a new minor release and a documented migration;
after 1.0 it requires a major release. Adding a new optional keyword or a new
root export remains an additive change.
