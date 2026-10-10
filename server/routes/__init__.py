"""Aggregates API routers."""

from . import artifacts, execute, execute_sync, health, sessions, uploads, vuegraphs, workflows, websocket, batch, tools, agenthub, agenthub_tasks, agenthub_metrics

ALL_ROUTERS = [
    health.router,
    agenthub.router,
    agenthub_tasks.router,
    agenthub_metrics.router,
    vuegraphs.router,
    workflows.router,
    uploads.router,
    artifacts.router,
    sessions.router,
    batch.router,
    execute.router,
    execute_sync.router,
    tools.router,
    websocket.router,
]

__all__ = ["ALL_ROUTERS"]
