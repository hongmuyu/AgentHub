"""Logical runtime references resolve only to server-allowlisted workflows."""

import json
from pathlib import Path

import pytest

from server.services.agenthub.runtime_resolver import (
    RuntimeRefResolver,
    RuntimeReferenceError,
)


REFERENCE = "workflow://research-agent/1"


def _resolver(tmp_path, manifest):
    root = tmp_path / "workflows"
    root.mkdir()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return RuntimeRefResolver(root, manifest_path), root, manifest_path


def test_allowlisted_reference_resolves_to_existing_workflow(tmp_path):
    resolver, root, _ = _resolver(tmp_path, {REFERENCE: "research.yaml"})
    workflow = root / "research.yaml"
    workflow.write_text("id: research\n", encoding="utf-8")

    assert resolver.resolve(REFERENCE) == workflow.resolve()


@pytest.mark.parametrize(
    "reference",
    ["workflow://unknown/1", "workflow://research-agent/2"],
)
def test_unknown_key_or_revision_is_rejected(tmp_path, reference):
    resolver, root, _ = _resolver(tmp_path, {REFERENCE: "research.yaml"})
    (root / "research.yaml").write_text("id: research\n", encoding="utf-8")

    with pytest.raises(RuntimeReferenceError) as error:
        resolver.resolve(reference)
    assert error.value.code == "UNKNOWN_RUNTIME_REF"


@pytest.mark.parametrize(
    "reference",
    [
        "/private/workflow.yaml",
        "../workflow.yaml",
        "workflow://research-agent/../1",
        "workflow://research-agent/0",
        "workflow://Research/1",
    ],
)
def test_non_logical_reference_is_rejected_without_echo(tmp_path, reference):
    resolver, _, _ = _resolver(tmp_path, {REFERENCE: "research.yaml"})

    with pytest.raises(RuntimeReferenceError) as error:
        resolver.resolve(reference)
    assert error.value.code == "INVALID_RUNTIME_REF"
    assert reference not in str(error.value)


@pytest.mark.parametrize(
    "target", ["/private/workflow.yaml", "../outside.yaml", "nested/workflow.yaml"]
)
def test_manifest_target_must_be_a_yaml_filename(tmp_path, target):
    resolver, _, _ = _resolver(tmp_path, {REFERENCE: target})

    with pytest.raises(RuntimeReferenceError) as error:
        resolver.resolve(REFERENCE)
    assert error.value.code == "INVALID_WORKFLOW_TARGET"
    assert target not in str(error.value)


def test_symlink_outside_workflow_root_is_rejected(tmp_path):
    resolver, root, _ = _resolver(tmp_path, {REFERENCE: "linked.yaml"})
    outside = tmp_path / "outside.yaml"
    outside.write_text("id: outside\n", encoding="utf-8")
    (root / "linked.yaml").symlink_to(outside)

    with pytest.raises(RuntimeReferenceError) as error:
        resolver.resolve(REFERENCE)
    assert error.value.code == "INVALID_WORKFLOW_TARGET"
    assert str(outside) not in str(error.value)
    assert str(root) not in str(error.value)


def test_missing_target_is_rejected_without_path_echo(tmp_path):
    resolver, root, _ = _resolver(tmp_path, {REFERENCE: "missing.yaml"})

    with pytest.raises(RuntimeReferenceError) as error:
        resolver.resolve(REFERENCE)
    assert error.value.code == "INVALID_WORKFLOW_TARGET"
    assert str(root) not in str(error.value)


def test_workflow_file_stat_error_does_not_echo_server_path(tmp_path, monkeypatch):
    resolver, root, _ = _resolver(tmp_path, {REFERENCE: "research.yaml"})
    (root / "research.yaml").write_text("id: research\n", encoding="utf-8")

    def fail_stat(path):
        raise OSError(f"Cannot inspect {path}")

    monkeypatch.setattr(Path, "is_file", fail_stat)
    with pytest.raises(RuntimeReferenceError) as error:
        resolver.resolve(REFERENCE)
    assert error.value.code == "INVALID_WORKFLOW_TARGET"
    assert str(root) not in str(error.value)


def test_resolver_rechecks_manifest_on_each_call(tmp_path):
    resolver, root, manifest_path = _resolver(tmp_path, {REFERENCE: "research.yaml"})
    (root / "research.yaml").write_text("id: research\n", encoding="utf-8")
    assert resolver.resolve(REFERENCE) == (root / "research.yaml").resolve()

    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeReferenceError) as error:
        resolver.resolve(REFERENCE)
    assert error.value.code == "UNKNOWN_RUNTIME_REF"


def test_unreadable_or_malformed_manifest_has_safe_error(tmp_path):
    resolver, _, manifest_path = _resolver(tmp_path, {REFERENCE: "research.yaml"})
    manifest_path.write_text("{", encoding="utf-8")

    with pytest.raises(RuntimeReferenceError) as error:
        resolver.resolve(REFERENCE)
    assert error.value.code == "INVALID_WORKFLOW_MANIFEST"
    assert str(manifest_path) not in str(error.value)
