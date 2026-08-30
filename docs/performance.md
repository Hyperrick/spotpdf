# Inventory performance

`spotpdf list` has a deterministic single-pass work contract. Named-color
declarations are discovered once, removal hazards are attributed in one
resource traversal, and each reached page or compatible Form content stream is
interpreted once regardless of how many spot colors the PDF declares.

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

## Reference measurement

This local before/after measurement used Python 3.13.12, pikepdf 10.12.0, and
macOS arm64 on 2026-08-30. Each value is the median of nine warmed runs.

| Spots | Version | Stream parses | Time | Python heap peak |
| ---: | --- | ---: | ---: | ---: |
| 64 | pre-single-pass baseline | 1,024 | 175 ms | 845,015 B |
| 64 | single-pass | 16 | 16.5 ms | 836,275 B |
| 128 | pre-single-pass baseline | 2,048 | 570 ms | 1,472,869 B |
| 128 | single-pass | 16 | 29.1 ms | 1,462,279 B |

The structural change removes the `colorants × streams` parse multiplier. The
representative run was about 10 times faster for 64 spots and 18 times faster
for 128 spots, while measured Python heap peaks stayed effectively unchanged.
Exact timings vary by machine and PDF compression.

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
