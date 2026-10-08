"""Extend reviewed clear labels with overlapping and out-of-catalog tasks."""

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from .clear_dataset import build_clear_dataset
from .evaluation_dataset import RoutingDataset
from .metadata import AgentMetadata

EXTRA_CASES_PATH = Path(__file__).resolve().parents[3] / "evaluation" / "ambiguous_no_match_cases_v1.json"


def build_full_dataset(catalog: Mapping[str, AgentMetadata]) -> RoutingDataset:
    """Bind all 60 reviewed labels to one registered demo catalog snapshot."""
    clear = build_clear_dataset(catalog)
    source = json.loads(EXTRA_CASES_PATH.read_text(encoding="utf-8"))
    cases = source["cases"]
    if (source["dataset_version"] != clear.dataset_version
            or Counter((case["category"], case["split"]) for case in cases) != {
                ("ambiguous", "calibration"): 6, ("ambiguous", "test"): 6,
                ("no_match", "calibration"): 6, ("no_match", "test"): 6,
            }):
        raise ValueError("full dataset requires 12 ambiguous and 12 no-match cases across splits")

    additions = []
    for case in cases:
        refs = case.get("expected_runtime_refs", [])
        if case["category"] == "ambiguous":
            valid_refs = (len(refs) >= 2 and len(refs) == len(set(refs))
                          and all(ref in catalog for ref in refs))
        else:
            valid_refs = not refs
        if not valid_refs:
            raise ValueError("invalid expected runtime_ref set")
        additions.append({
            **{key: value for key, value in case.items() if key != "expected_runtime_refs"},
            "dataset_version": clear.dataset_version,
            "expected_agent_ids": [str(catalog[ref].snapshot.id) for ref in refs],
        })
    clear_payload = clear.model_dump(mode="json")
    return RoutingDataset.model_validate({
        **clear_payload,
        "cases": [*clear_payload["cases"], *additions],
    })
