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
excerpts are synthetic. This file records label review, not measured routing or
task-quality results. Threshold calibration and benchmark execution are outside
T39.

# T40 complete routing labels

`ambiguous_no_match_cases_v1.json` adds 12 overlapping-capability cases with
explicit acceptable `runtime_ref` sets and 12 out-of-catalog cases marked
`should_reject`. `build_full_dataset()` combines them with the 36 clear cases,
binds all accepted references to the same registered UUID/version snapshot, and
validates the 60-case `RoutingDataset`. Each category has an even
`calibration`/`test` split (18/18 clear, 6/6 ambiguous, 6/6 no-match). These are
synthetic labels, with no routing decisions, timing or quality scores recorded.

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
not Router accuracy; threshold choice and benchmark execution remain unmeasured.

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

`select_calibration_config()` accepts only `semantic` runs on the calibration
split, with one catalog/dataset/model/environment provenance. It refuses an
empty route or no-match denominator, any infrastructure error, and repeated
candidate gates. The declared selection rule maximizes **Top-K Recall + Reject
Accuracy**, then Top-1 Accuracy, then prefers smaller K and higher threshold.
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
| 10 | semantic | 9.394 / 9.766 | 0.072 / 0.087 | 9.374 / 9.744 | 0.013 / 0.019 | not applicable |
| 10 | semantic_llm | 9.405 / 9.658 | 0.108 / 0.118 | 9.345 / 9.598 | 0.013 / 0.014 | 0.026 / 0.032 |
| 50 | semantic | 44.646 / 45.458 | 0.215 / 0.236 | 44.612 / 45.436 | 0.013 / 0.024 | not applicable |
| 50 | semantic_llm | 44.599 / 45.646 | 0.255 / 0.277 | 44.540 / 45.586 | 0.013 / 0.014 | 0.027 / 0.038 |
| 100 | semantic | 90.423 / 98.400 | 0.404 / 0.440 | 90.401 / 98.378 | 0.013 / 0.017 | not applicable |
| 100 | semantic_llm | 90.320 / 96.638 | 0.446 / 0.530 | 90.259 / 96.575 | 0.013 / 0.014 | 0.028 / 0.038 |

Each row has 36 measured samples and zero routing errors. Catalog/index build
times for 10/50/100 are 38.519/183.895/365.535 ms, measured separately.
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

**Live verification: NOT VERIFIED.** This checkout has no configured
`AGENTHUB_EMBEDDING_BASE_URL`, model, dimensions or credential selector, and no
AgentHub live reranker transport/configuration. No live endpoint was called.
Consequently the 60-case calibration gate was not selected/frozen, the
independent 30-case test split was not evaluated, and there are no valid live
Top-1, Top-K, Reject Accuracy, False Accept Rate, strategy comparison, or
provider-latency results. The offline catalog-size numbers must not be used as
semantic quality or live latency evidence. T43 remains blocked on a safely
configured embedding endpoint and a reranker transport; only explicit opt-in
live evaluation may fill these gaps.
