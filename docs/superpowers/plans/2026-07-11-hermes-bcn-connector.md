# Hermes BCN Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native Hermes choice to Avernet's BCN onboarding flow and run a standalone connector that bridges the existing BCN WebSocket protocol to a configured local Hermes Dashboard.

**Architecture:** The frontend selects one of two resource-template pairs without changing token issuance. A self-contained Python connector owns the BCN and Hermes WebSocket clients, persists credentials and per-group Hermes sessions under the selected `HERMES_HOME`, and translates BCN requests into Hermes JSON-RPC events. The existing BCN Rust service and OpenClaw path remain unchanged.

**Tech Stack:** React 18, TypeScript, Jest/ts-jest, Python 3.11+, `asyncio`, `websockets>=14,<16`, shell installer, existing Hermes Dashboard JSON-RPC.

**Global Constraints**

- Work only on `design/hermes-bcn-entry`, based on the repository's real upstream default `origin/dev`.
- Preserve the existing OpenClaw behavior and keep OpenClaw selected by default.
- Do not add a Provider/Webhook platform, BaaS dependency, BCN server special case, or model/provider/key configuration UI.
- Use BCN protocol version 2 and Hermes Dashboard `/api/ws` JSON-RPC directly.
- Never print BCN bot tokens, human registration tokens, Hermes API keys, or the generated Dashboard token.
- Persist connector credentials atomically with mode `0600`.
- `chat.inject` must ACK without prompting Hermes; buffer at most 256 observations and 64 KiB per group.
- Serialize turns within one group while allowing different groups to run concurrently.
- Use `USE_CN_MIRROR=1`/`--china-mirror` for the public PyPI mirror, while honoring an explicit `PIP_INDEX_URL` first.
- Keep the existing service on port `8000` running; use a separate free port for the feature frontend.

---

### Task 1: Frontend resource selection and onboarding UI

**Files:**
- Modify: `src/frontend/src/shell/types.ts`
- Modify: `src/frontend/src/shell/extension.ts`
- Create: `src/frontend/src/pages/BcnHome/lib/botAccess.ts`
- Create: `src/frontend/src/pages/BcnHome/lib/botAccess.test.ts`
- Modify: `src/frontend/src/pages/BcnHome/components/AccessSection.tsx`
- Modify: `src/frontend/src/pages/GroupChat/components/AddBotGuideModal.tsx`

- [ ] Add a failing Jest test covering OpenClaw default selection, Hermes manual/automatic template selection, `{token}` replacement, and hiding an engine whose two templates are `null`.
- [ ] Run `npm test -- --runInBand src/pages/BcnHome/lib/botAccess.test.ts` from `src/frontend` and confirm the missing helper/resource behavior fails.
- [ ] Add the two nullable Hermes resource fields and open-source command/instruction defaults using the existing `{token}` placeholder.
- [ ] Implement a small pure helper that returns visible engine choices and the selected engine's two access methods.
- [ ] Add a compact `OpenClaw | Hermes` segmented control to both onboarding components and source all labels/templates from the shared helper.
- [ ] Keep existing clipboard and six-hour token behavior unchanged; update engine-specific copy only.
- [ ] Re-run the focused Jest test and confirm it passes.
- [ ] Run `npm run build:oss` from `src/frontend` with `NODE_OPTIONS=--max-old-space-size=4096`.
- [ ] Commit with message `feat(frontend): add Hermes BCN onboarding choice`.

### Task 2: Protocol-tested standalone Hermes connector

**Files:**
- Create: `src/bcs/connectors/hermes/hermes_bcn.py`
- Create: `src/bcs/connectors/hermes/tests/test_bcs_protocol.py`
- Create: `src/bcs/connectors/hermes/tests/test_hermes_gateway.py`
- Create: `src/bcs/connectors/hermes/tests/test_bridge.py`

- [ ] Write failing async tests around fake BCN and Hermes WebSocket servers for handshake/reconnect token persistence, immediate `chat.send` ACK then delta/final, silent observation buffering, session resume, abort, history, and unknown methods.
- [ ] Run `python -m unittest discover -s src/bcs/connectors/hermes/tests -v` in a connector test environment containing `websockets>=14,<16`; confirm tests fail because the connector is absent.
- [ ] Implement `AtomicJsonStore` with temporary-file rename, owner-only file mode, and redacted structured logging.
- [ ] Implement `HermesClient` for authenticated `/api/ws`, request correlation, `session.create`, `session.resume`, `session.history`, `prompt.submit`, and `session.interrupt`.
- [ ] Implement `BcsClient` for protocol-v2 `bot.connect`, heartbeat, reconnect, request ACK/response, and event output.
- [ ] Implement `HermesBcnBridge` with per-group locks, cross-group concurrency, bounded observation buffers, persistent stored-session mapping, and terminal error translation.
- [ ] Implement `run` so it can either connect to an existing Dashboard URL/token or own a private loopback Dashboard child for the selected Hermes profile.
- [ ] Re-run the connector test suite and confirm all tests pass.
- [ ] Run `python -m py_compile src/bcs/connectors/hermes/hermes_bcn.py`.
- [ ] Commit with message `feat(bcs): add native Hermes connector`.

### Task 3: Registration, lifecycle commands, and installer instructions

**Files:**
- Modify: `src/bcs/connectors/hermes/hermes_bcn.py`
- Create: `src/bcs/connectors/hermes/tests/test_cli.py`
- Create: `src/bcs/docs/install-instructions/install-hermes.sh`
- Create: `src/bcs/docs/install-instructions/install-hermes.md`
- Modify: `src/bcs/docs/install-instructions/README.md`
- Modify: `src/bcs/docs/install-instructions/README.zh-CN.md`

- [ ] Add failing CLI tests for protected credential writes, registration response validation, idempotent start, stale PID repair, status, stop ownership, and mirror precedence.
- [ ] Run the focused CLI tests and confirm the missing commands fail.
- [ ] Add `register`, `start`, `stop`, and `status` subcommands without introducing a general connector framework.
- [ ] Make registration call the existing `POST /register?token=...&bot-name=...` endpoint and persist successful credentials before any later setup step.
- [ ] Add the self-service installer with Hermes/profile/tool preflight, atomic connector download, connector-only venv, `websockets>=14,<16`, China mirror support, and health wait.
- [ ] Add the bot-assisted Markdown instruction and index links.
- [ ] Re-run all connector tests and `bash -n src/bcs/docs/install-instructions/install-hermes.sh`.
- [ ] Commit with message `feat(bcs): add Hermes connector installer`.

### Task 4: Local product-manager rollout and visible acceptance

**Files:**
- No tracked repository files unless a test-discovered fix is required.
- Local state only: `~/.hermes/profiles/avernet-product-manager`, its `bcn/session.json`, `bcn/groups.json`, PID, and log files.

- [ ] Reconfirm existing services and ports; do not stop the frontend on `8000` or existing backend/BCS processes.
- [ ] Clone the configured `avernet-default` Hermes profile to `avernet-product-manager` without exposing keys, and set its role prompt to product manager.
- [ ] Obtain the current human registration token locally without printing it; register the display name `产品经理` through the new command.
- [ ] Start the connector against the existing BCN and verify the bot is online.
- [ ] Install frontend dependencies with `npm_config_registry=https://registry.npmmirror.com` if needed, then start the feature frontend on a free port such as `8001`.
- [ ] Open the feature page and verify both the landing section and Connect Bot dialog display `OpenClaw | Hermes`, with Hermes templates selected correctly.
- [ ] Send a real mention in group `bcs_grp_98ee7619-cdd7-4070-8f30-d0a16407c9e8`; confirm a response from the configured Hermes backend and verify the same bot UUID reconnects after a connector restart.
- [ ] Only after the real response succeeds, stop/remove the old OpenClaw product-manager bot plus `glm5` and backend-created `developer`; preserve `CEO`, `研发`, `验证`, and `客服`.
- [ ] Verify the final visible roster contains exactly those four OpenClaw roles plus Hermes `产品经理`.

### Task 5: Branch-wide verification and review

- [ ] Run frontend focused tests and `npm run build:oss`.
- [ ] Run all Hermes connector tests and Python syntax compilation.
- [ ] Run shell syntax validation for the installer.
- [ ] Inspect `git diff --check`, `git status --short`, and the branch diff against `origin/dev`; exclude generated `.codegraph` and local secrets/state.
- [ ] Perform a final code review against `docs/superpowers/specs/2026-07-11-hermes-bcn-connector-design.md`, fix Critical/Important findings, and repeat affected tests.
- [ ] Leave the feature frontend and Hermes connector running, report the URL, exact live acceptance result, and any deliberately deferred non-goals.
