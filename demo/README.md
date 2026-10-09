# T48 business demo and resume evidence

This is a four-scenario demonstration of the existing AgentHub platform. The
[recorded video](agenthub_t48_demo.mp4) is a **225-second, 1280×720 H.264 screen recording**, not
an animation of simulated API responses. It combines one fresh UI submission,
historical replay/query of completed real-provider runs, Registry UI actions,
and the [measured evidence board](t48_evidence_board.html). The board is a
presentation of the checked-in [T48 run record](t48_evidence_2026-10-09.json),
not a new benchmark. Its rounded values should be checked against the JSON.
The browser address bar was cropped to avoid exposing session URLs.

## Reproduce the live scenarios

Use the [standard Docker deployment and private environment setup](../docs/agenthub.md)
from T46/T47. The application must have a reachable configured chat provider,
the frozen T43 DashScope embedding/DeepSeek rerank configuration, and all six
demo Agents registered and active. In particular, the six controlled thin
workflows must pass the standard catalog validator. This command is an explicit
**live provider** run: it sends the four synthetic task texts to configured
providers and creates five TaskRuns in the existing SQLite database.

```bash
docker compose up -d --build
docker compose exec -T backend python -m server.services.agenthub.demo_catalog
.venv/bin/python demo/t48_reproduce.py --output /tmp/agenthub-t48-reproduction.json
```

The runner uses `http://localhost:6400` and `ws://localhost:6400/ws`, requires
the six-Agent catalog to start active, and restores Data Agent in a `finally`
block after scenario 4. If a provider call or the restoration fails, stop and
inspect the Registry before retrying. The output stores synthetic task text,
Run IDs, candidate scores, selected Agents, statuses, native completion/result
presence and a result hash; it does not store response text, credentials or
session IDs. Real-provider routing and generation can vary; a rerun is new
evidence, not a deterministic guarantee of these same selections. The script
does not independently prove which provider credentials are active; verify the
private deployment configuration without displaying secret values.

| Scenario | Measured 2026-10-09 outcome | Evidence |
| --- | --- | --- |
| Clear task | Supplied LangGraph/AutoGen notes → Research Agent, `TaskRun.success`, `AgentRun.success`, native result present | Run `7d623c0e-0624-4061-8e0b-13a9e1f1bbdd`; Research led Top-K at cosine 0.497; route 1874 ms |
| Ambiguous task | Supplied operations note → semantic Top-K included Data and Document; live DeepSeek rerank returned Data within Top-K; execution succeeded | Run `ff655a2e-178d-4114-a5ef-2fee9828cc9f`; route 3538 ms; native result present |
| No match | Email sending request → `NO_SUITABLE_AGENT`, `TaskRun.rejected`, no selected Agent or AgentRun | Run `4e0e77c7-a0c4-469f-9af2-84bad07283cf`; top cosine 0.329 below frozen threshold 0.357276… |
| Registry change | Same synthetic median/range task: Data selected and succeeded; after Data was disabled, Document selected and succeeded; Data absent from candidates; Data restored | Runs `d6d7d726-eaa9-4159-9417-b6e3adfe1657` and `3edd6519-df8a-4e1c-9700-04638ccf4f75` |

The runner recorded five additional runs: global metrics moved from 10 to 15
total, with success 5→9 and rejected 4→5. Token usage was **unknown**, not
zero. One earlier exploratory external-browsing run was still `running` in the
archived metrics snapshot after its cancel request; a later query confirmed
`cancelled` for run `36ee4a9c-4285-4ee6-839b-a3363fc9cae9`. It is outside
the four acceptance scenarios. The four-scenario
acceptance evidence relies on the five runs above. Business
`success` in these runs means structured selected-Agent success and normal
workflow completion; it does **not** assert answer quality. Rejection is a
routing outcome, distinct from execution failure.

## What the video shows

1. Six-Agent trusted internal Registry and the Data Agent capability card.
2. Clear Research routing evidence, a fresh task submission in Launch, then
   completed native result and persisted business status from the recorded run.
   The fresh UI submission created run
   `3d0c1f58-9a7c-499f-8de5-dbcb23b066f2`; query after recording confirmed
   Research Agent, `TaskRun.success`, `AgentRun.success`, and native `completed`.
3. Ambiguous Top-K scores, optional live DeepSeek rerank and the completed Data
   Agent run. DeepSeek only chose from the retrieved candidate IDs.
4. A no-match request with `NO_SUITABLE_AGENT`, `rejected`, and no AgentRun.
5. Before/after routing for the same task, plus an actual Registry UI disable
   and restore operation. The Router source was not changed.
6. The independent [T43 live test](../evaluation/t43_live_test_2026-10-08.json)
   with configuration and denominator limits. It is separate from these five
   T48 executions and from the deterministic
   [offline catalog benchmark](../evaluation/t43_offline_catalog_size_2026-10-08.json).

The recorded run replay uses an existing WebSocket session while this backend
process remains alive. A `run_id` remains queryable after backend restart, but
the old native WebSocket session and in-flight provider/tool calls do not
recover. Registry change is verified by both the real run records and the
controlled fake-embedding/temporary-SQLite integration regression.

## Claims supported for a resume

- Built an enterprise internal Agent Registry and capability-based semantic
  discovery/routing extension around the existing ChatDev workflow runtime;
  six controlled demo Agents were registered and queried in standard Docker.
  See [deployment evidence](../docs/agenthub.md) and the four live runs above.
- Added optional Top-K LLM reranking, independent business Run state, routing
  traces and aggregate metrics. In a 30-case independent **live** routing test,
  semantic Top-1 was 11/24; semantic+DeepSeek rerank was 18/24, with two
  truncated-output infrastructure errors excluded from reject denominators.
  See [T43 calibration/test provenance](../evaluation/README.md).
- Demonstrated Registry-controlled routing change without Router branches:
  the same task selected Data before disable and Document afterward, with
  native outputs and business success recorded for both.

These are bounded implementation and measured-test claims. The evaluation is
one held-out sample, not a statistical significance or SLA claim. External
tool results, answer-quality success, concurrent-load limits, untrusted/public
multi-tenant security, and recovery of active runs after restart are **NOT
VERIFIED**. Task text is persisted and sent to the configured embedding
provider; reranking also sends task text and candidate metadata to the LLM.
Use only data approved for this trusted internal setup. No API keys, private
SQLite files, attachments or user artifacts belong in this demo evidence.
