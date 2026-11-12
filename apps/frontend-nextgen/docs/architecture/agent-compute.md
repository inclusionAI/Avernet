# Agent Compute (agentswarm) frontend

The workbench client for `/openapi/v1/agentswarm`. A user defines a goal, a set of
agents and run options, submits the run, and watches it until it finishes.

Code lives in `src/services/agentswarm/`. The gateway implementation is
`apps/backend/src/agentclaw/community/adapters/http/agentswarm/` — that package's
`schemas.py` and `router.py` are the contract this module transcribes.

## Endpoints used

| Operation | Method and path | Payload |
| --- | --- | --- |
| Submit | `POST /openapi/v1/agentswarm/runs` | `SubmitRunDTO` → `{run_id}` |
| Cancel | `POST /openapi/v1/agentswarm/runs/{id}/cancel` | `{run_id, status}` |
| Read one run | `GET /openapi/v1/agentswarm/runs/{id}` | `RunSummaryDTO` |
| List runs | `GET /openapi/v1/agentswarm/runs` | `RunListDTO` |
| Read detail | `GET /openapi/v1/agentswarm/runs/{id}/detail` | `RunDetailDTO` |
| Liveness | `GET /openapi/v1/agentswarm/health`, `/ready` | — |

`/runs/{id}/events` (SSE) and `/runs/{id}/report` (HTML) exist but are not used: the
client polls, and the report is not surfaced.

## Entry and layout

The entry is the chat composer's **plus menu**, as the PRD specifies: a `计算任务` row
beside 上传文件 / 任务 / 工作流任务, with the PRD's hint on hover
(「根据工作目标，定义执行 Agent，启动多个容器协同批量计算」).

Selecting it opens `AgentComputePanel` as a **layout sibling of the conversation**, not a
route or a modal: `<aside>` taking 440–480px on `xl+` and overlaying the pane when
narrower. The panel holds both halves of the flow — the config form, then the run list and
detail — so a submit never leaves the conversation.

`查看计算任务详情` (`ComputeRunLink`) only records which run to address
(`useAgentComputePanelStore.openDetail`); opening the panel is the caller's act.

**The panel is session-scoped.** `agentComputePanelStore` records the session it was
opened for, and `AgentComputePanel` renders nothing when the active session differs, so a
panel opened in one conversation never appears in another.

`/agent-compute` remains as a deep-link host that opens the same panel full width for the
current identity. There is deliberately no second layout — the panel is the only
implementation of the surface.

Every route takes a mandatory `tenant` query parameter. Tenancy is **not** derived from
the authenticated principal on this surface, and `tenantParams()` refuses a blank value
before a request is made.

This version targets a single tenant, so the value is a deployment constant:
`resolveAgentswarmTenant()` returns `TEAMCLAW_AGENTSWARM_TENANT` (injected by
`config/config.local.ts`, default `teamclaw`). Do not replace it with a per-user identity
lookup — the identity id is a user, not a tenant.

A wrong-but-valid tenant returns an empty list rather than an error, so an empty run list
is not proof that the user has no runs.

## The identity source

`identity_bot_id` is optional in `SubmitRunDTO` but required at runtime: a run with any
`bot`-kind agent is refused without it.

- `400` — no source supplied (`IdentitySourceRequired`)
- `422` — the named source is not a Bot owned by the caller holding an issued passport
  (`ext.passport.agent_code` present, status not `PENDING`)

`llm` agents run in-process and hold no passport, so a pure-`llm` run is exempt and a
source it happens to name is ignored rather than validated. `validateIdentitySource()`
mirrors this rule exactly so the refusal reaches the user before a request is sent.

## The optional manifest

A `bot`-kind agent may carry `config_manifest`: a five-category YAML document
(`identity` / `mcp` / `resources` / `skills` / `cli_tools`, plus `script`) submitted as a
**string** and forwarded verbatim to the platform's preflight, which stays authoritative
for the grammar.

**Blank is meaningful, not invalid.** An empty manifest means "let the platform
synthesize the identity-only persona" — the ordinary path. The editor is therefore
collapsed and empty by default, and `normalizeManifest()` omits the field rather than
sending `""`.

`src/domain/agentswarm/manifest.ts` checks only what hand-edited YAML gets wrong: syntax,
a non-mapping top level, `schema_version: 1`, the five category keys present as lists, and
an `identity` list that is non-empty with `type`/`content` on each entry. It deliberately
does not reimplement the platform's validator.

Verified against the live gateway: the payload built by `toSubmitDTO` for a manifest agent
returned `200` from `POST /runs` and the stored document was byte-identical
(`ac_bot_config_manifest`), with the persona provisioned from it.

## Agent kind

The UI creates **`bot` only**. `llm` is a test-only kind with no entry point; the type
and the gateway's exemption remain because the field is still on the wire and a draft can
carry one.

The picker's options come from `GET /api/bots/by-owner-or-collaborator`, which does
**not** expose passport status. The list therefore cannot be filtered up front, and an
unusable choice surfaces as a 422 at submit with a message telling the user to choose
another Bot.

## Run states

`RunStatus` is `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT`,
`GUARD_KILLED`, `STUCK`. `RUN_STATUSES` and the `RunStatus` union in `types.ts` are a
transcription of that vocabulary — do not add a state the gateway cannot report.

There is **no run-level authorization state** on this surface. A per-agent
`BotState.AWAITING_AUTHORIZATION` exists, and `AgentDetailDTO.authorization` is populated
while a bot sits at the human gate, but an identity-inheriting run never reaches it.
Rendering an approval flow from that field is out of scope for this version.

## Polling

- `GET /runs` and `GET /runs/{id}` while a run is active; stop at any terminal status.
- `GET /runs/{id}/detail` only when the user opens the per-agent view, or once at
  terminal. It carries every agent's full `messages`, `attempt_ledger` and `result`,
  and grows with the run, so it must never be polled.
- `agentswarmRequest` sets `retryOnTransient: false`. The caller owns cadence; transport
  retries on top of a poll would multiply load without adding reliability.

## Errors

`agentswarmRequest` normalizes transport and envelope failures into
`AgentswarmRequestError` with a `kind`: `identity_source_missing`, `identity_source_invalid`,
`unconfirmed` (502/504 — the run may or may not exist), `not_found`, `refused`, `unknown`.
`explainSubmitRefusal()` turns a kind into user-facing text plus two flags the UI needs:
whether resubmitting can help, and whether the run must be verified first.

## Open Core

This module is public code: no internal registries, company domains or credentials.
It composes on the app-wide `backendRequest`, so it inherits auth and the error-notify
protocol rather than duplicating them.