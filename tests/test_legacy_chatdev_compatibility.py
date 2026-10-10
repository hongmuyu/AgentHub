"""T45: bounded checks for the original Web workflow and session contracts."""

import asyncio
import io
import json
import zipfile

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import runtime  # noqa: F401  # Initialize the existing runtime/check import cycle first.
from entity.messages import Message, MessageRole
from server.models import WorkflowRequest
from server.routes import artifacts, execute, sessions, uploads, workflows
from server.services.artifact_events import ArtifactEvent
from server.services.attachment_service import AttachmentService
from server.services.session_store import SessionStatus, WorkflowSessionStore
from server.services.websocket_executor import WebSocketGraphExecutor
from server.services.websocket_manager import WebSocketManager


class Socket:
    def __init__(self):
        self.messages = []

    async def accept(self):
        pass

    async def send_text(self, payload):
        self.messages.append(json.loads(payload))

    async def close(self, **kwargs):
        pass


def _manager(tmp_path, monkeypatch):
    manager = WebSocketManager(
        session_store=WorkflowSessionStore(),
        attachment_service=AttachmentService(root=tmp_path / "attachments"),
    )
    monkeypatch.setattr(manager, "_start_gc", lambda: None)
    return manager


def test_legacy_execute_uses_default_service_and_replays_native_messages(tmp_path, monkeypatch):
    workflow = tmp_path / "manual.yaml"
    workflow.write_text(yaml.safe_dump({
        "version": "0.4.0", "vars": {},
        "graph": {
            "id": "manual", "start": ["echo"], "end": ["echo"],
            "nodes": [{"id": "echo", "type": "passthrough",
                       "config": {"only_last_message": True}}],
            "edges": [],
        },
    }), encoding="utf-8")
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(execute, "ensure_known_session", lambda *_args, **_kwargs: manager)
    monkeypatch.setattr(manager.workflow_run_service, "_resolve_yaml_path", lambda _: workflow)
    monkeypatch.setattr("server.services.workflow_run_service.WARE_HOUSE_DIR", tmp_path / "warehouse")
    original = WebSocketGraphExecutor.execute_graph_async
    observed = {}

    async def capture(self, task_input):
        observed["recorder"] = self._get_execution_context().outcome_recorder
        await original(self, task_input)
        observed["output"] = self.get_final_output_message()

    monkeypatch.setattr(WebSocketGraphExecutor, "execute_graph_async", capture)
    original_start = manager.workflow_run_service.start_workflow
    finished = asyncio.Event()

    async def start(*args, **kwargs):
        observed["call"] = (args, kwargs)
        try:
            await original_start(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(manager.workflow_run_service, "start_workflow", start)

    async def run():
        socket = Socket()
        session_id = await manager.connect(socket, "legacy-session")
        response = await execute.execute_workflow(WorkflowRequest(
            yaml_file="manual.yaml", task_prompt="hello", session_id=session_id,
            attachments=[],
        ))
        await asyncio.wait_for(finished.wait(), timeout=5)
        session = manager.session_store.get_session(session_id)
        assert response == {
            "status": "started", "session_id": session_id,
            "message": "Workflow execution started",
        }
        assert observed["call"] == (
            (session_id, "manual.yaml", "hello", manager),
            {"attachments": [], "log_level": None},
        )
        assert observed["recorder"] is None
        assert isinstance(observed["output"], Message)
        assert observed["output"].role == MessageRole.USER
        assert observed["output"].text_content() == "hello"
        assert session.status == SessionStatus.COMPLETED
        assert session.yaml_file == "manual.yaml"
        assert session.task_attachments == []
        assert [message["type"] for message in socket.messages if message["type"] in {
            "connection", "workflow_started", "workflow_completed",
        }] == ["connection", "workflow_started", "workflow_completed"]
        assert [message["type"] for message in session.message_buffer if message["type"] in {
            "workflow_started", "workflow_completed",
        }] == ["workflow_started", "workflow_completed"]
        manager.disconnect(session_id)
        replay = Socket()
        assert await manager.connect(replay, session_id) == session_id
        assert replay.messages[0]["type"] == "connection"
        assert [message["type"] for message in replay.messages[1:-1]] == [
            message["type"] for message in session.message_buffer
        ]
        assert replay.messages[-1]["type"] == "session_resumed"
        assert replay.messages[-1]["data"]["status"] == "completed"

    asyncio.run(run())


def test_legacy_human_input_and_status_messages(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)

    async def run():
        socket = Socket()
        session_id = await manager.connect(socket, "input-session")
        session = manager.session_store.create_session(
            yaml_file="manual.yaml", task_prompt="hello", session_id=session_id,
        )
        manager.session_controller.set_waiting_for_input(session_id, "human", {})
        await manager.handle_message(session_id, json.dumps({
            "type": "human_input", "data": {"input": "approved", "attachments": ["upload-1"]},
        }))
        assert session.human_input_future.result() == {
            "text": "approved", "attachments": ["upload-1"],
        }
        await manager.handle_message(session_id, '{"type":"get_status"}')
        assert [message["type"] for message in socket.messages] == [
            "connection", "input_received", "status",
        ]
        assert socket.messages[-1]["data"]["session_id"] == session_id

    asyncio.run(run())


def test_legacy_upload_artifact_and_session_download_routes(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    session_id = "legacy-download"
    session = manager.session_store.create_session(
        yaml_file="manual.yaml", task_prompt="hello", session_id=session_id,
    )
    monkeypatch.setattr("server.state.websocket_manager", manager)
    warehouse = tmp_path / "warehouse"
    session_path = warehouse / f"session_{session_id}"
    session_path.mkdir(parents=True)
    (session_path / "result.txt").write_text("legacy result", encoding="utf-8")
    monkeypatch.setattr(sessions, "WARE_HOUSE_DIR", warehouse)
    app = FastAPI()
    for router in (uploads.router, artifacts.router, sessions.router):
        app.include_router(router)

    with TestClient(app) as client:
        upload = client.post(f"/api/uploads/{session_id}", files={
            "file": ("notes.txt", b"legacy attachment", "text/plain"),
        })
        assert upload.status_code == 200
        attachment_id = upload.json()["attachment_id"]
        assert upload.json()["name"] == "notes.txt"
        listed = client.get(f"/api/uploads/{session_id}")
        assert listed.status_code == 200
        assert attachment_id in listed.json()["attachments"]

        store = manager.attachment_service.get_attachment_store(session_id)
        artifact = store.register_bytes(b"legacy artifact", display_name="output.txt", mime_type="text/plain")
        artifact_id = artifact.ref.attachment_id
        session.artifact_queue.append_many([ArtifactEvent(
            node_id="echo", attachment_id=artifact_id, file_name="output.txt",
            relative_path="output.txt", workspace_path="output.txt",
            mime_type="text/plain", size=artifact.ref.size, sha256=artifact.ref.sha256,
            data_uri=None,
        )])
        events = client.get(f"/api/sessions/{session_id}/artifact-events", params={"wait_seconds": 0})
        assert events.status_code == 200
        assert events.json()["events"][0]["attachment_id"] == artifact_id
        meta = client.get(f"/api/sessions/{session_id}/artifacts/{artifact_id}")
        assert meta.status_code == 200
        assert meta.json()["name"] == "output.txt"
        stream = client.get(f"/api/sessions/{session_id}/artifacts/{artifact_id}", params={
            "mode": "stream", "download": "true",
        })
        assert stream.status_code == 200
        assert stream.content == b"legacy artifact"
        assert stream.headers["content-disposition"] == 'attachment; filename="output.txt"'

        download = client.get(f"/api/sessions/{session_id}/download")
        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            assert archive.read(f"session_{session_id}/result.txt") == b"legacy result"


def test_legacy_workflow_list_and_yaml_get_routes(tmp_path, monkeypatch):
    (tmp_path / "manual.yaml").write_text("name: manual\n", encoding="utf-8")
    monkeypatch.setattr(workflows, "YAML_DIR", tmp_path)
    app = FastAPI()
    app.include_router(workflows.router)
    with TestClient(app) as client:
        listed = client.get("/api/workflows")
        content = client.get("/api/workflows/manual.yaml/get")
    assert listed.status_code == 200
    assert "manual.yaml" in listed.json()["workflows"]
    assert content.status_code == 200
    assert content.json()["content"] == "name: manual\n"
