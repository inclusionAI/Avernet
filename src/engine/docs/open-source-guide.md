# Open-Source Guide — Engine Adapter

> 🌏 中文版：[open-source-guide.zh-CN.md](./open-source-guide.zh-CN.md)

This is the getting-started guide for the **open-source (community) build** of the
engine adapter. It covers what ships, how to install and run it, and how to drive
each of the two bundled engines — **claude_code** (runnable out of the box) and
**openclaw** (requires an external gateway that is not part of this export).

> If you just want to see it work: jump to
> [Quick start — claude_code](#quick-start--claude_code).

---

## 1. What this is

The engine adapter is a small **Python / FastAPI service** (default port `20003`)
that exposes a uniform WebSocket + HTTP surface and translates it to a concrete
AI coding engine. It follows an **Anti-Corruption Layer (ACL)** design: the
adapter itself contains no engine internals — each engine is a thin **WebSocket
client** to an external *gateway* process that actually talks to the model.

```
                 ┌──────────────────────────┐
  your client    │   engine adapter (this)  │        engine gateway
  (WS / HTTP) ──▶ │   FastAPI · :20003       │ ──WS──▶ (drives the model)
                 │   /api/{engine}/ws       │
                 └──────────────────────────┘
                        │            │
             claude_code│            │openclaw
                        ▼            ▼
            ws://…:18900          ws://…:18789
      community/claude_code_gateway   (external OpenClaw
      (vendored — see §Quick start)    gateway — NOT included)
```

Two engines are registered in this build:

| Engine | OSS status | Gateway | Talks to |
|---|---|---|---|
| **claude_code** | ✅ Runnable out of the box | **Vendored** in `community/claude_code_gateway/` | Claude, via `@anthropic-ai/claude-agent-sdk` or the `claude` CLI |
| **openclaw** | ✅ Runnable with an external gateway | Public `openclaw` npm package **≥ 2026.5.x** (install separately) | OpenClaw's own model/agent setup |

---

## 2. What ships in the open-source build

The public export is the `community/` namespace only. Internal/corp code
(`corp/`), internal defaults, credentials, and internal build scripts are
stripped (see `.github-export-exclude`). Concretely, the export contains:

- `src/engine/src/engine/community/**` — the adapter, both engines, and the
  vendored `claude_code_gateway/` Node service.
- `start.py`, `pyproject.toml`, `scripts/run.sh`, `docs/`, `.env.example`.

All Python dependencies resolve from public PyPI; all gateway npm dependencies
resolve from the public npm registry. No credentials or internal endpoints are
bundled.

---

## 3. Prerequisites

- **Python 3.12+**
- **Node.js 18.19+** and **npm** (only for the claude_code gateway)
- Your **own Anthropic access** for claude_code: an `ANTHROPIC_API_KEY`, or a
  logged-in [`claude` CLI](https://docs.claude.com/en/docs/claude-code). The
  adapter and gateway do **not** bundle credentials — this is inherent to running
  Claude Code.

---

## 4. Install

```bash
cd src/engine

# Option A — pip (standard)
python -m venv .venv && source .venv/bin/activate
pip install .

# Option B — uv (faster; used by the project)
uv sync
```

This installs the `engine` package. The ASGI app is
`engine.community.api.app:app` and the entry point is `start.py`.

---

## 5. Quick start — claude_code

The claude_code engine is a WS client; it needs its gateway running. Two
processes: **(A)** the gateway, **(B)** the adapter.

### A. Build and start the gateway

The gateway is vendored in this repo — build it in place:

```bash
cd src/engine/src/engine/community/claude_code_gateway

npm install                 # public npm deps only
npm run prepublishOnly      # build (tshy) -> dist/

# Start it. Default bridge is the Anthropic SDK, needs an API key:
ANTHROPIC_API_KEY=sk-ant-... PORT=18900 node dist/esm/server.js
# -> "claude-code-gateway gateway ws: ws://127.0.0.1:18900 (bridge=sdk)"
```

Bridge modes:

- `CLAUDE_BRIDGE=sdk` (default) — uses `@anthropic-ai/claude-agent-sdk`; requires
  `ANTHROPIC_API_KEY`.
- `CLAUDE_BRIDGE=cli` — drives a locally installed, logged-in `claude` CLI; no
  API key env needed.

The gateway also serves a small debug panel (`public/`) and speaks a frame
protocol (`req`/`res`/`event`) documented in
`community/claude_code_gateway/README.md`.

### B. Start the adapter, pointed at claude_code

```bash
cd src/engine

CHAT_ENGINE=claude_code \
ENGINE_PROFILE=community \
CLAUDE_CODE_RELAY_URL=ws://127.0.0.1:18900 \
python start.py --port 20003
```

`CHAT_ENGINE=claude_code` is important — see the
[note on the default engine](#7-a-note-on-the-default-engine). `CLAUDE_CODE_RELAY_URL`
defaults to `ws://127.0.0.1:18900`, so you can omit it if you didn't change the
gateway port.

### C. Verify

```bash
curl -s localhost:20003/health      # -> 200
curl -s localhost:20003/readiness   # readiness probe
open  http://localhost:20003/docs   # OpenAPI UI
```

Then connect a WebSocket client to `ws://localhost:20003/api/claude_code/ws` and
send a `chat.send` frame (see the frame protocol in the gateway README), or use
the gateway's own debug panel.

---

## 6. openclaw engine

The openclaw engine is a WS client to an
**[OpenClaw](https://www.npmjs.com/package/openclaw)** gateway. Unlike
claude_code's gateway, it is **not vendored** here — but it is a public npm
package, so you install and run it yourself:

```bash
npm i -g openclaw            # use >= 2026.5.x — see the version note below
openclaw gateway run --dev --allow-unconfigured    # binds ws://127.0.0.1:18789
```

Then point the adapter at it:

```bash
CHAT_ENGINE=openclaw ENGINE_PROFILE=community \
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789 \
python start.py --port 20003
# -> "Connected to OpenClaw Gateway: protocol=4"
```

> **Gateway version matters (verified).** The shipped client speaks the OpenClaw
> connect protocol **v4**, which requires **openclaw ≥ 2026.5.x**. An older
> gateway (e.g. 2026.3.x, protocol v3) rejects the handshake with
> `Handshake failed: protocol mismatch`. If you need to target a different
> gateway protocol, override `OPENCLAW_GATEWAY_PROTOCOL_VERSION` (default `4`).

> As with claude_code, the gateway drives the model on your behalf, so it needs
> OpenClaw's own model/credential configuration for a full chat. The
> engine ↔ gateway handshake itself is verified working on openclaw 2026.6.x.

> The bundled `engine.json` also has `supervisorctl` start commands and internal
> filesystem paths for openclaw/hermes — legacy internal-deployment hints, not
> used by the community WS-client path.

---

## 7. Configuration reference

Environment variables (none are auto-loaded from `.env` — `export` them or pass
inline; `.env.example` documents the same set):

| Variable | Applies to | Default | Purpose |
|---|---|---|---|
| `ENGINE_PROFILE` | adapter | `community` | Profile to assemble. Use `community` for OSS. |
| `CHAT_ENGINE` | adapter | (from `engine.json`, i.e. `openclaw`) | Startup engine. Set to `claude_code` for the runnable path. |
| `CLAUDE_CODE_RELAY_URL` | claude_code | `ws://127.0.0.1:18900` | Gateway URL the engine connects to. (`AICODING_RELAY_URL` also accepted.) |
| `ANTHROPIC_API_KEY` | gateway | — | Anthropic key for the SDK bridge. |
| `CLAUDE_BRIDGE` | gateway | `sdk` | `sdk` (needs API key) or `cli` (needs logged-in `claude`). |
| `PORT` | gateway | `18900` | Gateway listen port. |
| `OPENCLAW_GATEWAY_URL` | openclaw | `ws://127.0.0.1:18789` | External OpenClaw gateway URL. |

### A note on the default engine

The shipped `engine.json` sets `engine.default = "openclaw"`, and the engine is
resolved as `CHAT_ENGINE` env → `engine.json` → `"openclaw"`. Because openclaw's
gateway is not included, a bare `python start.py` lands on a non-runnable engine.
**Always set `CHAT_ENGINE=claude_code`** for the out-of-the-box path (this is why
`.env.example` sets it).

---

## 8. API surface

The adapter exposes (see `community/api/app.py`):

| Path | Type | Purpose |
|---|---|---|
| `/api/{engine}/ws` | WebSocket | Path-pinned engine channel (`/api/claude_code/ws`, `/api/openclaw/ws`). |
| `/ws` | WebSocket | Default engine channel. |
| `/health`, `/readiness` | HTTP GET | Liveness / readiness. |
| `/docs`, `/redoc`, `/openapi.json` | HTTP | API documentation. |

The engine-facing gateway protocol (methods `chat.send`, `chat.abort`,
`session.new`, `approval.resolve`, …) is documented in
`community/claude_code_gateway/README.md`.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Engine boots but chat never responds; `connect failed … 18789` | Active engine is openclaw (the default) with no gateway | Set `CHAT_ENGINE=claude_code`. |
| `connect failed … 18900` | claude_code gateway not running | Start the gateway (§5A). |
| Gateway exits immediately / auth errors | No `ANTHROPIC_API_KEY` and `CLAUDE_BRIDGE=sdk` | Provide a key, or use `CLAUDE_BRIDGE=cli` with a logged-in `claude`. |
| `Cannot find module …/dist/esm/server.js` | Gateway not built | Run `npm install && npm run prepublishOnly` in `claude_code_gateway/`. |
| `/config` returns "only support 'openclaw'" | That legacy debug endpoint is openclaw-only | Not needed for claude_code; ignore. |

---

## 10. Project layout

```
src/engine/
├── start.py                     # entry point → uvicorn engine.community.api.app:app
├── pyproject.toml               # public PyPI deps
├── .env.example                 # documented env vars
└── src/engine/community/
    ├── api/                     # FastAPI app, WS endpoints
    ├── core/ · plugin_api/ · kernel/   # ACL layers (adapters, ports, primitives)
    ├── di/                      # composition root (profile wiring)
    ├── engines/
    │   ├── claude_code/         # claude_code composition root (WS client + ACL)
    │   └── openclaw/            # openclaw composition root (WS client + ACL)
    ├── plugins/                 # concrete transport leaves
    └── claude_code_gateway/     # vendored Node gateway for claude_code
```

For architecture details see `docs/community-corp-architecture.md` and
`docs/heterogeneous-engine-architecture.md`.
