# Visual diagnostic reports

Add `--report report.html` to `remove`, `rename`, `set-alternate`, or `convert`
to locate failures in the original PDF. This also works with `--dry-run`:

```sh
spotpdf remove input.pdf --spot Varnish --dry-run --report report.html
spotpdf convert input.pdf --spot Varnish --to-cmyk 0,80,0,0 \
  -o output.pdf --report report.html --format json
```

Open the HTML in a browser. It needs no server, network connection, or original
PDF: original-page PNG previews are embedded as Base64. Each preview is embedded
once and reused by its enlarged excerpts. The report includes the whole preview
pages, not just isolated objects. Its English interface provides a finding filter,
links between findings and numbered page markers, and expandable technical details.

## What a location means

A finding identifies its rule, explanation, selected spot names, original indirect
object number and generation where available, or a direct resource path. Paint
operations usually do not have their own indirect object number. Their provenance
uses the original content stream, zero-based operator index, and Form invocation
chain. `sequence_index` is zero-based within a page's combined content sequence;
`operator_index` is local to its individual source stream. Pages are one-based.
Repeated Form invocations have separate locations even when their stream is shared.

The report distinguishes these localization levels:

- **Object bounds:** a marked source operation is linked to PDFium geometry.
  Bounds are in original PDF page coordinates, before display rotation. They can
  include clipped, occluded, or transparent content; this is not a visible-pixel mask.
- **Surrounding area:** a containing region, such as an annotation rectangle.
- **Page only:** a known page without a reliable geometric correspondence.
- **Structure:** a resource or metadata location without a known visible placement.

Text, paths, images, and nested Form invocations receive source-operation markers
in a private diagnostic copy. PDFium reads those markers, and the report crops the
**original** page render. These previews are orientation aids, not color proofs.
Unmapped structures never receive guessed boxes. Before using operation geometry,
the worker compares original and diagnostic page renders; a mismatch suppresses
those boxes and records a coverage gap. Unknown content operators also stop
instrumentation rather than guessing their effect on subsequent graphics state.

## Completeness and execution outcome

The actual operation still stops when it cannot prove a safe rewrite. That failure
is labelled **Operation failure**. A separate read-only pass reuses the operation's
validators to find additional independent failures. A successful operation or dry
run instead reports its actual successful outcome.

Structural removal/conversion checks can collect multiple refusals. Additional
removal content checks continue on independent pages after a page fails. Detailed
planners first retain their actual refusal; additional rename/alternate checks then
continue across independent resources and conversion checks across independent pages,
when request validation permits it. Each resource/content sequence still stops at
its first refusal. Cross-page rewrite and post-save invariants are represented by
the actual operation result, not by speculative continued execution. These coverage
limits and any skipped areas are explicitly reported; a report is not a promise to
list every possible failure.

The report worker uses a private input snapshot and refuses localization if the
input changed since the operation. Strict input validation failures do not trigger
a diagnostic reopen or a renderer fallback. The private copy and intermediate
images are cleaned up after report publication.

## Limits and publication

PDFium (`pypdfium2`) and Pillow are installed with spotpdf. Without `--report`,
there is no renderer import, rendering, or additional diagnostic pass.

| Option | Default |
| --- | ---: |
| `--report-max-findings` | 1,000 |
| `--report-max-pages` | 100 |
| `--report-max-bytes` | 52,428,800 (50 MiB) |
| `--report-timeout` | 120 seconds |

Values must be positive integers. Preview images have a maximum edge of 1,600
pixels. Instrumentation additionally stops after 100,000 operators (or the lower
configured processing limit), 64 nested Forms, or 10,000 objects in one resource
provenance walk. Existing input processing budgets still apply. Report limits can
omit images, findings, or remaining locations and are disclosed in the report.
If even the minimal technical HTML cannot fit the byte limit, report creation fails.

The worker runs in a separate, killable process. On timeout or a worker failure,
the CLI publishes the latest technical checkpoint with an incomplete-coverage
notice, without unfinished previews. No external assets or scripts are loaded;
PDF-controlled text is escaped as text.

Reports are published atomically. Existing reports require `--report-overwrite`;
`--force` applies only to the PDF output. Input/output aliases, including hard links,
and symbolic-link report destinations are rejected before execution.

A report failure never replaces the original operation failure. If the operation
succeeded but the requested report could not be written, the command returns `1`
and states whether the output PDF was already published. A usable partial report
does not turn a successful operation into a failure. See the
[JSON contract](json-output.md#diagnostic-report-results) for machine-readable status.
