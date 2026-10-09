# T39 clear routing cases

`clear_cases_v1.json` contains 36 synthetic, source-reviewable task labels: six
per demo capability, each with three `calibration` and three held-out `test`
cases. It contains logical `runtime_ref` targets rather than invented UUIDs.
`build_clear_dataset()` binds those targets to the UUID/version/status returned
by `register_demo_catalog()` and validates the result with T38's
`RoutingDataset`. Its snapshot ID hashes the registered public metadata and
status. Re-registering the unchanged catalog against the same database retains
the mapping; a fresh database creates a different snapshot ID.

## Manual semantic review — 2026-10-08

- **Research (01–06):** Public documentation, specifications, release notes and
  technology comparisons all explicitly require external source lookup and
  citations. They do not request local implementation or inaccessible sources;
  successful lookup still depends on configured public web tools.
- **Code (01–06):** Supplied Python, JavaScript, SQL and TypeScript snippets ask
  for behavior analysis or minimal code corrections. No case assumes unseen
  repository access, test execution or verified changes.
- **Data (01–06):** Distinct supplied numeric/table tasks cover descriptive
  statistics, changes, missing/duplicate rows, outlier inspection and rates.
  No task requires loading an external dataset or audited figures.
- **Document (01–06):** Supplied runbook, specification, policy, note, checklist
  and API-guide excerpts ask for summary, extraction or factual comparison.
  None asks to fetch private documents or judge unsupported external claims.
- **Planning (01–06):** Future work is decomposed, ordered or prioritized using
  stated constraints. No case promises dates, allocates people or executes work.
- **Review (01–06):** Supplied designs, test plans, generated summaries and
  drafts are assessed for omissions, unsupported claims and quality risks, with
  actionable feedback. The aim is critique of an existing artifact.

Within each group, the six requests differ in artifact, operation or reasoning
required; none is a sentence-level rewrite of another. All task text and source
excerpts are synthetic. This T39 review records labels rather than measured
routing or task-quality results; live routing evidence appears in T43 below.

# T40 complete routing labels

`ambiguous_no_match_cases_v1.json` adds 12 overlapping-capability cases with
explicit acceptable `runtime_ref` sets and 12 out-of-catalog cases marked
`should_reject`. `build_full_dataset()` combines them with the 36 clear cases,
binds all accepted references to the same registered UUID/version snapshot, and
validates the 60-case `RoutingDataset`. Each category has an even
`calibration`/`test` split (18/18 clear, 6/6 ambiguous, 6/6 no-match). The T40
label artifact itself contains no routing decisions, timing or quality scores.

## Manual semantic review — 2026-10-08

- **Research + Document (2):** Supplied technical abstracts or public API notes
  permit synthesis of findings and factual comparison of supplied text. Neither
  case requires unavailable private retrieval or live web lookup.
- **Code + Review (2):** Each supplied snippet has a concrete unhandled input.
  Both implementation debugging and quality critique can answer the request.
- **Data + Document (2):** Supplied report or note figures can be compared as
  structured counts or extracted and compared as document claims. The text
  requests no statistical inference beyond the stated values.
- **Planning + Review (2):** Existing rollout steps have unsafe ordering.
  Dependency-based resequencing and critique of the current plan are both valid.
- **Document + Review (2):** Supplied drafts contain explicit conflicts;
  comparison and consistency review both identify the same contradiction.
- **Data + Review (2):** Supplied numeric counts contradict generated summaries.
  Recalculation and output-quality review both expose the incorrect claim.
- **No-match (12):** Calendar, email, SSO, deployment, finance, HR, CRM,
  procurement, building controls, image creation, audio transcription and active
  network scanning require an action, modality or system access absent from all
  six registered Agents. Their labels describe catalog mismatch, not provider,
  embedding or other infrastructure failure. Planning how to perform an action
  would not fulfill these direct execution requests.

Each overlap pair has one `calibration` and one held-out `test` case. The 24
added tasks differ in artifact and requested operation, and duplicate IDs/text
are rejected across the full dataset. This review establishes label intent,
not Router accuracy; the subsequent T43 measurements appear below.

# T41 routing benchmark runner

`run_routing_benchmark()` accepts a validated `RoutingDataset`, the matching
Registry, one split, a fixed Top-K and `CalibratedThreshold`, and either the
existing semantic Router or its candidate-only rerank path. It verifies the
current catalog UUID/version/status references before and after each case.
The returned `BenchmarkRun` is JSON-serializable: its config records the
catalog/dataset snapshot, split, embedding model key, K, threshold value and
source, strategy, optional rerank model key, and local environment. A stable
`config_id` connects each case row to that configuration.

Each row records the final `selected`, `rejected`, or `infra_error` status,
ordered semantic candidates and cosine scores, selected Agent or safe error
code, and successful rerank order/reason when applicable. The measured
`routing_latency_ms` starts immediately before query embedding and ends when
the Router returns, including reranking. If routing returns before query
embedding (for example, a pre-query failure or no eligible Agent), latency is
null with `timing_scope=query_not_started`; no duration is invented. Attachment
fixture cases are rejected until their contents can be
supplied explicitly. This runner does not start workflows or compute metrics.

The small offline fake fixture exercises these individual results:
`selected-case → selected`, `reject-case → rejected`, and
`error-case → infra_error`; a separate fake reranker can select the second
semantic candidate or retain `RERANK_TIMEOUT` as an infrastructure error.
These are deterministic test decisions, not measured catalog accuracy or
live-provider results. Latencies are measured afresh on every run and are not
published as a fixed fixture value.

# T42 routing metrics

`calculate_routing_metrics(dataset, run)` checks the catalog snapshot,
dataset version, selected split, config IDs, and exact case coverage before
counting. For clear and ambiguous cases, Top-1 uses the final selected Agent;
a valid rejection is incorrect. Top-K uses the **semantic candidate list**,
including under `semantic_llm`, and asks whether any acceptable Agent appears.
For no-match cases, a valid rejection is correct and selecting any Agent is a
false accept. Infrastructure-error cases appear in `infra_error_case_ids` and
are excluded from every quality-rate denominator. Each rate reports its
numerator, valid denominator, and value; an empty denominator produces null.

Latency uses every case with an actual query-to-decision measurement, including
an infrastructure error if its query embedding started. The summary lists
included error IDs and unmeasured IDs separately. Mean is the arithmetic
average; p50 and p95 use the nearest-rank rule on sorted values
(`ceil(percentile × sample_count)`). Each `BenchmarkRun` yields its own
strategy-labelled report. These routing rates do not measure execution success
or answer quality.

## Hand-counted test fixture

The eight synthetic cases contain five route-required cases (four valid, one
infrastructure error) and three no-match cases (two valid, one infrastructure
error). Among the four valid route-required cases, two final selections are
acceptable and three semantic Top-K lists contain an acceptable Agent:
Top-1 = 2/4, Top-K = 3/4. Among the two valid no-match cases, one is rejected
and one is falsely accepted: Reject Accuracy = 1/2 and False Accept Rate = 1/2.
Seven measured latencies are 10, 20, 30, 40, 50, 60, and 70 ms; their mean and
p50 are 40 ms, p95 is 70 ms. The 50 ms infrastructure-error sample is included
in latency only; the other error has no measured query and no invented duration.
These numbers verify formulas in a fixture, not a benchmark result for the demo
catalog or a live provider.

# T43 calibration and catalog-size evidence — 2026-10-08

## Provenance correction — 2026-10-09

The final M4 audit found an inaccurate `gate_selection_rule` label in the live
generator and both saved calibration artifacts (the final run and `attempt1`).
The label said to maximize Top-K Recall + Reject Accuracy, then Top-1. The
executed `select_calibration_config()` algorithm instead maximizes **Top-1
Accuracy + Reject Accuracy**, then Top-K Recall, then smaller K, then higher
threshold. This correction changes that label only; the selection algorithm
is unchanged.

Re-ranking each artifact's 183 saved **calibration** grid entries confirms
K=5 and threshold `0.35727615782291194`. The final calibration configuration ID
remains `eaf560784ee8eb9eb4d00f51bc200bc514479caab129e56bf8f481cbf1cd1b4b`;
the attempt1 ID remains
`0111662eee1972a8e0c223e53e3d1a5530dfac84a9b463ac918d6df4a1e3957a`.
No test split was used for selection, and no historical provider call was
repeated. Original case results, reranker responses, metrics, timings, splits,
configuration IDs and frozen gates are preserved.

The artifacts' original `provenance.source_sha256` values are also preserved:
they identify the generator used for the 2026-10-08 measurements, available
at commit `166d7d8`, rather than the corrected generator. Git history records
the original label and this correction. Regression tests exercise the real
generator with offline providers up to calibration freeze, reselect from the
saved grids, and verify the algorithm's objective and tie-break order.

`select_calibration_config()` accepts only `semantic` runs on the calibration
split, with one catalog/dataset/model/environment provenance. It refuses an
empty route or no-match denominator, any infrastructure error, and repeated
candidate gates. The declared selection rule maximizes **Top-1 Accuracy + Reject
Accuracy**, then Top-K Recall, then prefers smaller K and higher threshold.
Top-K Recall is independent of the threshold and therefore cannot select the
gate by itself.
The selected `FrozenRoutingConfig` stores the winning calibration `config_id`;
`run_frozen_test()` applies that K and threshold to both strategies on the
independent test split. A small fake fixture verifies the boundary. Its labels
and timing are engineering checks, not 60-case quality measurements.

The [measured offline catalog-size report](t43_offline_catalog_size_2026-10-08.json)
was produced by:

```bash
.venv/bin/python -m evaluation.catalog_size_benchmark --output evaluation/t43_offline_catalog_size_2026-10-08.json
```

It uses a fixed SHA-256 seed, 16-dimensional fake vectors, one active indexed
metadata version per Agent, and allowlisted `runtime_ref` entries mapped to a
statically validated thin workflow. It performs no workflow or provider call.
The threshold is `-1.0` solely to keep the scan path open; K is 3. Each size
has two warmup requests and 36 measured requests per strategy (12 fixed
queries × three repeats). SQLite and OS caches remain warm. The report records
the script hash, hardware, Python/OS, build time, sample counts, errors, and
all segment p50/p95 values. The reranker is a local identity transport, so its
timing is **not** LLM latency. All values below are measured milliseconds on
this machine; no target or production estimate is implied.

| Agents | Strategy | Full route p50/p95 | Query-to-decision p50/p95 | Discovery excluding embedding p50/p95 | Query embedding p50/p95 | Local rerank p50/p95 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 10 | semantic | 9.301 / 9.495 | 0.068 / 0.077 | 9.281 / 9.473 | 0.013 / 0.019 | not applicable |
| 10 | semantic_llm | 9.456 / 9.834 | 0.106 / 0.120 | 9.400 / 9.773 | 0.013 / 0.014 | 0.025 / 0.028 |
| 50 | semantic | 44.964 / 46.811 | 0.211 / 0.239 | 44.942 / 46.790 | 0.013 / 0.025 | not applicable |
| 50 | semantic_llm | 44.953 / 46.042 | 0.248 / 0.273 | 44.894 / 45.979 | 0.013 / 0.014 | 0.027 / 0.043 |
| 100 | semantic | 90.257 / 111.712 | 0.398 / 0.429 | 90.235 / 111.689 | 0.013 / 0.014 | not applicable |
| 100 | semantic_llm | 90.437 / 96.474 | 0.437 / 0.549 | 90.376 / 96.414 | 0.013 / 0.020 | 0.028 / 0.037 |

Each row has 36 measured samples and zero routing errors. Catalog/index build
times for 10/50/100 are 36.343/181.746/361.029 ms, measured separately.
`query-to-decision` starts at query embedding, matching the T41 runner; it
excludes the status check, static workflow validation and index loading that
precede embedding. `full route` includes that work. `discovery excluding
embedding` contains those pre-query steps, exact cosine and sorting, so it is
**not** an isolated cosine scan figure. The sub-millisecond query-to-decision
figures show small local vector math cost under this fixture, while full route
cost rises materially with catalog size. These measurements alone do not prove
an exact scan meets a live latency objective or that a vector database would
help: the dominant measured work includes repeated validation and SQLite
loading. P0 can retain the current simple exact scan pending live workload
evidence; any architecture change needs separate review.

## T43 live provider verification — 2026-10-08

The opt-in DashScope embedding and DeepSeek JSON chat rerank smoke tests passed
before the measured run. The local ignored `.env` maps
`AGENTHUB_EMBEDDING_API_KEY_ENV` to `DASHSCOPE_API_KEY` and
`AGENTHUB_RERANK_API_KEY_ENV` to `DEEPSEEK_API_KEY`. No credential values appear
in these artifacts. The live embedding model key is
`qwen-text-embedding-v4-1024` (1024 dimensions); the rerank model is
`deepseek-flash`. The scripts require the configured HTTPS provider hosts and
the 60-case script records hashes of non-secret endpoint/model configuration.
The thin workflows
use validation-only placeholders; no workflow is executed.

Run the 60-case quality evaluation explicitly:

```bash
.venv/bin/python -m evaluation.live_routing_benchmark --output-dir evaluation
```

The [calibration artifact](t43_live_calibration_2026-10-08.json) contains all
30 calibration decisions, 183 K/threshold candidate gates, measured baseline
latency, dataset/catalog/model provenance, and the selected configuration. The
gate was written **before** the independent test run. Only calibration labels
and scores selected K=5 and cosine threshold=0.35727615782291194. Its selected
calibration scores were Top-1 15/24, Top-K 24/24, Reject 4/6, False Accept
2/6, with zero infrastructure errors. Derived gate decisions have no invented
per-gate latency; the 30-case baseline is the measured calibration timing.
The 60-case dataset is 36 clear, 12 ambiguous and 12 no-match, split equally
between calibration and test (18/6/6 each).

The [independent test artifact](t43_live_test_2026-10-08.json) records every
decision, Top-K candidate/score, rerank result, error code and query-to-decision
latency under the same frozen gate:

| Strategy | Test cases | Top-1 | Top-K | Reject | False Accept | Infra errors | Route p50/p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| semantic | 30 | 11/24 | 24/24 | 3/6 | 3/6 | 0 | 368.009 / 643.426 |
| semantic_llm | 30 | 18/24 | 24/24 | 3/4 | 1/4 | 2 | 2199.482 / 10066.879 |

Both strategy latency distributions contain 30 measured calls, including
measured infrastructure-error calls. The two `semantic_llm` errors were
`RERANK_TRUNCATED_RESPONSE` on no-match cases, so its Reject/False Accept
denominator is four; those rates are **not** directly comparable to semantic's
six-case denominator. Both Top-1 denominators are the same 24 applicable
cases. All 21 actual DeepSeek calls are included in rerank transport latency:
p50/p95 2640.014/9703.747 ms. The 60 test query embedding calls across both
strategies have p50/p95 334.196/642.572 ms. The earlier
[512-token attempt](t43_live_test_attempt1_2026-10-08.json) and its
[calibration artifact](t43_live_calibration_attempt1_2026-10-08.json) are
retained: seven DeepSeek outputs were truncated. Raising only the JSON output
limit to 2048 tokens reduced this to two; no test labels were used to retune
K/threshold. This is one 30-case test sample, not a statistical significance
claim or task-answer quality evaluation.

The six-Agent live catalog/index build took 2324.269 ms, separately from per
request routing. The index embedded six metadata entries. Calibration query
embedding p50/p95 was 356.833/1251.049 ms over 30 calls. The artifacts store
the full script/config fingerprints and platform details. The measured host
used Python 3.12.13, Linux 6.8.0-138-generic, 16 logical CPUs and an AMD
Ryzen 7 8745H; caches and remote provider load were uncontrolled.

Run the separate synthetic catalog-size live latency benchmark explicitly:

```bash
.venv/bin/python -m evaluation.live_catalog_size_benchmark --output evaluation/t43_live_catalog_size_2026-10-08.json
```

The [live scale artifact](t43_live_catalog_size_2026-10-08.json) uses real
1024-dimensional embeddings and DeepSeek reranking, but synthetic Agent
metadata and queries. Each size has one warmup and 12 measured requests per
strategy; SQLite/OS/provider caches are not flushed. K=3 and threshold=-1
keep the scan/rerank path open. Build time is separate from routing. Values
below are measured milliseconds; the rerank segment includes the live API
request.

| Agents | Strategy | Full route p50/p95 | Discovery excluding embedding p50/p95 | Query embedding p50/p95 | Rerank p50/p95 | Errors |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 10 | semantic | 318.952 / 617.735 | 16.241 / 18.297 | 303.130 / 601.340 | n/a | 0 |
| 10 | semantic_llm | 2178.088 / 3974.995 | 15.811 / 17.816 | 243.991 / 474.662 | 1917.320 / 3514.593 | 0 |
| 50 | semantic | 439.839 / 484.852 | 67.503 / 69.178 | 372.245 / 417.335 | n/a | 0 |
| 50 | semantic_llm | 3224.426 / 10302.801 | 68.963 / 73.226 | 377.345 / 826.630 | 2446.415 / 9883.576 | 1 truncated |
| 100 | semantic | 439.294 / 589.729 | 131.137 / 135.468 | 306.658 / 461.633 | n/a | 0 |
| 100 | semantic_llm | 3202.536 / 10776.190 | 135.814 / 209.487 | 330.643 / 1593.711 | 2824.551 / 10334.756 | 2 truncated |

All non-rerank segments have 12 samples per row; rerank has 12 actual calls
for each `semantic_llm` row, including failed calls. Catalog/index build
times for 10/50/100 were 3902.657/18904.271/36551.787 ms. The live report
also contains query-to-decision p50/p95, per-sample error indexes/codes,
hardware (16 logical CPUs, 32,146,840 KiB RAM) and source hashes. Its
`discovery excluding embedding` segment includes status/allowlist validation,
SQLite index loading, exact cosine and sorting; isolated cosine time was not
measured. The larger catalogs increased that local segment on this host, while
embedding and especially rerank calls dominated observed live latency. For
this P0-sized catalog, retaining exact scan avoids extra infrastructure, but
these small sequential samples establish neither an SLA nor concurrent-load
performance. The deterministic offline figures above remain separate from
live provider and semantic quality results.
