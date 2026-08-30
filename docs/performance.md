# Inventory performance

`spotpdf list` has a deterministic single-pass semantic-inventory contract.
Named-color declarations are discovered once, removal hazards are attributed in
one resource traversal, and each reached page or compatible Form content stream
is semantically interpreted once regardless of how many spot colors the PDF
declares. Before that inventory, the processing-budget preflight performs a
graph traversal, one decoded-byte pass, and one streaming operator-token pass.

## Reproduce the benchmark

The benchmark creates its PDFs inside a temporary directory and deletes them
after the run. It commits neither generated PDFs nor customer data.

```bash
uv run python scripts/benchmark_inventory.py --runs 9 \
  --output tmp/inventory-benchmark.json
```

The fixture has eight pages and eight unique Forms. Half of each page's spots
are painted directly and half inside its Form. Every spot has exactly one paint
operation and one expected page. The benchmark instruments the real
`pikepdf.parse_content_stream` function as well as the scanner's internal work
counters.

| Spots | Resource contexts | Page streams | Form streams | Actual parses | Unique parse objects | Instructions |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 64 | 16 | 8 | 8 | 16 | 16 | 272 |
| 128 | 16 | 8 | 8 | 16 | 16 | 528 |

Every parse object must be visited exactly once. The page/Form and instruction
counts are blocking regression assertions in the unit suite. The Linux/Python
3.13 Quality job additionally verifies resource-context counts and the real
parse calls and object identities. It runs three samples and uploads
`inventory-benchmark-<commit SHA>` as a JSON artifact retained for 14 days.
Wall-clock time is reported but is not used as a cross-platform CI gate.
Separate deterministic unit fixtures double unique page/Form graphs and shared
hazard aliases from 64 to 128 and reject super-linear traversal growth.

These counters, timings, and `tracemalloc` samples diagnose inventory
complexity; they are not runtime CPU or memory quotas. The independent
[processing budgets](processing-budgets.md) provide deterministic source
refusal points, while native-process isolation remains an operating-system
concern.

## Reference measurement

This local before/after measurement used Python 3.13.12, pikepdf 10.12.0, and
macOS arm64 on 2026-08-30. Each value is the median of nine warmed runs.

| Spots | Version | Semantic `parse_content_stream` calls | Time | Python heap peak |
| ---: | --- | ---: | ---: | ---: |
| 64 | pre-single-pass baseline | 1,024 | 175 ms | 845,015 B |
| 64 | single-pass plus budget preflight | 16 | 19.9 ms | 834,349 B |
| 128 | pre-single-pass baseline | 2,048 | 570 ms | 1,472,869 B |
| 128 | single-pass plus budget preflight | 16 | 35.2 ms | 1,458,469 B |

The structural change removes the `colorants × streams` parse multiplier. The
representative run was about 8.8 times faster for 64 spots and 16.2 times faster
for 128 spots, while measured Python heap peaks stayed effectively unchanged.
The current timing includes all three budget-preflight phases; the
`actual_parse_calls` metric counts only semantic inventory parses. Exact timings
vary by machine and PDF compression.

The script separately samples timing and `tracemalloc` to avoid tracing
overhead in the timing result. Its deliberately generous growth guard requires:

```text
peak_128 <= 2.25 * peak_64 + 256 KiB
```

This catches an asymptotic Python-heap regression without treating
platform-specific allocator noise as a release failure. The JSON output
includes every raw sample, runtime version, actual parse count, unique
parse-object count, resource and content structural counters, and check result
for review.
