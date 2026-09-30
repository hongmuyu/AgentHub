"""Resolve allowlisted AgentHub runtime references to local workflows."""

import json
import re
from pathlib import Path

from server.settings import YAML_DIR

from .metadata import AgentMetadataInput


_WORKFLOW_FILENAME = re.compile(r"[A-Za-z0-9._-]+\.ya?ml")


class RuntimeReferenceError(ValueError):
    """A safe, path-free error for an invalid runtime reference or target."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RuntimeRefResolver:
    """Map logical references through a server-owned manifest within a workflow root."""

    def __init__(self, workflow_root: Path = YAML_DIR, manifest_path: Path | None = None) -> None:
        self.workflow_root = Path(workflow_root)
        self.manifest_path = (
            Path(manifest_path)
            if manifest_path is not None
            else self.workflow_root / "agenthub_manifest.json"
        )

    def resolve(self, runtime_ref: str) -> Path:
        if not isinstance(runtime_ref, str):
            raise RuntimeReferenceError("INVALID_RUNTIME_REF")
        try:
            reference = AgentMetadataInput.validate_runtime_ref(runtime_ref)
        except ValueError:
            raise RuntimeReferenceError("INVALID_RUNTIME_REF") from None

        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise RuntimeReferenceError("INVALID_WORKFLOW_MANIFEST") from None
        if not isinstance(manifest, dict):
            raise RuntimeReferenceError("INVALID_WORKFLOW_MANIFEST")

        if reference not in manifest:
            raise RuntimeReferenceError("UNKNOWN_RUNTIME_REF")
        filename = manifest[reference]
        if (
            not isinstance(filename, str)
            or ".." in filename
            or _WORKFLOW_FILENAME.fullmatch(filename) is None
        ):
            raise RuntimeReferenceError("INVALID_WORKFLOW_TARGET")

        try:
            root = self.workflow_root.resolve(strict=True)
            target = (root / filename).resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                raise RuntimeReferenceError("INVALID_WORKFLOW_TARGET")
        except (OSError, RuntimeError):
            raise RuntimeReferenceError("INVALID_WORKFLOW_TARGET") from None
        return target
