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
