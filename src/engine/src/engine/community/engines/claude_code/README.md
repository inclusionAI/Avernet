# claude_code community engine

Open-source composition root for the **claude_code** engine (ACL-assembled).
Mirrors `engines/openclaw/`: this `engines/` package is the composition root
(free to import `core/adapters` + `plugins`), while the pure transport leaf
lives in `plugins/community/claude_code/`.

- **Assembly:** `engine.py` builds one `ClaudeCodePluginImpl` (community
  transport) and wraps it in the `core/adapters/claude_code/*` ACL adapters.
- **Transport:** WebSocket client to a claude_code gateway at
  `ws://127.0.0.1:18900` (override `CLAUDE_CODE_RELAY_URL` / `AICODING_RELAY_URL`).
  The engine itself is only the Python WS-client + ACL; the gateway is a separate
  Node service. **Unlike** the openclaw engine (whose gateway is external and
  **not** shipped), the claude_code gateway **is vendored in-tree** at
  [`../../claude_code_gateway/`](../../claude_code_gateway/) — build it in place
  (below) and point `CLAUDE_CODE_RELAY_URL` at it. `18789` is OpenClaw's port —
  not this one.
- **Delivery:** `router.py` mounts `/api/claude_code/ws` (community profile;
  wired in `di/optional_routers.py`).
- **Registration:** `__init__.py` self-registers `ClaudeCodeCommunityEngine`
  under the name `"claude_code"`. Mutually exclusive by profile with corp's
  export-excluded `engines/claude_code/` (same name) — see `engines/__init__.py`.

## How to run (end-to-end)

The engine is only a WS client + ACL — it needs the **claude_code gateway**
running to actually talk to Claude. Two processes:

**1. Start the gateway** (the Node service that drives Claude via
`@anthropic-ai/claude-agent-sdk` or the `claude` CLI):

```bash
# in the vendored gateway: ../../claude_code_gateway/ (community/claude_code_gateway/)
npm install
npm run prepublishOnly        # build (tshy) -> dist/
node dist/esm/server.js       # boots ws://127.0.0.1:18900
#   PORT=<n> to change the port; CLAUDE_BRIDGE=sdk (default) | cli
```

> For a full step-by-step (both engines, config reference, troubleshooting) see
> the open-source guide: [`docs/open-source-guide.md`](../../../../../docs/open-source-guide.md)
> ([中文](../../../../../docs/open-source-guide.zh-CN.md)).

> The gateway calls Claude on your behalf, so you need **your own Anthropic
> access**: an `ANTHROPIC_API_KEY` (SDK bridge) or a logged-in `claude` CLI
> (`CLAUDE_BRIDGE=cli`). That requirement is inherent to running Claude Code —
> the engine/gateway do not bundle credentials.

**2. Run the engine** (community profile), pointing it at the gateway:

```bash
export CLAUDE_CODE_RELAY_URL=ws://127.0.0.1:18900   # optional; this is the default
cd src/engine
ENGINE_PROFILE=community ./scripts/run.sh --port 20003
```

The engine connects to the gateway on first use; select the `claude_code`
engine via the EngineManager / the `/api/claude_code/ws` endpoint.

## Context Boundary

- **Upstream (imports):** `core/adapters/claude_code/*`, `core/{bash,engine}`,
  `plugins/community/claude_code` (transport leaf), `core/engine/registry`,
  `api/transport/ws_server` (router only), `di`.
- **Downstream (imported by):** `engines/__init__.py` (profile-gated load),
  `di/optional_routers.py` (router mount). No `core`/`api`/`plugins` module
  imports this package.
- **Profile:** community / test only. corp loads `engines/claude_code/` instead.

## Deliberate simplifications vs corp

The open-source community engine intentionally does **not** replicate some
corp-only chat behaviors. Each was verified against the in-repo frontend
(`src/frontend/`) as **not load-bearing**:

| Dropped corp behavior | Why it's safe to drop |
|---|---|
| `final`→synthetic-`delta` re-emission (corp `engines/claude_code/chat.py`) | The frontend (`AICodingParser._finalizeInFlightMessages`) renders the assistant bubble directly from the `final` frame's `message.content` text blocks. The load-bearing contract is "the `final` frame carries `message.content`", which the community transport preserves — locked by `test_chat_stream_final_frame_preserves_message_content`. |
| `/new` · `/reset` slash-command interception → `sessions.reset` + synthetic `agent/final` | The frontend never sends these as chat messages; session clear is a REST call (`DELETE /api/sessions/{id}/messages`). Zero references to `/new` / `/reset` / `agent/final` in `src/frontend/`. |

## Known non-alignments (documented, not implemented this round)

These corp behaviors are **not** replicated and would require extending the
native port signature. They are tracked as future work, not bugs — the community
engine is functionally correct without them:

- `cron run_job`: corp forwards `mode=force|due`; the community port
  `cron_run_job` has no `force`/`mode` param (force-run degrades to a due check).
- `session create`: corp sends `permissionMode=bypassPermissions` and falls back
  `cwd = request.cwd or default_cwd`; the community port has no `permission_mode`
  and passes `cwd` verbatim.
- `file remove` reports `path_type="file"` even for directories; `file list_dir`
  ignores `recursive` / `exclude_dirs`.
- HITL `resolve_*` do not validate `decision` / `action` against a whitelist
  (invalid values reach the relay, which rejects them).

Implement any of these as a separate ACL round (extend port → community impl →
local mock → adapter), following the openclaw reference.
