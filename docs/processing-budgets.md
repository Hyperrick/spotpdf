# Processing budgets

Every input-processing `spotpdf` subcommand applies finite, deterministic work
ceilings to one input PDF before read-only analysis continues or an in-memory
mutation begins.
The limits are inclusive: a measured value equal to its limit is accepted; the
first value above it stops the command.

## Defaults

| Resource | CLI option | `ProcessingLimits` field | Default |
| --- | --- | --- | ---: |
| Input file size | `--max-input-bytes` | `max_input_bytes` | 805,306,368 bytes (768 MiB) |
| Pages | `--max-pages` | `max_pages` | 10,000 |
| Reachable graph entries | `--max-reachable-objects` | `max_reachable_objects` | 1,000,000 |
| Decoded page/Form content | `--max-decoded-content-bytes` | `max_decoded_content_bytes` | 268,435,456 bytes (256 MiB) |
| Content operators | `--max-operators` | `max_operators` | 5,000,000 |

The file and page defaults leave headroom above two KDP reference ceilings and
guidance pages: one documents a 650 MB manuscript ceiling and the other discusses
files up to 8,000 pages. They are reference points, not PDF-format limits or a
claim that every such manuscript is supported. The graph, decoded-content, and
operator values are conservative application engineering ceilings. They may be
revised in a future release if public, reproducible prepress fixtures show that
legitimate work needs different defaults.

- [KDP manuscript file-size limits](https://kdp.amazon.com/en_US/help/topic/G202145060)
- [KDP large-manuscript guidance](https://kdp.amazon.com/en_US/help/topic/G200735140)

## What is counted

The checks run in a fixed order so a PDF that exceeds several limits reports the
same first failure on every supported platform:

1. filesystem input bytes, before `pikepdf.open()`;
2. a non-recovering open and any immediate parser warnings;
3. page count;
4. the reachable object graph;
5. decoded page/Form content bytes;
6. content operators; and
7. qpdf's full syntax check and final warnings.

`reachable graph entries` is a bounded graph-work measure, not the number of
unique indirect object numbers. It charges one entry for the trailer root and
one for every Array item or Dictionary/Stream value reached from it. Alias edges
each count, while a shared target container is expanded only once. Traversal is
iterative and stops without first building a complete edge list for a large
container.

Decoded content includes every page `/Contents` stream and every reachable Form
XObject stream. A page content stream shared by multiple pages counts once per
page because it is processed in each page context; a shared Form is counted
once. Inline-image data inside those streams is included. The counter excludes
raster image streams, ICC profiles, attachments, metadata, tint functions, and
other non-content streams.

For a page whose `/Contents` is an Array, decoded bytes are the sum of its
individual stream entries after qpdf applies its generalized and supported
non-lossy specialized filters. Operator tokenization treats the Array as one
logical sequence, so an operand/operator sequence may
cross a stream boundary.

The operator counter streams over every lexical PDF content operator word in
those page and Form streams, including unknown words. The `BI`, `ID`, and `EI`
tokens of an inline-image sequence each count. It does not count only paint
operators: state, path-construction, text, and compatibility operators consume
the same budget.

One fresh counter set is used per public library call. Internal mutation
planning, dry runs, apply passes, and saved-output verification do not
accumulate against the source limits a second time. This keeps the contract
independent of the fixed number of verification passes. Saved output is not a
second independently budgeted input; use an external output/temp-disk ceiling
where that distinction matters.

## CLI overrides

Every subcommand accepts the same five options. Values must be positive decimal
integers. There is intentionally no blanket `--no-limits` switch.

```bash
spotpdf remove large-trusted-job.pdf --all -o clean.pdf \
  --max-input-bytes 1073741824 \
  --max-pages 20000 \
  --max-operators 10000000
```

Raise only the limit demonstrated by a trusted job. A budget failure exits with
code `1` and identifies both the observed value and the matching option:

```text
spotpdf: error: processing budget exceeded: pages 10001 > 10000 (raise this limit with --max-pages or ProcessingLimits(max_pages=...) for a trusted large job)
```

With `--format json`, the same failure uses
`error.code: "budget_exceeded"` and provides stable `metric`, `field`,
`observed`, `limit`, and `option` values in `error.details`. See the
[JSON output contract](json-output.md).

No output is published. An existing destination supplied with `--force`
remains byte-for-byte unchanged, and no private temporary candidate remains.

## Library configuration

`ProcessingLimits` is immutable and accepted as the keyword-only `limits=`
argument by `inspect_pdf`, `check_spot`, `remove_spot`, `remove_all_spots`,
`rename_spot`, `set_alternate_cmyk`, and `convert_spot_to_cmyk`.
`DEFAULT_PROCESSING_LIMITS` is the exported immutable instance used when the
argument is omitted.

```python
from pathlib import Path

from spotpdf import ProcessingLimits, inspect_pdf

limits = ProcessingLimits(
    max_input_bytes=1_073_741_824,
    max_pages=20_000,
)
report = inspect_pdf(Path("trusted-large-job.pdf"), limits=limits)
```

Catch `ProcessingBudgetExceeded` when a caller needs structured handling. It
exposes stable `metric`, `field`, `observed`, `limit`, and `option` attributes:

```python
from spotpdf import ProcessingBudgetExceeded, inspect_pdf

try:
    report = inspect_pdf(Path("input.pdf"), limits=limits)
except ProcessingBudgetExceeded as error:
    print(error.field, error.observed, error.limit)
```

Library callers may set an individual field to `None` to disable that one
application limit. CLI users must always supply a positive value. Disabling or
raising an application ceiling does not alter PDF compatibility checks and does
not relax qpdf or operating-system controls. See the complete
[Python API](python-api.md) for canonical imports, results, and controlled
exceptions.

## What these budgets cannot guarantee

The budgets are application-level refusal points, not hard CPU or memory
quotas. qpdf must parse enough structure to expose the PDF graph. A single
stream's supported non-lossy content filters are decoded by native code before
`spotpdf` can observe its decoded length. qpdf's later syntax check also examines
streams outside the page/Form content scope. Temporary output size is not capped by the input-byte
setting.

For attacker-controlled or multi-tenant input, use an unprivileged isolated
process with independent CPU, address-space/RAM, wall-clock, output/temp-disk,
process, descriptor, and filesystem-permission limits. See the
[security policy](../SECURITY.md) for the three separate protection layers and
the [architecture](architecture.md) for preflight placement.
