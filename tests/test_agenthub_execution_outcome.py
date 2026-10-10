"""T25: structured Agent outcomes stay safe and isolated to one run/node."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import runtime.node.agent_outcome as contract


def test_missing_outcome_has_explicit_failure_reason():
    recorder = contract.AgentOutcomeRecorder(run_id=uuid4(), node_id="agent")

    result = recorder.read()

    assert result.status == "missing"
    assert result.outcome is None
    assert result.error_code == "EXECUTION_OUTCOME_MISSING"


def test_success_outcome_is_structured_without_error_fields():
    run_id = uuid4()
    recorder = contract.AgentOutcomeRecorder(run_id=run_id, node_id="agent")
    outcome = contract.AgentExecutionOutcome(
        run_id=run_id, node_id="agent", state="succeeded",
        call_summary=contract.AgentCallSummary(provider_calls=1, tool_calls=0),
    )

    recorder.record(outcome)
    result = recorder.read()

    assert result.status == "recorded"
    assert result.outcome == outcome
    assert result.error_code is None
    assert outcome.call_summary.provider_calls == 1
    assert outcome.call_summary.tool_calls == 0
    assert outcome.error_code is None
    assert outcome.error_category is None


def test_failed_outcome_has_safe_code_and_category():
    run_id = uuid4()
    recorder = contract.AgentOutcomeRecorder(run_id=run_id, node_id="agent")
    outcome = contract.AgentExecutionOutcome(
        run_id=run_id, node_id="agent", state="failed",
        error_code="PROVIDER_CALL_FAILED", error_category="provider_error",
    )

    recorder.record(outcome)
    result = recorder.read()

    assert result.status == "recorded"
    assert result.outcome.state == "failed"
    assert result.outcome.error_code == "PROVIDER_CALL_FAILED"
    assert result.outcome.error_category == "provider_error"
    assert result.outcome.call_summary is None


def test_identical_duplicate_is_idempotent():
    run_id = uuid4()
    recorder = contract.AgentOutcomeRecorder(run_id=run_id, node_id="agent")
    first = contract.AgentExecutionOutcome(run_id=run_id, node_id="agent", state="succeeded")
    duplicate = contract.AgentExecutionOutcome(run_id=run_id, node_id="agent", state="succeeded")

    recorder.record(first)
    recorder.record(duplicate)

    assert recorder.read().status == "recorded"
    assert recorder.read().outcome == first


def test_conflicting_duplicate_is_sticky_invalid():
    run_id = uuid4()
    recorder = contract.AgentOutcomeRecorder(run_id=run_id, node_id="agent")
    success = contract.AgentExecutionOutcome(run_id=run_id, node_id="agent", state="succeeded")
    failure = contract.AgentExecutionOutcome(
        run_id=run_id, node_id="agent", state="failed",
        error_code="PROVIDER_CALL_FAILED", error_category="provider_error",
    )

    recorder.record(success)
    recorder.record(failure)
    recorder.record(success)
    result = recorder.read()

    assert result.status == "invalid"
    assert result.outcome is None
    assert result.error_code == "EXECUTION_OUTCOME_INVALID"


@pytest.mark.parametrize("wrong_target", ("run", "node"))
def test_foreign_outcome_is_rejected_without_contaminating_recorder(wrong_target):
    run_id = uuid4()
    recorder = contract.AgentOutcomeRecorder(run_id=run_id, node_id="agent")
    foreign = contract.AgentExecutionOutcome(
        run_id=uuid4() if wrong_target == "run" else run_id,
        node_id="other_agent" if wrong_target == "node" else "agent",
        state="succeeded",
    )

    with pytest.raises(ValueError, match="target"):
        recorder.record(foreign)

    assert recorder.read().status == "missing"
    own = contract.AgentExecutionOutcome(run_id=run_id, node_id="agent", state="succeeded")
    recorder.record(own)
    assert recorder.read().outcome == own


def test_separate_recorders_do_not_share_outcomes():
    run_id = uuid4()
    first = contract.AgentOutcomeRecorder(run_id=run_id, node_id="agent_a")
    other_node = contract.AgentOutcomeRecorder(run_id=run_id, node_id="agent_b")
    other_run = contract.AgentOutcomeRecorder(run_id=uuid4(), node_id="agent_a")

    first.record(contract.AgentExecutionOutcome(
        run_id=run_id, node_id="agent_a", state="succeeded"
    ))

    assert first.read().status == "recorded"
    assert other_node.read().status == "missing"
    assert other_run.read().status == "missing"


@pytest.mark.parametrize("changes", (
    {"state": "failed", "error_code": "API_KEY=private-marker", "error_category": "provider_error"},
    {"state": "failed", "error_code": "PROVIDER_CALL_FAILED", "error_category": "api_key=private-marker"},
    {"state": "failed", "error_code": "PROVIDER_CALL_FAILED", "error_category": "provider_error",
     "raw_error": "private-marker"},
    {"state": "succeeded", "error_code": "PROVIDER_CALL_FAILED"},
    {"state": "failed", "error_code": "PROVIDER_CALL_FAILED"},
    {"node_id": "/tmp/private-marker"},
    {"run_id": UUID(int=0)},
))
def test_outcome_rejects_unsafe_or_inconsistent_fields_without_echoing_input(changes):
    values = {"run_id": uuid4(), "node_id": "agent", "state": "succeeded"}
    values.update(changes)

    with pytest.raises(ValidationError) as error:
        contract.AgentExecutionOutcome(**values)

    assert "private-marker" not in str(error.value)


def test_summary_accepts_only_nonnegative_counts():
    with pytest.raises(ValidationError):
        contract.AgentCallSummary(provider_calls=-1)
    with pytest.raises(ValidationError):
        contract.AgentCallSummary(tool_calls="one")
    with pytest.raises(ValidationError):
        contract.AgentCallSummary(provider_calls=1, details="private-marker")
