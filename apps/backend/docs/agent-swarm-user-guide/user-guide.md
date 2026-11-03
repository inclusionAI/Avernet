# Agent Swarm — User Guide

> **Surface:** HTTP API under `/openapi/v1/agentswarm` (version `v1`, additive-only).
> **Code authority:** [`agentswarm/router.py`](../../src/agentclaw/community/adapters/http/agentswarm/router.py) + [`schemas.py`](../../src/agentclaw/community/adapters/http/agentswarm/schemas.py) in the community backend.
> **Manifest authority:** [`bot-config-manifest/manifest-schema.zh-CN.md`](../bot-config-manifest/manifest-schema.zh-CN.md) (schema v1 grammar) and [`examples.zh-CN.md`](../bot-config-manifest/examples.zh-CN.md).
> **Source of truth:** this English version; [`user-guide.zh-CN.md`](./user-guide.zh-CN.md) is its paired translation.
> **Last verified against code:** 2026-10-21.

This guide shows how to use the Agent Swarm API: submit a run, watch it
provision and execute, and read its result. It also explains the per-agent
`config_manifest` — a full bot configuration document inside every agent
entry — and the create-with-manifest surface that document grammar belongs to.

---

## 1. What an Agent Swarm run is

A **run** pursues a `goal` with a set of **agents** you declare up front:

- `kind: "llm"` — an in-process completion persona. Fast (seconds), no
  container.
- `kind: "bot"` — a real provisioned bot persona: the platform creates a bot
  on the fly (optionally from your `config_manifest`), the agent runs as that
  bot, and the run's teardown deletes the bot when the run ends.

The orchestrator decomposes the goal, spawns/executes agents in rounds
(parallel waves or sequential dependencies, decided by the goal text), and
finishes with a `final_answer`. A run ends in one of the terminal statuses
(§6); everything before that is observable through polling or a live SSE
stream.

Key facts before the first call:

| Topic | Rule |
| --- | --- |
| Authentication | Every route resolves an **authenticated end-user** — an anonymous caller gets `401`. Only `/health` and `/ready` are probe-open. |
| Tenancy | The tenant is an **explicit per-request parameter**: body field `tenant` on submit, query parameter `?tenant=` everywhere else. Blank is refused at validation (`422`). Cross-tenant access is refused at the data layer and is indistinguishable from an unknown run (`404` either way). |
| Versioning | The surface is `/openapi/v1/agentswarm`, additive-only within `v1`. Renames or removals require a new `/v2/` prefix. |
| Idempotency | An identical `idempotency_key` (with an identical submission) joins the **same run** rather than starting a second one. |

## 2. Base URL and credentials

All examples use a `BASE` variable:

```bash
# Local singlebox stack
BASE=http://127.0.0.1:8000/openapi/v1/agentswarm

# …or through your deployment's gateway host
BASE=http://<gateway-host>/openapi/v1/agentswarm
```

Authentication is deployment-specific: gateway deployments resolve the
end-user from the request principal (for example a browser session cookie,
with matching `Referer`/`Origin` if your auth provider checks it), while
local singlebox profiles resolve an anonymous or dev principal. Whatever the
profile, unauthenticated callers receive `401` from every route except
`/health` and `/ready`.

## 3. Five-minute quickstart

Submit one `llm` agent, then poll to terminal:

```bash
# 1. submit — returns {"run_id": "..."}
curl -sS -X POST "$BASE/runs" -H 'Content-Type: application/json' -d '{
  "tenant": "teamclaw",
  "goal": "Say hello world",
  "agents": [
    {"name": "greeter_bot", "role": "hello world producer", "kind": "llm",
     "instructions": "Respond with exactly the single word: hello world"}
  ],
  "options": {"model_name": "DeepSeek-V4.1-Flash"}
}'
# → {"run_id":"a1b2c3…"}

# 2. poll until the status is terminal (§6)
curl -sS "$BASE/runs/$RUN_ID?tenant=teamclaw"
# → {"run_id":"a1b2c3…","status":"COMPLETED","termination_reason":"finish",
#    "final_answer":"hello world","error":null,"started_at":…,"ended_at":…}
```

Prefer to block? Add `?wait=true` and the same call answers the full summary
only after the run is terminal (`llm` runs typically complete in seconds).

## 4. Interface index

| # | Method | Path | Purpose |
| --- | --- | --- | --- |
| 1 | POST | `/runs` | Submit a run (async; `?wait=true` for sync) |
| 2 | POST | `/runs/{run_id}/cancel` | Request cancellation |
| 3 | GET | `/runs/{run_id}` | Run summary |
| 4 | GET | `/runs` | List runs (paged, filterable) |
| 5 | GET | `/runs/{run_id}/detail` | Full detail: agents, rounds, guards, messages |
| 6 | GET | `/runs/{run_id}/events` | Live SSE stream |
| 7 | GET | `/runs/{run_id}/report` | HTML report page |
| 8 | GET | `/health` | Liveness probe (auth-free) |
| 9 | GET | `/ready` | Readiness probe (auth-free) |

## 5. Endpoint reference

### 5.1 Submitting a run — `POST /runs`

Request body (`SubmitRunDTO`, strict — unknown keys are `422`):

| Field | Type | Required | Constraints |
| --- | --- | --- | --- |
| `tenant` | string | ✅ | 1–64 chars |
| `goal` | string | ✅ | ≤ 8,000 chars; what the orchestrator decomposes |
| `agents` | array | ✅ | 1–64 entries (table below) |
| `provider` | enum | — | `default` \| `stub` \| `avernet` |
| `model` | string | — | Run-level model override; **outranks** `options.model_name` |
| `idempotency_key` | string | — | ≤ 190 chars; same key joins the same run |
| `options` | object | — | Operational overrides (table below) |

Each `agents[]` entry (`AgentSpecDTO`, strict):

| Field | Type | Required | Constraints / defaults |
| --- | --- | --- | --- |
| `name` | string | ✅ | ≤ 128 chars |
| `role` | string | ✅ | ≤ 256 chars |
| `instructions` | string | — | ≤ 20,000 chars; the persona text |
| `kind` | enum | — | `llm` (default) \| `bot` |
| `model` | string | — | Per-agent model |
| `engine` | string | — | Serving engine for a bot agent |
| `bot_type` | string | — | Defaults to `personal` |
| `identity_file` | string | — | Defaults to `SOUL.md` |
| `metadata` | object | — | ≤ 32 entries; key ≤ 200 chars, value ≤ 2,000 chars |
| `config_manifest` | string | — | **≤ 200,000 chars.** A full bot config manifest YAML document — see §8 |

`options` (`RunOptionsDTO`, strict) — every value falls back to the
deployment's `agentswarm.orchestration.*` configuration when omitted, and
the deployment stays the authority in one of three ways:

- **fallback** — `model_name` names the model for agents that don't set
  their own; the deployment default serves when omitted.
- **tighten-only** — `max_width`, `max_attempts`, `slice_max_seconds`, and
  `per_attempt_timeout_seconds` are honored but clamped by the
  deployment's configured value.
- **raise-to-slice-room** — `setup_timeout_seconds` re-budgets one
  agent's provisioning phase (spawn + authorization park + READY): the
  deployment's `setup_timeout` is the fallback while a requested value
  wins **up to the slice room the deployment leaves**
  (`slice_max_seconds − per_attempt_timeout`). Parked time waiting on
  human consent never counts against it.

`max_turns` and `max_total_agents` are persisted with the run but do not
override the deployment's orchestrator budgets.

| Field | Range | Meaning |
| --- | --- | --- |
| `model_name` | ≤ 128 chars | Model for agents that don't set their own |
| `slice_max_seconds` | (0, 3600] | Slice deadline per-run (tighten-only) |
| `per_attempt_timeout_seconds` | (0, 600] | One agent-attempt bound (tighten-only) |
| `setup_timeout_seconds` | (0, 900] | Agent provisioning budget (raise up to the deployment's slice room) |
| `max_turns` | [1, 50] | Persisted only — deployment budget governs |
| `max_total_agents` | [1, 64] | Persisted only — deployment budget governs |
| `max_width` | [1, 16] | Max in-flight agents at once (tighten-only) |
| `max_attempts` | [1, 3] | Per-agent fresh-context retry cap (tighten-only) |

Responses:

- **`200`** — `{"run_id": "…"}` (async submit, the default).
- **`200` with `?wait=true`** — the full run summary (§5.3's shape), answered
  only after the run is terminal or its deadline hits.
- **`429`** — admission refused (§7). **`422`** — shape or validation error.
  Submits — accepted and rejected — land in the append-only audit trail
  (`ac_agent_swarm_audit`).

### 5.2 Listing runs — `GET /runs?tenant=…`

Query parameters: `page` (≥ 1), `page_size` (1–100, default 20), optional
`status` (a `RunStatus` value). Returns
`{"items":[summary…], "total":…, "page":…, "page_size":…}`.

### 5.3 One run — `GET /runs/{run_id}?tenant=…`

Returns the run summary:

```json
{"run_id":"…", "status":"COMPLETED", "termination_reason":"finish",
 "final_answer":"hello brave new wide world", "error":null,
 "started_at":1760000000, "ended_at":1760000012}
```

Unknown run (or another tenant's run): `404 {"detail":"run not found"}`.

### 5.4 Full detail — `GET /runs/{run_id}/detail?tenant=…`

The summary plus everything the run recorded: `goal`, `provider`, the
declared `agent_catalog`, every spawned agent (with `bot_id` / `bot_state`
for bot kind, its `attempts`, the full `attempt_ledger`, and `messages`
including orchestrator decisions and `token_usage`), the `rounds` (stage
index, cohort), and `guards` events.

### 5.5 Cancelling — `POST /runs/{run_id}/cancel?tenant=…`

`200 {"run_id":"…","status":"RUNNING"}` — `status` is the run's status
immediately after the cancel request (the orchestrator observes the request
and ends the run; it settles as `CANCELLED`). Unknown run: `404`.

### 5.6 Live stream — `GET /runs/{run_id}/events?tenant=…`

Server-Sent Events; each `data:` line is
`{"event":…, "run_id":…, "data":…}`:

| Event | `data` payload | Meaning |
| --- | --- | --- |
| `status` | `{"status":"RUNNING"}` | Emitted once at stream start |
| `agent` | `{agent_id, name, status, kind, spawn_round}` | One per received agent cursor (replays from 0 on each new stream) |
| `round` | `{round_id, stage_index, cohort_size}` | Round cohort |
| `guard` | `{"kind":"width_exceeded"}` | A guard fired (see §6) |
| `progress` | `{"status":"RUNNING"}` | ~1 Hz heartbeat between deltas |
| `terminal` | `{"status":"COMPLETED"}` | Terminal — the stream closes; the `id: terminal` frame also asks a reader to reconnect every 5 s while the run was live |

```bash
curl -sS -N "$BASE/runs/$RUN_ID/events?tenant=teamclaw"
event: status
data: {"event":"status","run_id":"…","data":{"status":"RUNNING"}}
…
event: terminal
id: terminal
data: {"event":"terminal","run_id":"…","data":{"status":"COMPLETED"}}
```

Each agent/round/guard event is emitted once per stream (ordered by durable
row cursors) — a fresh connection replays the whole run to date, then tails.

### 5.7 Report and probes — `/report`, `/health`, `/ready`

- `GET /runs/{run_id}/report?tenant=…` — a small HTML page (run id, goal,
  status, termination, agent count) suitable for linking into a ticket,
  served with `Cache-Control: no-store`.
- `GET /health` — auth-free `{"status":"ok","version":"v1"}`; `version` is
  the machine-read keyword for fleet pinning.
- `GET /ready` — auth-free `{"ready":true}`.

## 6. Vocabulary

**Run status (`status`)** — the first is non-terminal, the rest terminal:

| Status | Meaning |
| --- | --- |
| `RUNNING` | In flight |
| `COMPLETED` | Finished; `final_answer` carries the answer |
| `FAILED` | An error ended the run |
| `CANCELLED` | A cancel was observed |
| `TIMED_OUT` | A deadline ended the run |
| `GUARD_KILLED` | A guard (e.g. width/depth) ended the run |
| `STUCK` | No observed progress; shut down |

**Termination reason (`termination_reason`)**: `finish`, `no_decision`,
`no_progress`, `turn_budget`, `max_agents`, `wall_clock`, `cancelled`,
`stuck`, `error`, `guard`.

**Agent status** (in detail + SSE): `QUEUED`, `RUNNING`, `COMPLETED`,
`FAILED`, `KILLED`.

**Bot state** (bot-kind agents, in detail's `agents[].bot_state`):
`PROVISIONING`, `READY`, `AUTHORIZATION_REJECTED`,
`AUTHORIZATION_EXPIRED`, `CREATE_FAILED`, `APPLY_FAILED` — the persona bot's
own creation lifecycle (§8.5).

**Guard kinds**: `serial_collapse`, `spurious_parallelism`, `dedupe_reject`,
`depth_exceeded`, `width_exceeded`, `timeout`, `kill`, `stuck`,
`prompt_injection`. Example: submit 10 agents with
`options.max_width = 3` and the stream shows `width_exceeded` guards as the
orchestrator tries a wider wave and is capped.

## 7. Error model

| HTTP | Body (error key) | Cause |
| --- | --- | --- |
| 401 | — | Anonymous caller on a non-probe route |
| 404 | `{"detail":"run not found"}` | Unknown run id **or** cross-tenant access (same answer by design) |
| 400 | `{"error":"…"}` | Missing serving configuration for the requested model/provider |
| 422 | validation detail | Shape errors: unknown field, over-limit, blank tenant |
| 429 | `{"error":"tenant_run_limit_exceeded","tenant":…,"limit":…}` | Too many concurrent runs for this tenant |
| 429 | `{"error":"global_run_limit_exceeded","limit":…}` | Fleet-wide concurrent run ceiling |
| 429 | `{"error":"queue_depth_exceeded","limit":…}` | Dispatch queue at capacity |

## 8. Bot agents: provisioning from a `config_manifest`

### 8.1 What the field is

`agents[].config_manifest` accepts a **full bot configuration manifest**
(schema v1): the five content categories `mcp`, `resources`, `skills`,
`cli_tools`, `identity` under a `manifest:` block, plus named `sources:` and
the imperative `script:` — the same document
`POST /openapi/v1/bots/with-manifest` accepts (§8.5).

Behavior on the submit path:

- **Present** — the platform validates the document at submit ("preflight",
  the same validation and the same all-violations `422` list as the
  with-manifest surface) and provisions the persona bot from it **verbatim**:
  `role` / `instructions` / `identity_file` still ride along as normal spec
  fields, but the manifest's own `identity` category is what the bot carries.
  The accepted document is durably stored at submit time (receipt in
  `ac_bot_config_manifest`).
- **Blank (default)** — the platform synthesizes an identity-only document
  from `identity_file` + `instructions`.
- Persona bots are provisioned through the shared background queue — allow
  **minutes**, and prefer streaming or polling over tight client deadlines.
  In listings the persona appears named after its agent, and the run's
  terminal teardown soft-deletes it.

### 8.2 The document at a glance

```yaml
schema_version: 1

sources:                      # named sources, declared once, referenced often
  content:
    protocol: git             # git | oss
    url: https://code.example-corp.com/team/content.git
    ref: v1.2.0
    auth: corp-git-content    # a named credential — never a secret inline

manifest:                     # declarative categories — every engine
  mcp: [ … ]                  # MCP servers (server_code + optional config)
  resources: [ … ]            # workspace files and directory trees
  skills: [ … ]               # local skill packages
  identity: [ … ]             # persona/rule files (SOUL.md, SAFETY.md, …)
  cli_tools: [ … ]            # platform-installed command-line tools

script:                       # imperative part (top level, not under manifest)
  body: |
    echo provisioned
```

**Convergence is category-level overwrite** — the section of the grammar
that catches everyone once:

| Declaration | Apply does |
| --- | --- |
| Category absent | Leaves that category completely untouched |
| Category non-empty | Converges the category's area to exactly the declaration (**removes** undeclared entries in the area) |
| Category present but `[]` | The empty set is a declaration — removes everything in the category's area |
| Whole document deleted | Removes nothing — prior deliveries stay on the bot |

A category is **all-or-nothing**: if any declared entry in it cannot be
materialized, that category is not overwritten at all (its siblings still
apply). A partially-landed apply reports `APPLY_FAILED`, never a "success
with a footnote" — under overwrite semantics a partial set would be
destructive.

### 8.3 Entries — the shared source machine

Every content entry names **exactly one** of `from` / `source` / `content`
(mutually exclusive), plus shared optional fields:

| Field | Rules |
| --- | --- |
| `from: <name>` | Names a top-level `sources` entry; the credential lives on the source, not the entry |
| `source: {protocol: git …}` | Inline source object: `git` takes `url` + `ref` (+ `mode`, own `subpath`, `auth`); `oss` takes `bucket` + `key` (+ `auth`). Bare URL strings are refused with the replacement spelling |
| `content: \|…` | Inline UTF-8 text, ≤ 64 KiB. `auth` / `digest` / `on_fetch_failure` are illegal on it (nothing is fetched) |
| `subpath` | `git`: a path in the delivered tree; `oss`: **a member of an unpacked archive** — refused where the entry unpacks nothing |
| `digest: sha256:…` | **oss only** — fetched bytes must match or the fetch fails. **Mandatory** for `skills` and `cli_tools` over oss; illegal on git (the commit SHA is the pin) |
| `auth: <name>` | A named tenant credential, presented during fetch within its stored prefixes |
| `on_fetch_failure` | `keep_last` (default — reuse the platform's last good copy) \| `fail` (the category aborts before writing) |
| `unpack` / `strip_components` | oss archives (`zip` \| `tar.gz`); illegal on git; mandatory for oss directory resources and skill packages |
| `apply_once` | v1 reserved word — refused wherever it appears |

Per-category keys (anything else is refused as `unknown_field`):

- **`mcp`** — `server_code` (MCP Center registry id) + optional `config`
  (`url`, non-sensitive `headers`, `endpoint_env` `PROD|PRE`,
  `transport_protocol` `SSE|STREAMABLE_HTTP`). Registering brand-new MCP
  identities is outside the manifest contract.
- **`resources`** — `path` (workspace-relative; a trailing `/` makes it a
  directory entry owning that subtree).
- **`skills`** — `name`.
- **`identity`** — `type` (e.g. `SOUL.md`, `SAFETY.md`, `RULES.md`, …);
  `MEMORY.md` and `IDENTITY.md` are **refused at write** (engine-generated
  runtime state — apply must stay convergent).
- **`cli_tools`** — `name` + optional `version`: one entry = one command =
  one file (a `subpath` selects it inside a fetched archive).
- **`script.body`** — the start command, bounded by the startup-script cap
  (24 KiB). Per-construct support follows the same capability resolver the
  with-manifest surface publishes — e.g. `script` on teclaw-class engines is
  refused as `unsupported_script`, and a category no materializer can apply
  (today: `engine_config`) is refused at submit rather than landing inert.

**`${BOT_*}` substitution** (whitelist, resolved before fetch and before
credential prefix checks): `${BOT_ENGINE_TYPE}`, `${BOT_ENV}`,
`${BOT_TENANT}`, `${BOT_ARCH}` (today: `amd64`). There is deliberately no
`${BOT_ID}`.

### 8.4 Limits and provenance

| Limit | Value |
| --- | --- |
| Whole manifest document | 64 KiB (UTF-8) |
| Entries per category | 50 |
| One inline `content` block | 64 KiB |
| Source URL | 2,048 chars |
| `script.body` | 24 KiB |
| `config_manifest` field itself (swarm submit DTO) | 200,000 chars |
| Fetch-time caps (per entry / per apply) | enforced by the fetcher, not the submitter — byte ceilings on fetched objects, unpacked trees, and per-hop timeouts belong to the fetch side |

> **Verified against** `docs/bot-config-manifest/manifest-schema.zh-CN.md`
> (§1–§5), `adapters/http/agentswarm/router.py`, and
> `adapters/http/agentswarm/schemas.py`, 2026-10. The grammar document is
> the authority for anything this condensed reference does not cover.

### 8.5 The sibling surface: create one bot with a manifest

The swarm's manifest travels through the same contract the single-bot create
surface defines — worth knowing both, because the lifecycle states in
`agents[].bot_state` are exactly these:

**`POST /openapi/v1/bots/with-manifest`** — one request creating a bot and
its configuration. Body: the ordinary create fields (`bot_name`, `bot_desc`,
`engine` (e.g. `openclaw`), `cluster_name` (e.g. `ACRA`), `bot_type` (e.g.
`personal`)) plus `config_manifest`. The manifest is validated **before
anything is spent**: an invalid document is a `422` naming every violation,
with no bot id minted and nothing stored. It always answers `202` with an
envelope:

```json
{"code": 202000, "message": "OK",
 "data": {"bot_id": "20260813_a7k2m9p1",
          "iframe_url": "https://auth.example.com/passport/consent?flow=f-1",
          "redirect_url": ""},
 "request_id": "…"}
```

Direct the user to whichever authorization URL is non-empty (the auth
provider returns one or the other, unpredictably). Then poll:

**`GET /openapi/v1/bots/{bot_id}/with-manifest/status`** →
`{"state": …, "bot_id": …, "bot": …?, "apply": …?, "message": …}` where:

| State | Meaning |
| --- | --- |
| `AWAITING_AUTHORIZATION` | Nothing exists yet — send the user to the URL |
| `AUTHORIZATION_REJECTED` / `AUTHORIZATION_EXPIRED` | Terminal — **no bot was created**; the stored manifest is deleted with the creation |
| `CREATING` | Authorized; the bot record exists and its container is being provisioned |
| `CREATE_FAILED` | Terminal — no usable bot (nothing to do with the manifest) |
| `APPLYING` | The bot is up; the post-container manifest phase is running |
| `READY` | Terminal — the bot is up and the whole manifest landed |
| `APPLY_FAILED` | Terminal — **bot is up and running**; part of the configuration did not land. The response carries the bot as well as the per-entry apply report; fix the manifest and apply it again — nothing needs recreating |

The terminal states return both the bot and the per-entry apply report
(categories, entries, actions `created`/`updated`/`unchanged`/`skipped`/
`failed`, plus any `removed` entries). Unlike the ordinary `PUT
…/config-manifest` — which may store a construct nothing can apply yet —
this create surface **refuses** such a construct outright: accepting it
would mean authorizing, creating, and only then failing to configure.

### 8.6 A complete manifest sample

A full worked sample (from the platform's own contract examples,
`api-contracts/genome/v1/examples/manifest-v1/01-complete/` — the
`engine_config` category is omitted because v1 refuses it):

```yaml
schema_version: 1

sources:
  content:
    protocol: git
    url: https://code.example-corp.com/team/content.git
    ref: v1.2.0
    auth: corp-git-content
  order-lookup:
    protocol: oss
    bucket: artifacts
    key: tools/skills/order-lookup-1.4.0.zip
    auth: oss-artifacts
  shopctl:
    protocol: oss
    bucket: artifacts
    key: tools/shopctl/2.3.0/shopctl-linux-amd64
    auth: oss-artifacts

manifest:
  identity:
    - type: SOUL.md
      from: content
      subpath: bots/support-agent/soul.md
    - type: RULES.md
      from: content
      subpath: kb/service-rules.md
    - type: SAFETY.md
      content: |
        Support safety boundaries: never promise refund amounts;
        escalate money-loss cases to a human.

  resources:
    - path: data/faq.csv
      from: content
      subpath: kb/faq.csv
      on_fetch_failure: keep_last
    - path: data/kb/
      from: content
      subpath: kb/
      on_fetch_failure: keep_last

  skills:
    - name: quality-check
      from: content
      subpath: skills/quality-check/
    - name: order-lookup
      from: order-lookup
      digest: "sha256:896e1cb125db379d13790e045213f16bb8c936b29ff0b0d6ca33b1ea037c359f"
      unpack: zip

  cli_tools:
    - name: shopctl
      from: shopctl
      digest: "sha256:d89a79825b1468374f2d0a5cdc5c856694730e6355624d0b2f925db5202a68a0"
      version: "2.3.0"

  mcp:
    - server_code: mcp.ant.homistudio.meetmcp

script:
  body: |
    #!/bin/bash
    set -euo pipefail
    curl -fsSL http://inner-ops.example.com/whitelist/today.json \
      -o "$HOME/workspace/data/whitelist.json"
```

## 9. End-to-end walkthrough

A complete two-agent run — one `llm`, one **bot whose configuration travels
in the request** — from submit to report:

```bash
BASE=http://<gateway-host>/openapi/v1/agentswarm

# 1. submit; agent[1] carries a full config manifest (§8.6)
curl -sS -X POST "$BASE/runs" -H 'Content-Type: application/json' -d '{
  "tenant": "teamclaw",
  "goal": "Greet the user politely and confirm the greeting was logged.",
  "agents": [
    {"name": "s1", "role": "greeting reviewer", "kind": "llm",
     "instructions": "Reply with exactly: greeting ok"},
    {"name": "s2", "role": "manifest-configured greeter", "kind": "bot",
     "instructions": "Greet the user politely.",
     "identity_file": "SOUL.md",
     "config_manifest": "schema_version: 1\nmanifest:\n  identity:\n    - type: SOUL.md\n      content: \"You are a meticulous greeter.\"\n  mcp: []\n  resources: []\n  skills: []\n  cli_tools: []\nscript:\n  body: \"echo provisioned\"\n"}
  ],
  "options": {"model_name": "DeepSeek-V4.1-Flash"}
}'
# → {"run_id":"a1b2c3…"}; keep it in RUN_ID

# 2. poll — GET /runs/{run_id}?tenant=…  until status ∈ §6 terminal set
curl -sS "$BASE/runs/$RUN_ID?tenant=teamclaw"

# 3. watch: every agent event replays over SSE, guarded width caps included
curl -sS -N "$BASE/runs/$RUN_ID/events?tenant=teamclaw"

# 4. inspect the ledger — decisions/messages/rounds, the bot agent's
#    bot_id and bot_state, orchestrator decision turns
curl -sS "$BASE/runs/$RUN_ID/detail?tenant=teamclaw"

# 5. human-readable report
curl -sS "$BASE/runs/$RUN_ID/report?tenant=teamclaw"
```

Notes for the bot agent: its persona bot provisions through the shared
background queue (minutes, not seconds — §8.1); the durable manifest
receipt exists from the moment submit answered, so a slow persona never
invalidates the document; and when the run ends, the platform's teardown
deletes the persona bot.

## 10. Related documents

- Module internals, tables, configuration knobs, operational runbook:
  [`core/agentswarm/README.md`](../../src/agentclaw/community/core/agentswarm/README.md)
- Manifest grammar (authoritative) + worked examples:
  [`bot-config-manifest/manifest-schema.zh-CN.md`](../bot-config-manifest/manifest-schema.zh-CN.md),
  [`examples.zh-CN.md`](../bot-config-manifest/examples.zh-CN.md)
- Create-with-manifest contract (the sibling surface in §8.5):
  [`openapi_v1/bots/create_with_manifest.py`](../../src/agentclaw/community/adapters/http/openapi_v1/bots/create_with_manifest.py)
- Bot creation guide (ordinary create, engines, clusters, templates):
  `docs/bot-create-api-guide.zh-CN.md` in the Avernet repo docs.