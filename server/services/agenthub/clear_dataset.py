"""Bind the reviewed clear-case source to registered demo Agent identities."""

import hashlib
import json
from pathlib import Path
from typing import Mapping

from .evaluation_dataset import RoutingDataset
from .metadata import AgentMetadata


CLEAR_CASES_PATH = Path(__file__).resolve().parents[3] / "evaluation" / "clear_cases_v1.json"


def build_clear_dataset(catalog: Mapping[str, AgentMetadata]) -> RoutingDataset:
    """Materialize 36 labels using the UUID/version snapshot returned by registration."""
    source = json.loads(CLEAR_CASES_PATH.read_text(encoding="utf-8"))
    groups = source["groups"]
    references = [group["runtime_ref"] for group in groups]
    if (len(groups) != 6 or len(set(references)) != 6
            or any(len(group["cases"]) != 6 for group in groups)
            or set(references) != set(catalog)
            or any(ref != agent.snapshot.runtime_ref for ref, agent in catalog.items())):
        raise ValueError("clear dataset requires the registered six-Agent catalog")

    ordered = sorted(catalog.items())
    snapshot = [
        {"runtime_ref": ref, "metadata": agent.snapshot.model_dump(mode="json"),
         "status": agent.status}
        for ref, agent in ordered
    ]
    digest = hashlib.sha256(json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    cases = [
        {**case, "dataset_version": source["dataset_version"], "category": "clear",
         "should_reject": False,
         "expected_agent_ids": [str(catalog[group["runtime_ref"]].snapshot.id)]}
        for group in groups for case in group["cases"]
    ]
    return RoutingDataset.model_validate({
        "dataset_version": source["dataset_version"],
        "catalog_snapshot_id": f"demo-catalog-{digest}",
        "catalog_agents": [
            {"agent_id": agent.snapshot.id, "version": agent.snapshot.version,
             "status": agent.status}
            for _, agent in ordered
        ],
        "cases": cases,
    })
