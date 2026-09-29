# AgentHub Development Baseline

Verified on 2026-09-28 against the inherited ChatDev source. This document records reproducible setup and regression evidence before AgentHub feature implementation.

## Repository Baseline

- Branch: `agenthub-dev`.
- ChatDev upstream base: `4fb2db0ea90375ce1059f44fe03ffbd191a7a169` (ancestor of the current branch).
- AgentHub HEAD at verification start: `ce79d43e254bf158bd1437ed5ac15a217e16477a`.
- The working tree already contained an intentional documentation-only update to `AGENTS.md`; no ChatDev source was changed for this baseline task.

## Environment

| Component | Required or configured version | Verified version used |
| --- | --- | --- |
| OS | Ubuntu 22.04 | Ubuntu 22.04.5 LTS (Jammy, x86_64) |
| Python | `>=3.12,<3.13` (`pyproject.toml`) | uv-managed host Python 3.12.13; Docker runtime Python 3.12.14 |
| uv | Project package manager | 0.10.9 (host) |
| Node.js | Frontend Dockerfile uses Node 24 | v24.10.0 (host build and dev server) |
| npm | Lockfile-based install | 11.6.1 (host) |
| Docker Engine | Docker required for container baseline | 29.2.1 |
| Docker Compose | Compose required for local services | v5.0.2 |

The host default `python3` is 3.13.5 and is outside the project's declared range. Use `uv` with Python 3.12 or the verified Docker runtime.

## Required System Dependencies

The Dockerfile builder installs `pkg-config`, `build-essential`, `python3-dev`, and `libcairo2-dev`; the runtime image installs `libcairo2`. Its full locked dependency installation completed in the Docker build.

On the Ubuntu host, `pkg-config`, `build-essential`, `python3-dev`, and the Cairo runtime library are installed. The missing package is `libcairo2-dev`: `pkg-config` cannot find `cairo.pc`, and `pycairo` fails during Meson dependency detection. Installing `libcairo2-dev` is the smallest identified host fix. This environment requires an interactive sudo password, which was unavailable; no system packages were changed.

After installing that package, the expected host setup is:

```bash
sudo apt-get update
sudo apt-get install -y libcairo2-dev
uv sync --frozen --python 3.12
```

The final command has **not** been verified on the host after the package installation. The same locked sync did succeed in the Dockerfile builder.

## Python Setup

Host command tested:

```bash
uv sync --frozen --python 3.12
```

Result: failed while building `pycairo==1.29.0`, with `Dependency "cairo" not found`. The dependency chain is `xhtml2pdf → svglib → rlpycairo → pycairo`. No project dependency or lockfile was changed.
The failed attempt left an incomplete ignored `.venv`; do not use it as a working environment. Rerun the full sync after installing the missing system package.

Verified full-environment alternative:

```bash
docker build --target runtime -t agenthub-baseline:4fb2db0 .
```

This built the runtime image with the locked dependencies and Cairo runtime library. `agenthub-baseline:4fb2db0` was then used to start the backend and execute the smoke test below.

## Backend

The repository's Docker runtime command is `python server_main.py --port 6400 --host ${BACKEND_BIND:-0.0.0.0}`. The verified Compose backend command is:

```bash
cp --no-clobber .env.example .env
docker compose config --quiet
docker compose up -d backend
```

The `cp` follows the README's documented configuration step. `.env` is ignored by Git and was copied from the checked-in example; no real credentials were added. `docker compose up -d backend` started successfully.

These endpoints returned HTTP 200 from the Compose backend:

- `/health`
- `/health/live`
- `/health/ready`
- `/api/workflows`

This verifies that the server process responds. These health endpoints do not establish that an LLM, tool, or other external provider is healthy.

## Frontend

Verified on the host:

```bash
cd frontend
npm ci --no-audit --no-fund
npm run build
npm run dev -- --host 127.0.0.1 --port 15173 --strictPort
```

`npm ci` installed 216 packages. Vite 7.3.0 built 161 modules and generated production assets in `frontend/dist/`. The dev server returned HTTP 200 and served the app entry point.

The standard frontend Docker image build did not finish: its `npm ci` process produced no output for about nine minutes and was stopped. The host install, production build, and dev server were successful. A temporary Compose override using `node:24-alpine` with the host `frontend/` and `frontend/node_modules/` bind-mounted did start the frontend service; its HTTP response and the Compose backend/workflow checks passed. This verifies service/network integration with the host-installed dependencies, but not the standard Dockerfile-built frontend image.

## Existing Workflow Smoke Test

Tested `yaml_instance/demo_loop_counter.yaml` through `POST /api/workflow/run` in the Compose backend, without LLM credentials. The response was `status=completed` with `final_message=Final summary released`.

The shared output directory contained `execution_logs.json`, `node_outputs.yaml`, and `workflow_summary.yaml`. This validates a non-LLM graph execution path; it does not validate real LLM Agent execution.

## Test Baseline

Tests ran with Python 3.12.14 and pytest 9.0.2 in the built Docker runtime. Commands were bounded to prevent indefinite blocking.

- Full suite: 73 tests collected. The run reached `tests/test_websocket_send_message_sync.py` after the preceding tests and timed out at 60 seconds (exit 124). A 20-second faulthandler trace captured the blocking call.
- WebSocket test file alone: 7 tests collected; timed out at 35 seconds (exit 124). Its 15-second faulthandler trace captured the same block.
- Passing subset: `timeout 60 docker exec agenthub-baseline-backend python -m pytest -q --ignore=tests/test_websocket_send_message_sync.py` — **66 passed** in 4.85 seconds.
- Warning: one `RequestsDependencyWarning` about the locked `urllib3` / `chardet` / `charset_normalizer` versions.

The first WebSocket test, `test_send_from_main_thread`, blocks in `WebSocketManager.connect()` while `_json_default()` repeatedly serializes a `MagicMock`. The test fixture injects an unconstrained `MagicMock` as `session_store`; its truthy `has_session()` result incorrectly takes the reconnect path, and the truthy mocked session snapshot is then serialized. The real `WorkflowSessionStore.has_session()` returns a boolean and `get_session_snapshot()` returns a dictionary or `None`, so the test double does not match the production contract. This reproduced test-only failure did not require changing production WebSocket behavior.

Smallest proposed test-only fixture correction (not applied): configure the unused store to represent a new session, for example:

```python
session_store = MagicMock()
session_store.has_session.return_value = False
session_store.get_session.return_value = None
session_store.get_session_snapshot.return_value = None
```

Pass this `session_store` to `WebSocketManager` in `_make_manager()`. No test change was made; review this proposed diff before applying it.

## Docker Baseline

- `docker build --target runtime -t agenthub-baseline:4fb2db0 .` completed and produced the image `agenthub-baseline:4fb2db0` (image ID `sha256:0fdc15cca60bd972c28798b9cce8e309659736a9922fe6c3e488b3a951544336`).
- The Dockerfile builder's `uv sync --no-cache --frozen` layer completed; its Cairo development packages resolved the host `pycairo` build blocker.
- The runtime image started the backend; the health endpoints and non-LLM workflow passed as recorded above.
- No LLM/tool provider health or execution is implied by this image build or smoke test.

## Compose Baseline

- The README-supported `cp --no-clobber .env.example .env` created the local ignored `.env`; `.env.docker` was already present.
- `docker compose config --quiet` passed.
- `docker compose up -d backend` and the backend HTTP/workflow checks passed. The Compose backend was stopped after verification.
- Standard `docker compose up --build -d` did not complete because the frontend image's `npm ci` stalled without output for about nine minutes. The build was stopped, so the standard Dockerfile-built frontend image remains unverified.
- Complete two-service Compose integration was separately verified with a temporary override that ran `node:24-alpine` and bind-mounted the host frontend source and installed `node_modules`. Both services started; the frontend returned HTTP 200, the backend endpoints returned HTTP 200, and `demo_loop_counter.yaml` completed successfully. The override was kept in `/tmp` and the Compose services were stopped after the checks. This is a local integration check, not the repository's standard reproducible frontend image build.
- Compose requires `.env` and `.env.docker`. Model calls additionally need a real `API_KEY` and the intended `BASE_URL` in the ignored local `.env`; none was supplied or used in this baseline verification. `.env.docker` supplies container environment settings. The published frontend port uses `${FRONTEND_PORT:-5173}` in `compose.yml`; configure Compose interpolation through the shell or its `.env`/explicit `--env-file`, otherwise the default is 5173. A service-level `env_file` does not itself supply this interpolation.

## External Capabilities Not Yet Verified

- Real LLM calls.
- External MCP services and external tools.
- Mem0 service connectivity (the local Mem0 unit tests passed).
- PDF export through `xhtml2pdf`.
- Standard Dockerfile-built Compose frontend image startup.

## Known Upstream Issues

The inherited WebSocket test fixture mismatch and the un-applied test-only proposal are documented under **Test Baseline**. No upstream tests or production source were modified.

## AgentHub Development Baseline

Future work can rely on the ChatDev 2.0 source at the recorded upstream commit, the Docker runtime image and backend startup, the four checked backend endpoints, and the non-LLM workflow smoke test with generated artifacts. The host frontend install/build/dev server, temporary Compose service integration, and 66-test subset also passed. A local host Python install still needs `libcairo2-dev`; the full inherited test suite remains blocked by the WebSocket test double, and the standard Dockerfile-built Compose frontend image remains unverified. No AgentHub business feature implementation is included in this baseline.
