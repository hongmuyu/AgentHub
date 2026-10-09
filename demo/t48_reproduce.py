"""Run the four T48 synthetic demo scenarios against an already configured Web app.

The configured backend must be the operator-approved live deployment. This
script does not read provider credentials or turn mock responses into live proof.
"""

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets


RESEARCH_TASK = (
    "Research question for internal R&D: using only these supplied technical notes, "
    "compare LangGraph and AutoGen for an enterprise Agent project. Note A says "
    "LangGraph uses explicit state graphs. Note B says AutoGen emphasizes multi-agent "
    "conversations. Summarize the supported tradeoff and gaps needing future source "
    "verification. Do not browse or call external tools."
)
AMBIGUOUS_TASK = (
    "From this supplied operations note, compare the stated error counts: Monday 8 "
    "and Tuesday 5. Identify which day has the larger count."
)
NO_MATCH_TASK = "Send an announcement email to all employees now."
DATA_TASK = (
    "Given synthetic response times 10, 11, 12, 13, and 80 ms, compute the "
    "median and range and identify the value needing outlier review."
)
TERMINAL = {"success", "failed", "rejected", "cancelled"}
JSON_HEADER = {"Content-Type": "application/json"}


async def _run_task(
    client: httpx.AsyncClient, ws_url: str, task: str, strategy: str,
    names: dict[str, str],
) -> dict:
    async with websockets.connect(ws_url, open_timeout=10) as socket:
        connected = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        session_id = connected["data"]["session_id"]
        native = {"workflow_completed": False, "result_present": False,
                  "result_characters": 0, "result_sha256": None}

        async def read_native() -> None:
            async for raw in socket:
                message = json.loads(raw)
                if message.get("type") == "workflow_completed":
                    native["workflow_completed"] = True
                    results = message.get("data", {}).get("results")
                    native["result_present"] = bool(results)
                    encoded = json.dumps(results, sort_keys=True, default=str)
                    native["result_characters"] = len(encoded)
                    native["result_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()

        reader = asyncio.create_task(read_native())
        try:
            response = await client.post("/api/agenthub/tasks", json={
                "task": task, "session_id": session_id, "routing_strategy": strategy,
            })
            response.raise_for_status()
            if response.status_code != 202:
                raise RuntimeError("unexpected task submission status")
            accepted = response.json()
            run_id = accepted["run_id"]
            for _ in range(90):
                query = await client.get(f"/api/agenthub/tasks/{run_id}")
                query.raise_for_status()
                run = query.json()
                if run["status"] in TERMINAL:
                    break
                await asyncio.sleep(1)
            else:
                raise RuntimeError(f"run did not reach a terminal state: {run_id}")
            await asyncio.sleep(0.2)  # Let the matching native WS event reach the reader.
            trace = run["routing_trace"]
            selected = trace["selected_agent"] if trace else None
            candidates = trace["candidates"] if trace else []
            order = accepted.get("rerank_order") or []
            return {
                "run_id": run_id,
                "task": task,
                "strategy": strategy,
                "status": run["status"],
                "error_code": run["error_code"],
                "routing_status": trace["status"] if trace else None,
                "rejection_reason": trace["rejection_reason"] if trace else None,
                "routing_latency_ms": trace["routing_latency_ms"] if trace else None,
                "selected_agent": names.get(selected["agent_id"]) if selected else None,
                "candidates": [{
                    "name": names.get(item["agent_id"]),
                    "score_kind": item["score_kind"],
                    "raw_similarity": item["raw_similarity"],
                } for item in candidates],
                "rerank_order": [names.get(item["agent_id"]) for item in order],
                "agent_status": (run["agent_run"] or {}).get("status"),
                "native_status": (run["agent_run"] or {}).get("native_status"),
                "execution_latency_ms": (run["agent_run"] or {}).get("latency_ms"),
                "native_output": native.copy(),
            }
        finally:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass


async def reproduce(base_url: str, ws_url: str) -> dict:
    async with httpx.AsyncClient(base_url=base_url, timeout=40) as client:
        health = await client.get("/health")
        health.raise_for_status()
        catalog = await client.get("/api/agenthub/agents?limit=100")
        catalog.raise_for_status()
        agents = catalog.json()["agents"]
        if len(agents) != 6 or any(agent["status"] != "active" for agent in agents):
            raise RuntimeError("the six-Agent catalog must start fully active")
        names = {agent["id"]: agent["name"] for agent in agents}
        data_agent = next(
            agent for agent in agents
            if agent["runtime_ref"] == "workflow://data-agent/1"
        )
        report = {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "evidence_scope": "actual configured HTTP/WebSocket deployment; synthetic tasks",
            "provider_note": "verify the active provider separately; this script does not inspect credentials",
            "catalog_count": len(agents),
            "metrics_before": (await client.get("/api/agenthub/metrics")).json(),
            "scenarios": {},
        }

        clear = await _run_task(client, ws_url, RESEARCH_TASK, "semantic", names)
        report["scenarios"]["clear_research"] = clear
        if (clear["selected_agent"] != "Research Agent" or clear["status"] != "success"
                or clear["agent_status"] != "success"
                or clear["native_status"] != "completed"
                or not clear["native_output"]["workflow_completed"]
                or not clear["native_output"]["result_present"]):
            raise RuntimeError("clear Research scenario did not complete with output")

        ambiguous = await _run_task(client, ws_url, AMBIGUOUS_TASK, "semantic_llm", names)
        report["scenarios"]["ambiguous_rerank"] = ambiguous
        candidate_names = {item["name"] for item in ambiguous["candidates"]}
        if (ambiguous["status"] != "success"
                or ambiguous["agent_status"] != "success"
                or ambiguous["native_status"] != "completed"
                or not {"Data Agent", "Document Agent"} <= candidate_names
                or not ambiguous["rerank_order"]
                or not set(ambiguous["rerank_order"]) <= candidate_names
                or not ambiguous["native_output"]["workflow_completed"]
                or not ambiguous["native_output"]["result_present"]):
            raise RuntimeError("ambiguous live rerank scenario did not meet its evidence gate")

        rejected = await _run_task(client, ws_url, NO_MATCH_TASK, "semantic", names)
        report["scenarios"]["no_suitable_agent"] = rejected
        if (rejected["status"] != "rejected"
                or rejected["rejection_reason"] != "NO_SUITABLE_AGENT"
                or rejected["agent_status"] is not None
                or rejected["native_output"]["workflow_completed"]):
            raise RuntimeError("no-match task did not produce a pure routing rejection")

        before = await _run_task(client, ws_url, DATA_TASK, "semantic", names)
        report["scenarios"]["registry_before"] = before
        if (before["selected_agent"] != "Data Agent" or before["status"] != "success"
                or before["agent_status"] != "success"
                or not before["native_output"]["result_present"]):
            raise RuntimeError("Data Agent baseline is not an executed success")
        disabled = False
        try:
            changed = await client.post(
                f"/api/agenthub/agents/{data_agent['id']}/disable", headers=JSON_HEADER,
            )
            changed.raise_for_status()
            disabled = True
            if changed.json()["status"] != "disabled":
                raise RuntimeError("Registry did not confirm the disabled state")
            after = await _run_task(client, ws_url, DATA_TASK, "semantic", names)
            report["scenarios"]["registry_after_disable"] = after
            if (after["selected_agent"] != "Document Agent"
                    or after["status"] != "success"
                    or after["agent_status"] != "success"
                    or not after["native_output"]["result_present"]
                    or "Data Agent" in {item["name"] for item in after["candidates"]}
                    or after["selected_agent"] == before["selected_agent"]):
                raise RuntimeError("disabling Data Agent did not change routing")
        finally:
            if disabled:
                restored = await client.post(
                    f"/api/agenthub/agents/{data_agent['id']}/enable", headers=JSON_HEADER,
                )
                restored.raise_for_status()
                if restored.json()["status"] != "active":
                    raise RuntimeError("Data Agent was not restored")

        final_catalog = (await client.get("/api/agenthub/agents?limit=100")).json()["agents"]
        if len(final_catalog) != 6 or any(agent["status"] != "active" for agent in final_catalog):
            raise RuntimeError("six-Agent catalog was not restored")
        report["registry_restored"] = True
        report["metrics_after"] = (await client.get("/api/agenthub/metrics")).json()
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(reproduce("http://localhost:6400", "ws://localhost:6400/ws"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("T48 four-scenario evidence written:", args.output)


if __name__ == "__main__":
    main()
