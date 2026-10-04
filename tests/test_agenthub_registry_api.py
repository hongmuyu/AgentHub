"""Registry HTTP contract with real persistence and a controlled fake embedding backend."""

import json
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.models import WorkflowRequest, WorkflowRunRequest
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import discovery_text
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadataInput
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.thin_workflow import ThinWorkflowValidator
from server.routes import ALL_ROUTERS
from server.routes import agenthub


REFERENCE = "workflow://research-agent/1"
PUBLIC_FIELDS = {
    "id", "name", "description", "capabilities", "tools", "tags",
    "version", "status", "runtime_ref", "created_at", "updated_at",
}


def _content(name="Research Agent", **changes):
    content = {
        "name": name,
        "description": "Summarizes technical reports",
        "capabilities": ["Search technical docs"],
        "tools": ["web_search"],
        "tags": ["research"],
        "runtime_ref": REFERENCE,
    }
    content.update(changes)
    return content


@pytest.fixture
def api(tmp_path):
    root = tmp_path / "workflows"
    root.mkdir()
    (root / "research.yaml").write_text(yaml.safe_dump({
        "version": "0.4.0", "vars": {},
        "graph": {
            "id": "thin_research", "start": ["agent"], "end": ["agent"],
            "nodes": [{
                "id": "agent", "type": "agent",
                "config": {"provider": "openai", "name": "fixture-model", "role": "Summarize."},
            }],
            "edges": [],
        },
    }), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({REFERENCE: "research.yaml"}), encoding="utf-8")
    initial = AgentMetadataInput.model_validate(_content())
    second = AgentMetadataInput.model_validate(_content(
        "Code Agent", capabilities=["Review source code"], tags=["code"]
    ))
    updated = AgentMetadataInput.model_validate(_content("Updated Research Agent"))
    vectors = {
        discovery_text(content): (float(index + 1), 1.0)
        for index, content in enumerate((initial, second, updated))
    }
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    registry = AgentRegistry(
        database, ThinWorkflowValidator(RuntimeRefResolver(root, manifest)),
        FakeEmbeddingBackend(vectors, model_key="fake-v1", dimensions=2),
    )
    app = FastAPI()
    app.state.agenthub_registry = registry
    app.include_router(agenthub.router)
    with TestClient(app) as client:
        yield client, registry, database, manifest


def test_registry_router_is_in_application_router_set():
    assert agenthub.router in ALL_ROUTERS


def test_register_get_update_and_public_response(api):
    client, registry, _, _ = api
    created = client.post("/api/agenthub/agents", json=_content())
    assert created.status_code == 201
    first = created.json()
    assert set(first) == PUBLIC_FIELDS
    assert first["name"] == "Research Agent"
    assert first["version"] == 1
    assert first["status"] == "active"
    assert first["capabilities"] == ["Search technical docs"]
    assert "fixture-model" not in created.text
    assert "research.yaml" not in created.text
    agent_id = first["id"]
    assert client.get(f"/api/agenthub/agents/{agent_id}").json() == first

    changed = client.put(
        f"/api/agenthub/agents/{agent_id}", json=_content("Updated Research Agent")
    )
    assert changed.status_code == 200
    assert changed.json()["id"] == agent_id
    assert changed.json()["version"] == 2
    assert changed.json()["name"] == "Updated Research Agent"
    assert registry.versions.get_version(UUID(first["id"]), 1) is not None
    assert client.get(f"/api/agenthub/agents/{agent_id}").json() == changed.json()


def test_list_search_pagination_status_and_switches(api):
    client, _, _, _ = api
    research = client.post("/api/agenthub/agents", json=_content()).json()
    code = client.post("/api/agenthub/agents", json=_content(
        "Code Agent", capabilities=["Review source code"], tags=["code"]
    )).json()
    ordered = sorted((research, code), key=lambda item: item["id"])
    response = client.get("/api/agenthub/agents", params={"limit": 1, "offset": 1})
    assert response.status_code == 200
    assert response.json() == {"agents": ordered[1:], "limit": 1, "offset": 1}
    assert client.get("/api/agenthub/agents", params={"q": "SOURCE CODE"}).json()["agents"] == [code]
    assert client.get("/api/agenthub/agents", params={"q": "research-agent"}).json()["agents"] == []

    disabled = client.post(f"/api/agenthub/agents/{research['id']}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["version"] == 1
    assert client.post(f"/api/agenthub/agents/{research['id']}/disable").json() == disabled.json()
    assert client.get("/api/agenthub/agents").json()["agents"] == [code]
    assert client.get("/api/agenthub/agents", params={"status": "disabled"}).json()["agents"] == [disabled.json()]
    assert client.get("/api/agenthub/agents", params={"include_disabled": True}).json()["agents"] == sorted(
        (disabled.json(), code), key=lambda item: item["id"]
    )
    enabled = client.post(f"/api/agenthub/agents/{research['id']}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"
    assert enabled.json()["version"] == 1


@pytest.mark.parametrize("params", (
    {"limit": 0}, {"limit": -1}, {"offset": -1}, {"status": "unknown"},
))
def test_invalid_list_query_is_rejected(api, params):
    client, _, _, _ = api
    response = client.get("/api/agenthub/agents", params=params)
    assert response.status_code == 422


def test_unknown_or_malformed_id_is_safe(api):
    client, _, _, _ = api
    unknown = uuid4()
    for method, path in (
        ("GET", f"/api/agenthub/agents/{unknown}"),
        ("PUT", f"/api/agenthub/agents/{unknown}"),
        ("POST", f"/api/agenthub/agents/{unknown}/enable"),
        ("POST", f"/api/agenthub/agents/{unknown}/disable"),
    ):
        response = client.request(method, path, json=_content() if method == "PUT" else None)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "AGENT_NOT_FOUND"
    assert client.get("/api/agenthub/agents/not-a-uuid").status_code == 422


def test_nil_agent_id_is_rejected_by_item_operations(api):
    client, _, _, _ = api
    nil_id = "00000000-0000-0000-0000-000000000000"
    for method, suffix in (
        ("GET", ""), ("PUT", ""), ("POST", "/enable"), ("POST", "/disable")
    ):
        response = client.request(
            method, f"/api/agenthub/agents/{nil_id}{suffix}",
            json=_content() if method == "PUT" else None,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_AGENT_ID"


def test_invalid_body_and_reference_do_not_echo_input_or_register(api):
    client, registry, _, _ = api
    secret_marker = "SENSITIVE_MARKER_123"
    invalid = client.post("/api/agenthub/agents", json={
        **_content(), "provider_config": {"api_key": secret_marker}
    })
    assert invalid.status_code == 422
    assert secret_marker not in invalid.text
    assert invalid.json()["detail"]["code"] == "INVALID_AGENT_METADATA"

    bad_ref = client.post("/api/agenthub/agents", json=_content(
        runtime_ref="workflow://unknown/1"
    ))
    assert bad_ref.status_code == 422
    assert bad_ref.json()["detail"]["code"] == "UNKNOWN_RUNTIME_REF"
    assert "research.yaml" not in bad_ref.text
    assert registry.list(include_disabled=True) == ()


def test_failed_update_enable_and_index_write_leave_old_state(api):
    client, registry, database, manifest = api
    created = client.post("/api/agenthub/agents", json=_content()).json()
    agent_id = created["id"]
    invalid = client.put(f"/api/agenthub/agents/{agent_id}", json=_content(
        "Updated Research Agent", runtime_ref="workflow://unknown/1"
    ))
    assert invalid.status_code == 422
    assert client.get(f"/api/agenthub/agents/{agent_id}").json() == created

    disabled = client.post(f"/api/agenthub/agents/{agent_id}/disable").json()
    manifest.write_text("{}", encoding="utf-8")
    failed_enable = client.post(f"/api/agenthub/agents/{agent_id}/enable")
    assert failed_enable.status_code == 409
    assert failed_enable.json()["detail"]["code"] == "UNKNOWN_RUNTIME_REF"
    assert client.get(f"/api/agenthub/agents/{agent_id}").json() == disabled

    manifest.write_text(json.dumps({REFERENCE: "research.yaml"}), encoding="utf-8")
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER fail_index BEFORE INSERT ON agenthub_agent_embeddings
               WHEN NEW.version = 2 BEGIN SELECT RAISE(ABORT, 'private index detail'); END"""
        )
    failed_update = client.put(
        f"/api/agenthub/agents/{agent_id}", json=_content("Updated Research Agent")
    )
    assert failed_update.status_code == 503
    assert failed_update.json()["detail"]["code"] == "REGISTRY_WRITE_FAILED"
    assert "private index detail" not in failed_update.text
    assert registry.get(UUID(created["id"])).snapshot.version == 1
    assert registry.versions.get_version(UUID(created["id"]), 2) is None


def test_registration_index_failure_does_not_leave_partial_agent(api):
    client, registry, database, _ = api
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER fail_registration_index BEFORE INSERT ON agenthub_agent_embeddings
               BEGIN SELECT RAISE(ABORT, 'private index detail'); END"""
        )
    response = client.post("/api/agenthub/agents", json=_content())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "REGISTRY_WRITE_FAILED"
    assert "private index detail" not in response.text
    assert registry.list(include_disabled=True) == ()


def test_disable_write_failure_and_missing_index_keep_status(api):
    client, registry, database, _ = api
    created = client.post("/api/agenthub/agents", json=_content()).json()
    agent_id = created["id"]
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER fail_disable BEFORE UPDATE OF status ON agenthub_agents
               WHEN NEW.status = 'disabled'
               BEGIN SELECT RAISE(ABORT, 'private state detail'); END"""
        )
    failed_disable = client.post(f"/api/agenthub/agents/{agent_id}/disable")
    assert failed_disable.status_code == 503
    assert failed_disable.json()["detail"]["code"] == "REGISTRY_WRITE_FAILED"
    assert "private state detail" not in failed_disable.text
    assert registry.get(UUID(agent_id)).status == "active"

    with database.transaction() as connection:
        connection.execute("DROP TRIGGER fail_disable")
    disabled = client.post(f"/api/agenthub/agents/{agent_id}/disable").json()
    with database.transaction() as connection:
        connection.execute(
            "DELETE FROM agenthub_agent_embeddings WHERE agent_id = ?",
            (agent_id,),
        )
    failed_enable = client.post(f"/api/agenthub/agents/{agent_id}/enable")
    assert failed_enable.status_code == 409
    assert failed_enable.json()["detail"]["code"] == "INDEX_NOT_READY"
    assert client.get(f"/api/agenthub/agents/{agent_id}").json() == disabled


def test_missing_registry_dependency_returns_safe_unavailable():
    app = FastAPI()
    app.include_router(agenthub.router)
    with TestClient(app) as client:
        response = client.get("/api/agenthub/agents")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "REGISTRY_NOT_CONFIGURED"


def test_legacy_workflow_models_keep_required_yaml_file():
    with pytest.raises(ValidationError):
        WorkflowRequest.model_validate({"task_prompt": "hello"})
    with pytest.raises(ValidationError):
        WorkflowRunRequest.model_validate({"task_prompt": "hello"})
    assert WorkflowRequest(yaml_file="manual.yaml", task_prompt="hello").yaml_file == "manual.yaml"
    assert WorkflowRunRequest(yaml_file="manual.yaml", task_prompt="hello").yaml_file == "manual.yaml"
