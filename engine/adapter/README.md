# OpenClaw Engine — Community (Open-Source) Distribution

This is the open-source **community** build of the OpenClaw engine adapter: a
Python/FastAPI service that exposes a uniform WebSocket + HTTP surface and
translates it to an AI coding engine (claude_code, openclaw) over an
Anti-Corruption Layer. It ships **without** any internal (corp) code — the
`engine/corp/` tree is physically absent from this distribution, and the
community runtime imports zero corp modules.

This README is staged to the distribution root by
`scripts/build_community_dist.py` so the assembled community tree `uv sync`s and
builds standalone (the `pyproject.toml` here declares `readme = "README.md"`).

## Run it

See the full getting-started guide (both engines, config, troubleshooting):

- [`docs/quick-start.md`](../../docs/quick-start.md)
- 中文版：[`docs/quick-start.zh-CN.md`](../../docs/quick-start.zh-CN.md)

Quick start (claude_code, the out-of-the-box engine):

```bash
pip install .            # or: uv sync
# start the vendored gateway (community/claude_code_gateway), then:
CHAT_ENGINE=claude_code ENGINE_PROFILE=community python start.py --port 20003
```

See the [file upload error contract](docs/file-upload-api.md) for the
read-only filesystem response and compatibility guarantees.

### Chat error compatibility

OpenClaw-compatible OpenClaw/Hermes WebSocket chat errors expose nonempty
`payload.errorCode` and `payload.errorMessage` strings. Existing codes,
messages, event names and extension fields are preserved; missing runtime
codes use `UNAVAILABLE`. OpenClaw failed ACKs keep `error.code` and
`error.message` and do not emit an additional error event. There is no shared
error-code enum that clients must recognize. Normal, aborted and final events,
other engines, and existing retry/termination timing are unchanged.

### Session runtime observation

`GET /api/sessions/runtime/status?session_id=...` checks session history access
before reading process-local OpenClaw WebSocket and A2A run records. Both
transports expose the same run identity, start time, state and event sequence;
A2A tool, assistant and lifecycle events advance its existing kernel sequence,
even when replay buffering is exhausted. Queries do not execute or resume work.
An active run takes precedence over retained terminal records; multiple active
runs produce `AMBIGUOUS_RUN`. With only terminal records from both transports,
the most recently started observed run is returned. Terminal records expire
with their transport's existing retention policy (180 seconds).

Observation remains process-local: `NO_ACTIVE_RUN` is not proof that a session
has finished, and callers must retain their existing fallback for unavailable,
missing or ambiguous observations. Callers do not select a transport.

### Local Skill package publication

An Engine advertising `skills.local_package.parallel_targets.v1` allows
overlapping `POST /api/skills/local/apply` requests to distinct Skill names in
the same Bot layout. Publication and rollback operate within each target.
Callers still serialize writes to a single target and coordinate layout
changes. The capability does not provide remote fencing for a timed-out
request. An Engine exposing only `skills.local_package.apply.v1` supports
whole-package publication without advertising that concurrency contract.

## Layout

```
src/engine/community/    # api → core → plugin_api → kernel + community impls
  ├── api/               # FastAPI app, WS endpoints (/api/{engine}/ws)
  ├── di/                # composition root (community profile)
  ├── engines/           # claude_code + openclaw (WS clients + ACL)
  ├── plugins/           # transport leaves
  └── claude_code_gateway/  # vendored Node gateway for claude_code
```
