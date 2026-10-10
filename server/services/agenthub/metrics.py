"""Read lightweight business metrics from persisted AgentHub SQLite facts."""

import sqlite3
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel

from .agent_runs import AgentRunRepository, TokenUsageSummary
from .database import AgentHubDatabase
from .routing_traces import RoutingTrace, RoutingTraceRepository
from .task_runs import TaskRunRepository


class MetricsDataError(ValueError):
    """Persisted business facts cannot be aggregated consistently."""


class Rate(BaseModel):
    numerator: int
    denominator: int
    value: float | None


class AverageMs(BaseModel):
    sample_count: int
    value: float | None


class TokenCount(BaseModel):
    sum: int | None
    known_count: int
    unknown_count: int


class TokenCounts(BaseModel):
    total: TokenCount
    input: TokenCount
    output: TokenCount


class AgentUsage(BaseModel):
    agent_id: UUID
    version: int
    started_count: int


class SelectedAgentCount(BaseModel):
    agent_id: UUID
    version: int
    count: int


class RoutingDistribution(BaseModel):
    strategies: dict[str, int]
    selected_agents: list[SelectedAgentCount]
    rejection_reasons: dict[str, int]
    routing_failures: int


class MetricsResponse(BaseModel):
    total_runs: int
    run_status_counts: dict[str, int]
    execution_success_rate: Rate
    execution_failure_rate: Rate
    rejected_rate: Rate
    average_routing_latency_ms: AverageMs
    average_execution_latency_ms: AverageMs
    token_usage: TokenCounts
    agent_usage: list[AgentUsage]
    routing_distribution: RoutingDistribution
    task_quality_success_rate: Rate | None


def _rate(numerator: int, denominator: int) -> Rate:
    return Rate(
        numerator=numerator, denominator=denominator,
        value=numerator / denominator if denominator else None,
    )


def _average(values: list[float]) -> AverageMs:
    return AverageMs(
        sample_count=len(values), value=sum(values) / len(values) if values else None,
    )


def _token_count(values: list[int | None]) -> TokenCount:
    known = [value for value in values if value is not None]
    return TokenCount(
        sum=sum(known) if known else None,
        known_count=len(known), unknown_count=len(values) - len(known),
    )


class MetricsService:
    def __init__(self, database: AgentHubDatabase) -> None:
        self.database = database
        TaskRunRepository(database)
        RoutingTraceRepository(database)
        AgentRunRepository(database)

    def get(
        self, *, start: datetime | None = None, end: datetime | None = None
    ) -> MetricsResponse:
        if (start is not None and (start.tzinfo is None or start.utcoffset() is None)) or (
            end is not None and (end.tzinfo is None or end.utcoffset() is None)
        ):
            raise ValueError("metrics window requires timezone-aware timestamps")
        start = start.astimezone(timezone.utc) if start is not None else None
        end = end.astimezone(timezone.utc) if end is not None else None
        if start is not None and end is not None and start >= end:
            raise ValueError("metrics window start must precede end")

        conditions = []
        parameters = []
        if start is not None:
            conditions.append("t.created_at >= ?")
            parameters.append(start.isoformat())
        if end is not None:
            conditions.append("t.created_at < ?")
            parameters.append(end.isoformat())
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self.database.connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""SELECT t.status AS task_status, t.strategy, t.routing_trace_id,
                           t.agent_run_id, r.payload_json, r.routing_latency_ms,
                           a.status AS agent_status, a.agent_id, a.agent_version,
                           a.outcome_state, a.latency_ms, a.token_usage_json
                    FROM agenthub_task_runs AS t
                    LEFT JOIN agenthub_routing_traces AS r
                      ON r.run_id = t.run_id AND r.trace_id = t.routing_trace_id
                    LEFT JOIN agenthub_agent_runs AS a
                      ON a.run_id = t.run_id AND a.agent_run_id = t.agent_run_id
                    {where}""",
                parameters,
            ).fetchall()

        statuses = Counter({key: 0 for key in (
            "pending", "running", "success", "failed", "rejected", "cancelled"
        )})
        strategies: Counter[str] = Counter()
        selected: Counter[tuple[str, int]] = Counter()
        reasons: Counter[str] = Counter()
        usage: Counter[tuple[str, int]] = Counter()
        outcomes: Counter[str] = Counter()
        routing_ms: list[float] = []
        execution_ms: list[float] = []
        total_tokens: list[int | None] = []
        input_tokens: list[int | None] = []
        output_tokens: list[int | None] = []
        routing_failures = 0

        for row in rows:
            statuses[row["task_status"]] += 1
            strategies[row["strategy"]] += 1
            if row["routing_trace_id"] is not None and row["payload_json"] is None:
                raise MetricsDataError("linked RoutingTrace is missing")
            if row["payload_json"] is not None:
                trace = RoutingTrace.model_validate_json(row["payload_json"])
                routing_ms.append(row["routing_latency_ms"])
                if trace.status == "selected":
                    selected[(str(trace.selected_agent.agent_id), trace.selected_agent.version)] += 1
                elif trace.status == "rejected":
                    reasons[trace.rejection_reason] += 1
                else:
                    routing_failures += 1

            if row["agent_run_id"] is not None and row["agent_id"] is None:
                raise MetricsDataError("linked AgentRun is missing")
            if row["agent_id"] is None:
                continue
            usage[(row["agent_id"], row["agent_version"])] += 1
            outcome = row["outcome_state"]
            if outcome is not None:
                expected_status = "success" if outcome == "succeeded" else "failed"
                if outcome not in ("succeeded", "failed") or row["agent_status"] != expected_status:
                    raise MetricsDataError("structured outcome conflicts with AgentRun status")
                outcomes[outcome] += 1
            if row["agent_status"] in ("success", "failed", "cancelled") and row["latency_ms"] is not None:
                execution_ms.append(row["latency_ms"])
            tokens = (
                TokenUsageSummary.model_validate_json(row["token_usage_json"])
                if row["token_usage_json"] is not None else None
            )
            total_tokens.append(tokens.total_tokens if tokens is not None else None)
            input_tokens.append(tokens.input_tokens if tokens is not None else None)
            output_tokens.append(tokens.output_tokens if tokens is not None else None)

        executed = outcomes["succeeded"] + outcomes["failed"]
        return MetricsResponse(
            total_runs=len(rows), run_status_counts=dict(statuses),
            execution_success_rate=_rate(outcomes["succeeded"], executed),
            execution_failure_rate=_rate(outcomes["failed"], executed),
            rejected_rate=_rate(statuses["rejected"], len(rows)),
            average_routing_latency_ms=_average(routing_ms),
            average_execution_latency_ms=_average(execution_ms),
            token_usage=TokenCounts(
                total=_token_count(total_tokens), input=_token_count(input_tokens),
                output=_token_count(output_tokens),
            ),
            agent_usage=[
                AgentUsage(agent_id=UUID(agent_id), version=version, started_count=count)
                for (agent_id, version), count in sorted(usage.items())
            ],
            routing_distribution=RoutingDistribution(
                strategies=dict(sorted(strategies.items())),
                selected_agents=[
                    SelectedAgentCount(agent_id=UUID(agent_id), version=version, count=count)
                    for (agent_id, version), count in sorted(selected.items())
                ],
                rejection_reasons=dict(sorted(reasons.items())),
                routing_failures=routing_failures,
            ),
            task_quality_success_rate=None,
        )
