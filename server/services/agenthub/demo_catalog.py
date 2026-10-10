"""Register allowlisted demo fixtures and report their real directory identities.

Run explicitly with configured embeddings: python -m server.services.agenthub.demo_catalog
"""

import json
from pathlib import Path

from server.settings import YAML_DIR

from .database import AgentHubDatabase
from .metadata import AgentMetadata, AgentMetadataInput
from .registry import AgentRegistry
from .runtime_resolver import RuntimeRefResolver
from .thin_workflow import ThinWorkflowValidator


class DemoCatalogConflictError(ValueError):
    def __init__(self) -> None:
        self.code = "DEMO_CATALOG_CONFLICT"
        super().__init__(self.code)


def register_demo_catalog(
    registry: AgentRegistry, workflow_root: Path = YAML_DIR
) -> dict[str, AgentMetadata]:
    """Register each ordinary fixture once; retain UUID/version on repeated calls."""
    root = Path(workflow_root)
    files = sorted(root.glob("agenthub_*_metadata.json"))
    if not files:
        raise ValueError("demo catalog has no metadata fixtures")
    fixtures = tuple(
        AgentMetadataInput.model_validate_json(path.read_text(encoding="utf-8"))
        for path in files
    )
    references = [fixture.runtime_ref for fixture in fixtures]
    if len(set(references)) != len(references):
        raise DemoCatalogConflictError()
    for fixture in fixtures:
        registry.validator.validate(fixture.runtime_ref)

    existing: dict[str, list[AgentMetadata]] = {}
    for agent in registry.versions.list_current():
        existing.setdefault(agent.snapshot.runtime_ref, []).append(agent)
    for fixture in fixtures:
        matches = existing.get(fixture.runtime_ref, [])
        if len(matches) > 1 or (
            matches and matches[0].snapshot.model_dump(exclude={"id", "version"})
            != fixture.model_dump()
        ):
            raise DemoCatalogConflictError()

    mapping: dict[str, AgentMetadata] = {}
    for fixture in fixtures:
        matches = existing.get(fixture.runtime_ref, [])
        mapping[fixture.runtime_ref] = matches[0] if matches else registry.register(fixture)
    return mapping


if __name__ == "__main__":
    from .openai_embeddings import OpenAICompatibleEmbeddingBackend

    catalog = register_demo_catalog(
        AgentRegistry(
            AgentHubDatabase(),
            ThinWorkflowValidator(RuntimeRefResolver()),
            OpenAICompatibleEmbeddingBackend.from_environment(),
        )
    )
    print(json.dumps({
        reference: {"agent_id": str(agent.snapshot.id), "version": agent.snapshot.version}
        for reference, agent in catalog.items()
    }, sort_keys=True))
