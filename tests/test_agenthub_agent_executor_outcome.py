"""T27: Agent outcomes are recorded beside the existing Message path."""

from unittest.mock import Mock
from uuid import uuid4

import pytest

from entity.configs import Node
from entity.messages import Message, MessageRole
from runtime.node.agent import ModelResponse
from runtime.node.agent_outcome import AgentOutcomeRecorder
from runtime.node.executor.agent_executor import AgentNodeExecutor
from runtime.node.executor.base import ExecutionContext


SECRET = "synthetic-private-marker"


def _node(retry=None):
    config = {
        "provider": "fixture", "name": "fixture-model", "role": "Answer the task.",
    }
    if retry is not None:
        config["retry"] = retry
    return Node.from_dict({
        "id": "agent", "type": "agent",
        "config": config,
    }, path="fixture.nodes[0]")


def _run(monkeypatch, mode, recorder=None, retry=None):
    calls = {"create": 0, "model": 0}

    class FixtureProvider:
        def __init__(self, config):
            self.config = config

        def create_client(self):
            calls["create"] += 1
            if mode == "create_failure":
                raise RuntimeError(f"create failed: {SECRET}")
            return object()

        def call_model(self, client, **kwargs):
            calls["model"] += 1
            if mode == "call_failure":
                raise RuntimeError(f"call failed: {SECRET}")
            if mode == "retry_success" and calls["model"] == 1:
                raise RuntimeError("temporarily unavailable")
            return ModelResponse(message=Message(role=MessageRole.ASSISTANT, content="answer"))

    monkeypatch.setattr(
        "runtime.node.executor.agent_executor.ProviderRegistry.get_provider",
        lambda provider: FixtureProvider,
    )
    tool_manager = Mock()
    tool_manager.get_tool_specs.return_value = []
    log_manager = Mock()
    context = ExecutionContext(
        tool_manager=tool_manager, function_manager=Mock(), log_manager=log_manager,
        outcome_recorder=recorder,
    )
    output = AgentNodeExecutor(context).execute(
        _node(retry), [Message(role=MessageRole.USER, content="task")],
    )
    return output, calls


@pytest.mark.parametrize("mode,expected_state,model_calls", [
    ("success", "succeeded", 1),
    ("create_failure", "failed", 0),
    ("call_failure", "failed", 1),
])
def test_provider_paths_record_outcome_without_changing_message(
    monkeypatch, mode, expected_state, model_calls,
):
    recorder = AgentOutcomeRecorder(run_id=uuid4(), node_id="agent")

    old_output, old_calls = _run(monkeypatch, mode)
    output, calls = _run(monkeypatch, mode, recorder)

    assert calls == old_calls == {"create": 1, "model": model_calls}
    assert len(output) == len(old_output) == 1
    assert output[0] == old_output[0]
    assert output[0].role == MessageRole.ASSISTANT
    assert output[0].metadata["source"] == "agent"
    if mode == "success":
        assert output[0].text_content() == "answer"
    else:
        assert output[0].text_content().startswith(
            f"Error calling model fixture-model: {mode.split('_')[0]} failed: {SECRET}"
        )
        assert "Original input:" in output[0].text_content()

    read = recorder.read()
    assert read.status == "recorded"
    assert read.outcome.run_id == recorder.run_id
    assert read.outcome.node_id == "agent"
    assert read.outcome.state == expected_state
    if expected_state == "failed":
        assert read.outcome.error_code == "AGENT_EXECUTION_FAILED"
        assert read.outcome.error_category == "agent_execution"
    else:
        assert read.outcome.error_code is None
        assert read.outcome.error_category is None
    assert SECRET not in read.outcome.model_dump_json()


def test_other_node_recorder_stays_missing_and_message_is_unchanged(monkeypatch):
    recorder = AgentOutcomeRecorder(run_id=uuid4(), node_id="other_agent")

    old_output, old_calls = _run(monkeypatch, "success")
    output, calls = _run(monkeypatch, "success", recorder)

    assert output == old_output
    assert calls == old_calls == {"create": 1, "model": 1}
    assert recorder.read().status == "missing"


def test_retry_path_records_only_final_success_and_preserves_message(monkeypatch):
    retry = {
        "enabled": True, "max_attempts": 2,
        "min_wait_seconds": 0, "max_wait_seconds": 0,
    }
    recorder = AgentOutcomeRecorder(run_id=uuid4(), node_id="agent")

    old_output, old_calls = _run(monkeypatch, "retry_success", retry=retry)
    output, calls = _run(monkeypatch, "retry_success", recorder, retry)

    assert output == old_output
    assert calls == old_calls == {"create": 1, "model": 2}
    assert recorder.read().status == "recorded"
    assert recorder.read().outcome.state == "succeeded"
