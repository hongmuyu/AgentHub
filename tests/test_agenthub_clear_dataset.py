"""T39 clear labels bind to registered demo identities, without routing runs."""

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.services.agenthub.clear_dataset import CLEAR_CASES_PATH, build_clear_dataset
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.demo_catalog import register_demo_catalog
from server.services.agenthub.discovery import discovery_text
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.evaluation_dataset import RoutingDataset
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
    backend = FakeEmbeddingBackend(vectors, model_key="t39-fixture", dimensions=2)
    database = AgentHubDatabase(tmp_path / "catalog.db")
    registry = AgentRegistry(database, ThinWorkflowValidator(RuntimeRefResolver(ROOT)), backend)
    return registry, register_demo_catalog(registry, ROOT)


def test_36_clear_cases_bind_to_registered_uuid_and_version(registered_catalog):
    registry, catalog = registered_catalog
    source = json.loads(CLEAR_CASES_PATH.read_text(encoding="utf-8"))
    dataset = build_clear_dataset(catalog)

    assert isinstance(dataset, RoutingDataset)
    assert dataset.dataset_version == source["dataset_version"] == "routing-v1"
    assert dataset.catalog_snapshot_id.startswith("demo-catalog-")
    assert len(dataset.catalog_snapshot_id) == len("demo-catalog-") + 64
    assert len(dataset.catalog_agents) == len(source["groups"]) == 6
    assert len(dataset.cases) == 36
    assert Counter(case.category for case in dataset.cases) == {"clear": 36}
    assert Counter(case.split for case in dataset.cases) == {"calibration": 18, "test": 18}
    assert all(not case.should_reject and len(case.expected_agent_ids) == 1
               and case.dataset_version == dataset.dataset_version for case in dataset.cases)

    expected_by_case = {
        case["case_id"]: group["runtime_ref"]
        for group in source["groups"] for case in group["cases"]
    }
    assert len(expected_by_case) == 36
    for case in dataset.cases:
        actual = catalog[expected_by_case[case.case_id]]
        assert case.expected_agent_ids == (actual.snapshot.id,)
        assert registry.get(actual.snapshot.id).snapshot.version == actual.snapshot.version
        assert actual.snapshot.version == 1
    assert {(ref.agent_id, ref.version, ref.status) for ref in dataset.catalog_agents} == {
        (agent.snapshot.id, agent.snapshot.version, agent.status) for agent in catalog.values()
    }
    assert RoutingDataset.model_validate_json(dataset.model_dump_json()) == dataset


def test_each_capability_group_has_six_distinct_tasks_and_explanations(registered_catalog):
    _, catalog = registered_catalog
    source = json.loads(CLEAR_CASES_PATH.read_text(encoding="utf-8"))
    dataset = build_clear_dataset(catalog)

    assert {group["runtime_ref"] for group in source["groups"]} == set(catalog)
    assert all(len(group["cases"]) == 6 for group in source["groups"])
    assert len({case.case_id for case in dataset.cases}) == 36
    assert len({case.task_text.casefold() for case in dataset.cases}) == 36
    assert all(case.annotation_reason and case.task_text != case.annotation_reason
               for case in dataset.cases)
    for group in source["groups"]:
        assert Counter(case["split"] for case in group["cases"]) == {
            "calibration": 3, "test": 3,
        }


def test_catalog_snapshot_stays_stable_after_reopen_and_changes_with_version(registered_catalog):
    registry, catalog = registered_catalog
    first = build_clear_dataset(catalog)
    reopened = AgentRegistry(AgentHubDatabase(registry.database.path), registry.validator,
                             registry.backend)
    assert build_clear_dataset(register_demo_catalog(reopened, ROOT)) == first

    research_ref = "workflow://research-agent/1"
    research = catalog[research_ref]
    updated = research.update_content(AgentMetadataInput.model_validate(
        {**research.snapshot.model_dump(exclude={"id", "version"}),
         "description": "Changed fixture capability boundary"}
    ))
    changed = build_clear_dataset({**catalog, research_ref: updated})
    assert changed.catalog_snapshot_id != first.catalog_snapshot_id
    assert changed.catalog_agents != first.catalog_agents


def test_incomplete_or_extra_catalog_mapping_is_rejected(registered_catalog):
    _, catalog = registered_catalog
    without_research = {ref: agent for ref, agent in catalog.items()
                        if ref != "workflow://research-agent/1"}
    with pytest.raises(ValueError):
        build_clear_dataset(without_research)
    with pytest.raises(ValueError):
        build_clear_dataset({**catalog, "workflow://unlisted/1": next(iter(catalog.values()))})

    research_ref = "workflow://research-agent/1"
    code_ref = "workflow://code-agent/1"
    swapped = {**catalog, research_ref: catalog[code_ref], code_ref: catalog[research_ref]}
    with pytest.raises(ValueError):
        build_clear_dataset(swapped)


def test_disabled_expected_agent_is_not_a_valid_clear_label(registered_catalog):
    _, catalog = registered_catalog
    research_ref = "workflow://research-agent/1"
    disabled = catalog[research_ref].set_status("disabled")
    with pytest.raises(ValidationError):
        build_clear_dataset({**catalog, research_ref: disabled})


def test_validator_rejects_duplicate_id_text_and_cross_split_overlap(registered_catalog):
    _, catalog = registered_catalog
    dataset = build_clear_dataset(catalog)
    base = dataset.model_dump(mode="json")
    for field, value in (
        ("case_id", base["cases"][0]["case_id"]),
        ("task_text", base["cases"][0]["task_text"].upper()),
    ):
        modified = json.loads(json.dumps(base))
        modified["cases"][3][field] = value
        assert modified["cases"][3]["split"] != modified["cases"][0]["split"]
        with pytest.raises(ValidationError):
            RoutingDataset.model_validate(modified)
