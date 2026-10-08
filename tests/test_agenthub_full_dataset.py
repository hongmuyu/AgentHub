"""T40 labels extend the reviewed clear cases without running the router."""

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.demo_catalog import register_demo_catalog
from server.services.agenthub.discovery import discovery_text
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.evaluation_dataset import RoutingDataset
from server.services.agenthub.full_dataset import EXTRA_CASES_PATH, build_full_dataset
from server.services.agenthub.metadata import AgentMetadataInput
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.thin_workflow import ThinWorkflowValidator

ROOT = Path(__file__).resolve().parents[1] / "yaml_instance"
STEMS = ("research", "code", "data", "document", "planning", "review")


@pytest.fixture
def registered_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "fixture-model")
    monkeypatch.setenv("BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("API_KEY", "fixture-only")
    contents = [AgentMetadataInput.model_validate_json(
        (ROOT / f"agenthub_{stem}_metadata.json").read_text(encoding="utf-8")
    ) for stem in STEMS]
    vectors = {discovery_text(item): (0.0, float(index + 1))
               for index, item in enumerate(contents)}
    registry = AgentRegistry(
        AgentHubDatabase(tmp_path / "catalog.db"),
        ThinWorkflowValidator(RuntimeRefResolver(ROOT)),
        FakeEmbeddingBackend(vectors, model_key="t40-fixture", dimensions=2),
    )
    return registry, register_demo_catalog(registry, ROOT)


def test_full_dataset_schema_distribution_and_snapshot_binding(registered_catalog):
    registry, catalog = registered_catalog
    source = json.loads(EXTRA_CASES_PATH.read_text(encoding="utf-8"))
    dataset = build_full_dataset(catalog)

    assert isinstance(dataset, RoutingDataset)
    assert dataset.dataset_version == source["dataset_version"] == "routing-v1"
    assert len(dataset.cases) == 60
    assert Counter(case.category for case in dataset.cases) == {
        "clear": 36, "ambiguous": 12, "no_match": 12,
    }
    assert Counter(case.split for case in dataset.cases) == {
        "calibration": 30, "test": 30,
    }
    assert Counter((case.category, case.split) for case in dataset.cases) == {
        ("clear", "calibration"): 18, ("clear", "test"): 18,
        ("ambiguous", "calibration"): 6, ("ambiguous", "test"): 6,
        ("no_match", "calibration"): 6, ("no_match", "test"): 6,
    }
    assert len({case.case_id for case in dataset.cases}) == 60
    assert len({case.task_text.casefold() for case in dataset.cases}) == 60
    assert all(case.annotation_reason for case in dataset.cases)
    assert all(case.dataset_version == dataset.dataset_version for case in dataset.cases)
    assert RoutingDataset.model_validate_json(dataset.model_dump_json()) == dataset

    refs = {item.agent_id: item for item in dataset.catalog_agents}
    assert {(ref.agent_id, ref.version, ref.status) for ref in refs.values()} == {
        (agent.snapshot.id, agent.snapshot.version, agent.status) for agent in catalog.values()
    }
    for case in dataset.cases:
        for agent_id in case.expected_agent_ids:
            assert refs[agent_id].status == "active"
            assert registry.get(agent_id).snapshot.version == refs[agent_id].version


def test_ambiguous_sets_and_no_match_labels_follow_reviewed_source(registered_catalog):
    _, catalog = registered_catalog
    source = json.loads(EXTRA_CASES_PATH.read_text(encoding="utf-8"))
    dataset = build_full_dataset(catalog)
    cases = {case.case_id: case for case in dataset.cases}

    assert len(source["cases"]) == 24
    for item in source["cases"]:
        case = cases[item["case_id"]]
        if item["category"] == "ambiguous":
            assert item["should_reject"] is False
            assert len(item["expected_runtime_refs"]) >= 2
            assert len(item["expected_runtime_refs"]) == len(set(item["expected_runtime_refs"]))
            assert set(case.expected_agent_ids) == {
                catalog[ref].snapshot.id for ref in item["expected_runtime_refs"]
            }
            assert not case.should_reject
        else:
            assert item["category"] == "no_match"
            assert item["should_reject"] is True
            assert "expected_runtime_refs" not in item
            assert case.expected_agent_ids == ()
            assert case.should_reject


def test_bad_source_refs_and_cross_split_duplicates_are_rejected(
    registered_catalog, tmp_path, monkeypatch,
):
    _, catalog = registered_catalog
    source = json.loads(EXTRA_CASES_PATH.read_text(encoding="utf-8"))

    def build_with(modified):
        path = tmp_path / "modified.json"
        path.write_text(json.dumps(modified), encoding="utf-8")
        monkeypatch.setattr("server.services.agenthub.full_dataset.EXTRA_CASES_PATH", path)
        return build_full_dataset(catalog)

    unknown = json.loads(json.dumps(source))
    unknown["cases"][0]["expected_runtime_refs"][0] = "workflow://unknown-agent/1"
    with pytest.raises(ValueError, match="runtime_ref"):
        build_with(unknown)

    duplicate = json.loads(json.dumps(source))
    duplicate["cases"][1]["task_text"] = duplicate["cases"][0]["task_text"].upper()
    assert duplicate["cases"][0]["split"] != duplicate["cases"][1]["split"]
    with pytest.raises(ValueError, match="task_text"):
        build_with(duplicate)

    duplicate_id = json.loads(json.dumps(source))
    duplicate_id["cases"][1]["case_id"] = duplicate_id["cases"][0]["case_id"]
    with pytest.raises(ValueError, match="case_id"):
        build_with(duplicate_id)

    contradictory = json.loads(json.dumps(source))
    contradictory["cases"][12]["should_reject"] = False
    with pytest.raises(ValidationError, match="category, should_reject"):
        build_with(contradictory)
