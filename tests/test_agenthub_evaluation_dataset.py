"""Synthetic routing labels and catalog references; no benchmark scores."""

from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from server.services.agenthub.evaluation_dataset import RoutingDataset


RESEARCH = "11111111-1111-4111-8111-111111111111"
DOCUMENT = "22222222-2222-4222-8222-222222222222"
DISABLED = "33333333-3333-4333-8333-333333333333"


def dataset() -> dict:
    return {
        "dataset_version": "routing-v1",
        "catalog_snapshot_id": "synthetic-catalog-v1",
        "catalog_agents": [
            {"agent_id": RESEARCH, "version": 2, "status": "active"},
            {"agent_id": DOCUMENT, "version": 1, "status": "active"},
            {"agent_id": DISABLED, "version": 1, "status": "disabled"},
        ],
        "attachment_fixture_refs": ["fixture://synthetic-report"],
        "cases": [
            {
                "case_id": "clear-1",
                "task_text": "Summarize this synthetic research finding.",
                "attachment_fixture_ref": "fixture://synthetic-report",
                "expected_agent_ids": [RESEARCH],
                "should_reject": False,
                "category": "clear",
                "annotation_reason": "Research summary is the stated capability.",
                "dataset_version": "routing-v1",
                "split": "calibration",
            },
            {
                "case_id": "ambiguous-1",
                "task_text": "Compare the findings in two technical documents.",
                "expected_agent_ids": [DOCUMENT, RESEARCH],
                "should_reject": False,
                "category": "ambiguous",
                "annotation_reason": "Document comparison and research comparison both fit.",
                "dataset_version": "routing-v1",
                "split": "test",
            },
            {
                "case_id": "no-match-1",
                "task_text": "Reserve a meeting room for tomorrow.",
                "expected_agent_ids": [],
                "should_reject": True,
                "category": "no_match",
                "annotation_reason": "No catalog Agent can book rooms.",
                "dataset_version": "routing-v1",
                "split": "test",
            },
        ],
    }


def rejected(data: dict) -> None:
    with pytest.raises(ValidationError):
        RoutingDataset.model_validate(data)


def test_clear_ambiguous_and_no_match_round_trip_with_versioned_catalog():
    parsed = RoutingDataset.model_validate(dataset())

    assert [case.category for case in parsed.cases] == ["clear", "ambiguous", "no_match"]
    assert parsed.cases[0].attachment_fixture_ref == "fixture://synthetic-report"
    assert parsed.cases[1].expected_agent_ids == tuple(sorted(
        (UUID(DOCUMENT), UUID(RESEARCH)), key=str,
    ))
    assert parsed.cases[2].expected_agent_ids == ()
    assert parsed.catalog_agents[0].version == 2
    assert RoutingDataset.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize("field,value", [
    ("case_id", ""), ("task_text", " \t "),
    ("annotation_reason", ""), ("category", "unsupported"),
    ("split", "training"), ("dataset_version", "routing-v2"),
])
def test_case_fields_and_dataset_version_must_be_valid(field, value):
    data = dataset()
    data["cases"][0][field] = value
    rejected(data)


@pytest.mark.parametrize("category,should_reject,expected", [
    ("clear", False, []),
    ("clear", False, [RESEARCH, DOCUMENT]),
    ("clear", True, [RESEARCH]),
    ("ambiguous", False, [RESEARCH]),
    ("ambiguous", True, [RESEARCH, DOCUMENT]),
    ("no_match", False, []),
    ("no_match", True, [RESEARCH]),
])
def test_category_rejection_and_acceptable_set_must_agree(category, should_reject, expected):
    data = dataset()
    data["cases"][0].update(
        category=category, should_reject=should_reject, expected_agent_ids=expected,
    )
    rejected(data)


def test_duplicate_expected_agent_is_not_a_set():
    data = dataset()
    data["cases"][1]["expected_agent_ids"] = [RESEARCH, RESEARCH]
    rejected(data)


@pytest.mark.parametrize("agent_id", [
    "44444444-4444-4444-8444-444444444444", DISABLED,
])
def test_expected_agent_must_exist_as_active_snapshot_reference(agent_id):
    data = dataset()
    data["cases"][0]["expected_agent_ids"] = [agent_id]
    rejected(data)


@pytest.mark.parametrize("version", [0, -1, True, 1.5])
def test_catalog_version_must_be_positive_integer(version):
    data = dataset()
    data["catalog_agents"][0]["version"] = version
    rejected(data)


def test_catalog_cannot_contain_two_versions_of_same_agent_identity():
    data = dataset()
    data["catalog_agents"].append({"agent_id": RESEARCH, "version": 3, "status": "active"})
    rejected(data)


@pytest.mark.parametrize("field,value", [
    ("catalog_snapshot_id", ""), ("catalog_agents", []),
    ("cases", []),
])
def test_dataset_requires_identified_snapshot_and_cases(field, value):
    data = dataset()
    data[field] = value
    rejected(data)


def test_catalog_rejects_invalid_status():
    data = dataset()
    data["catalog_agents"][0]["status"] = "ready"
    rejected(data)


@pytest.mark.parametrize("reference", ["fixture://missing", "/tmp/private.pdf"])
def test_attachment_reference_must_be_declared_logical_fixture(reference):
    data = dataset()
    data["cases"][0]["attachment_fixture_ref"] = reference
    rejected(data)


def test_fixture_registry_rejects_duplicate_references():
    data = dataset()
    data["attachment_fixture_refs"].append("fixture://synthetic-report")
    rejected(data)


def test_duplicate_case_id_across_splits_is_rejected():
    data = dataset()
    data["cases"][1]["case_id"] = "clear-1"
    rejected(data)


@pytest.mark.parametrize("split", ["calibration", "test"])
def test_duplicate_task_within_or_across_splits_is_rejected(split):
    data = dataset()
    data["cases"][1]["split"] = split
    data["cases"][1]["task_text"] = "  SUMMARIZE  this synthetic research finding.  "
    rejected(data)


def test_extra_secret_or_result_fields_are_not_dataset_contract():
    data = dataset()
    data["cases"][0]["api_key"] = "fake-placeholder"
    rejected(data)
    data = deepcopy(dataset())
    data["cases"][0]["measured_score"] = 1.0
    rejected(data)
