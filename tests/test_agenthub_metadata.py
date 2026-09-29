"""Business Agent metadata contract, independent of ChatDev AgentConfig."""

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from server.services.agenthub.metadata import (
    AgentMetadata,
    AgentMetadataInput,
    AgentMetadataVersion,
)


def _content(**overrides):
    values = {
        "name": "Research Agent",
        "description": "Searches technical information",
        "capabilities": ["Search technical docs"],
        "runtime_ref": "workflow://research-agent/1",
    }
    values.update(overrides)
    return AgentMetadataInput(**values)


def test_register_normalizes_content_and_round_trips_json():
    content = AgentMetadataInput(
        name="  Research Agent  ",
        description="  Search   technical\ninformation  ",
        capabilities=["  Search docs  ", "search  docs", " Summarize findings "],
        tools=[" Web_Search ", "web_search"],
        tags=[" Research ", "research"],
        runtime_ref=" workflow://research-agent/1 ",
    )

    agent = AgentMetadata.register(content)

    assert isinstance(agent.snapshot.id, UUID)
    assert agent.snapshot.id.version == 4
    assert agent.snapshot.version == 1
    assert agent.snapshot.name == "Research Agent"
    assert agent.snapshot.description == "Search technical information"
    assert agent.snapshot.capabilities == ("Search docs", "Summarize findings")
    assert agent.snapshot.tools == ("Web_Search",)
    assert agent.snapshot.tags == ("research",)
    assert agent.snapshot.runtime_ref == "workflow://research-agent/1"
    assert agent.status == "active"
    assert agent.created_at.tzinfo == timezone.utc
    assert agent.updated_at.tzinfo == timezone.utc
    assert "status" not in agent.snapshot.model_dump()
    assert AgentMetadata.model_validate_json(agent.model_dump_json()) == agent


@pytest.mark.parametrize("field", ["name", "description"])
@pytest.mark.parametrize("value", ["", " \t\n "])
def test_required_text_rejects_empty_and_whitespace(field, value):
    with pytest.raises(ValidationError):
        _content(**{field: value})


@pytest.mark.parametrize("field, limit", [("name", 80), ("description", 1000)])
def test_required_text_accepts_limit_and_rejects_one_over(field, limit):
    assert getattr(_content(**{field: "a" * limit}), field) == "a" * limit
    with pytest.raises(ValidationError):
        _content(**{field: "a" * (limit + 1)})


@pytest.mark.parametrize("value", [[], [""], [" \t "]])
def test_capabilities_reject_empty_list_or_item(value):
    with pytest.raises(ValidationError):
        _content(capabilities=value)


@pytest.mark.parametrize("field, limit", [("capabilities", 200), ("tools", 80), ("tags", 80)])
def test_list_items_accept_limit_and_reject_one_over(field, limit):
    assert getattr(_content(**{field: ["a" * limit]}), field) == ("a" * limit,)
    with pytest.raises(ValidationError):
        _content(**{field: ["a" * (limit + 1)]})


@pytest.mark.parametrize("field", ["tools", "tags"])
def test_optional_list_rejects_empty_items(field):
    with pytest.raises(ValidationError):
        _content(**{field: [" "]})


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "workflow://research-agent",
        "workflow://research-agent/0",
        "workflow://../1",
        "workflow:///1",
        "/tmp/research.yaml",
        "https://example.test/a",
        "workflow://Research/1",
    ],
)
def test_runtime_ref_rejects_non_logical_reference(reference):
    with pytest.raises(ValidationError):
        _content(runtime_ref=reference)


def test_runtime_ref_length_boundary():
    valid = "workflow://" + "a" * 115 + "/1"
    assert len(valid) == 128
    assert _content(runtime_ref=valid).runtime_ref == valid
    with pytest.raises(ValidationError):
        _content(runtime_ref="workflow://" + "a" * 116 + "/1")


@pytest.mark.parametrize("field", ["name", "description", "capabilities", "tools", "tags"])
def test_obvious_credential_text_is_rejected_from_public_metadata(field):
    value = "api_key=fake-placeholder"
    with pytest.raises(ValidationError):
        _content(**{field: [value] if field in {"capabilities", "tools", "tags"} else value})


@pytest.mark.parametrize(
    "value",
    ["Bearer fake-placeholder", "sk-ABCDEFGHIJKLMNOPQRST", "OPENAI_API_KEY=fake-placeholder"],
)
def test_common_token_forms_are_rejected(value):
    with pytest.raises(ValidationError):
        _content(description=value)


def test_tool_entry_rejects_service_url():
    with pytest.raises(ValidationError):
        _content(tools=["https://example.test/mcp"])


@pytest.mark.parametrize("field", ["api_key", "provider", "model_name", "role"])
def test_runtime_and_secret_configuration_fields_are_rejected(field):
    with pytest.raises(ValidationError):
        _content(**{field: "fake-placeholder"})


def test_validation_error_does_not_echo_rejected_credential():
    with pytest.raises(ValidationError) as error:
        _content(api_key="fake-placeholder")
    assert "fake-placeholder" not in str(error.value)


@pytest.mark.parametrize("value", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_snapshot_rejects_invalid_business_id(value):
    with pytest.raises(ValidationError):
        AgentMetadataVersion(**_content().model_dump(), id=value, version=1)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_snapshot_rejects_invalid_version(value):
    with pytest.raises(ValidationError):
        AgentMetadataVersion(**_content().model_dump(), id=uuid4(), version=value)


def test_content_update_increments_version_without_mutating_prior_snapshot():
    original = AgentMetadata.register(_content())
    disabled = original.set_status("disabled")
    updated = disabled.update_content(
        _content(name="Updated Research Agent", runtime_ref="workflow://research-agent/2")
    )

    assert updated.snapshot.id == original.snapshot.id
    assert updated.snapshot.version == 2
    assert updated.snapshot.name == "Updated Research Agent"
    assert updated.snapshot.runtime_ref == "workflow://research-agent/2"
    assert original.snapshot.version == 1
    assert original.snapshot.name == "Research Agent"
    assert original.snapshot.runtime_ref == "workflow://research-agent/1"
    assert disabled.status == updated.status == "disabled"
    assert updated.created_at == original.created_at
    assert updated.updated_at >= disabled.updated_at


def test_status_change_preserves_version_and_content_snapshot():
    original = AgentMetadata.register(_content())
    disabled = original.set_status("disabled")
    enabled = disabled.set_status("active")

    assert disabled.snapshot is original.snapshot
    assert enabled.snapshot is original.snapshot
    assert original.status == enabled.status == "active"
    assert disabled.status == "disabled"
    assert disabled.updated_at >= original.updated_at
    with pytest.raises(ValidationError):
        enabled.set_status("pending")


def test_version_snapshot_is_frozen():
    agent = AgentMetadata.register(_content())
    with pytest.raises(ValidationError):
        agent.snapshot.name = "Changed in place"


def test_importing_business_metadata_does_not_initialize_provider():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import server.services.agenthub.metadata; "
            "assert 'openai' not in sys.modules; assert 'schema_registry' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_timestamps_require_timezone_and_normalize_to_utc():
    agent = AgentMetadata.register(_content())
    payload = agent.model_dump()
    payload["created_at"] = datetime(2026, 1, 1, 10, tzinfo=timezone(timedelta(hours=2)))
    payload["updated_at"] = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
    normalized = AgentMetadata.model_validate(payload)
    assert normalized.created_at == datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    assert normalized.created_at.tzinfo == timezone.utc

    payload["created_at"] = datetime(2026, 1, 1, 8)
    with pytest.raises(ValidationError):
        AgentMetadata.model_validate(payload)
    payload["created_at"] = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        AgentMetadata.model_validate(payload)
