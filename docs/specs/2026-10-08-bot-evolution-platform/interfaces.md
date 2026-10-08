# Interfaces — API, SDK, CLI, and bot-driven evolution

> Status: DRAFT. Answers "API/SDK or CLI, and how does a bot drive RSI?"

## 1. Recommendation in one paragraph

Build **one resource API** (OpenAPI, under `/openapi/v1/evolution/*` plus the
Genome endpoints under `/openapi/v1/bots/{id}/genome/*`), and derive
everything else from it: **generated client SDKs** for deterministic code, a
separate **Strategy (plugin) SDK** for people who write evolution approaches,
and a **thin `avn` CLI** over the client SDK that serves humans, CI, *and
bots*. Bots use the CLI through a shipped `SKILL.md`, authenticated as a
**bot principal with narrow scopes**, exactly like `bcs-cli` today. Do not
build a separate "bot API": a bot is just a caller with fewer permissions.
An MCP server can be generated from the same API later if an engine needs it.

## 2. Who drives evolution — four actors

The vagueness goes away once the actors are separated. They need different
*permissions*, not different *APIs*.

| Actor | Example | Surface | Typical calls | May promote? |
| --- | --- | --- | --- | --- |
| **A. Deterministic pipeline** | Nightly job, CI after a skill change, a product backend | Client SDK / REST | start run, poll, read report, approve per policy | Only within auto-promote policy configured by owner |
| **B. Human operator** | Bot owner, tenant admin, researcher | UI, CLI | everything, review queue, rollback | **Yes** (review) |
| **C. Runner bot** (bot *as the optimizer*) | ClawEvolve tune agent, a "coach" bot evolving other bots | Job Protocol via CLI + skill | claim job, read input, materialise sandbox, submit patch / scores | No |
| **D. Subject bot** (bot *improving itself*) | Support bot noticing it keeps failing refunds | CLI + skill | read own active genome, record observation, submit draft patch to inbox, request a run | **Never** |

Key insight: "RSI driven by a bot" is two different things — a bot doing the
optimization work for a run (C), and a bot asking to be improved (D). Both are
safe on the same API because neither can move `active`.

## 3. Resource API sketch

Resource-oriented, async for long work, idempotency keys on POSTs, ETags on
mutable resources. Normative spec is work item RSI-07.

```text
# Genome (Backend)
GET    /bots/{bot}/genome/revisions?status=&parent=
GET    /bots/{bot}/genome/revisions/{rev}
POST   /bots/{bot}/genome/revisions                {base, patch} | {manifest}   → candidate/draft revision
GET    /bots/{bot}/genome/revisions/{rev}/diff?against={rev}
GET    /bots/{bot}/genome/refs
PUT    /bots/{bot}/genome/refs/draft               {revision, expected_revision}
POST   /bots/{bot}/genome/rollback                 {to_revision, reason}           (promotion path)

# Evolution (apps/evolution)
GET    /evolution/strategies                       ?engine=&gene=
POST   /evolution/strategies                       register / new version
POST   /bots/{bot}/evolution/runs                  {strategy, version?, params?, budget?, trigger}
GET    /bots/{bot}/evolution/runs/{run}            status, iterations, budget used
GET    /bots/{bot}/evolution/runs/{run}/candidates
POST   /bots/{bot}/evolution/runs/{run}:cancel
GET    /bots/{bot}/evolution/candidates/{cand}/report   diff + eval comparison + gate decision
POST   /bots/{bot}/evolution/candidates/{cand}:approve  | :reject   (review queue)
GET    /bots/{bot}/evolution/policy                enabled strategies, schedules, auto-promote ceiling
PUT    /bots/{bot}/evolution/policy

# Experience (apps/evolution)
POST   /bots/{bot}/experience/observations         fast-loop note from subject bot
POST   /bots/{bot}/experience/feedback             rating / correction / outcome
GET    /bots/{bot}/experience/episodes?revision=&since=&outcome=

# Inbox (fast loop)
POST   /bots/{bot}/evolution/inbox                 draft patch from subject bot
GET    /bots/{bot}/evolution/inbox

# Evaluation (apps/evolution, read mostly)
GET    /evolution/suites/{suite}                   cases visible per caller role
POST   /bots/{bot}/evolution/evaluations           evaluate a revision on a suite (operator only)

# Jobs (runner protocol) — see strategy-sdk.md §5
```

Async pattern: POST returns `202` + resource; clients poll or subscribe
(webhook / SSE). The CLI hides this with `--wait`.

## 4. CLI design (`avn`)

Requirements that make a CLI good for both humans and agents:

- **Thin**: every command is one or two SDK calls. No logic that the API
  lacks — otherwise bots and pipelines diverge.
- **Machine-first output**: `--output json` default when stdout is not a TTY;
  stable field names; **stable exit codes** (0 ok, 2 usage, 3 not found,
  4 conflict/CAS, 5 policy denied, 6 budget exceeded, 7 transient).
- **Non-interactive**: no prompts unless `--interactive`; destructive
  operations require `--yes`; `--dry-run` everywhere it makes sense.
- **Self-describing**: `avn <cmd> --help --output json` emits the command
  schema, so an agent can discover arguments without a doc dump.
- **Auth from environment like `bcs-cli`**: session file under
  `$BOT_DATA_DIR` for bots; OAuth device flow / token for humans; explicit
  precedence documented.
- **Deliverable to every engine**: single static binary (Rust, like
  `bcs-cli`) or Python zipapp; delivered to bots via Manifest `cli_tools`,
  which already works on teclaw.

Command tree (illustrative):

```text
avn genome  show|log|diff|refs|rollback|patch apply --dry-run
avn evolve  run start|status|cancel|report  ·  strategies list|show  ·  policy get|set
avn evolve  review list|show|approve|reject
avn evolve  inbox submit|list                     # subject bot
avn evolve  observe "<note>" --episode <id>       # subject bot
avn experience episodes|feedback
avn job     claim|input|heartbeat|upload|complete|fail   # runner bot / worker
avn strategy dev|test|publish                     # strategy authors (wraps plugin SDK harness)
```

### The bot skill

Ship `skills/avernet-evolution/SKILL.md` (agentskills.io format) that tells a
bot *when* and *how* to use the subset of commands it is allowed:

- Subject-bot skill: "After a task fails or the user corrects you, run
  `avn evolve observe` with a one-line lesson and the episode id. If you
  believe a persona or skill change would prevent a repeated failure, draft it
  with `avn evolve inbox submit`. You cannot apply changes to yourself."
- Runner-bot skill: the job loop, the sandbox rules, and the output contract.

Progressive disclosure keeps this cheap in context: the description is
always loaded; the body only when relevant.

## 5. Why CLI (+skill) rather than MCP or a native tool, for bots

| Option | For | Against |
| --- | --- | --- |
| **CLI + SKILL.md** (recommended first) | Precedent (`bcs-cli` + `bcs-coordination` skill); delivery path exists (`cli_tools`, incl. teclaw); same binary for humans and CI; engine-neutral; testable (singlebox already gates `bcs-cli` leaf-command coverage) | Needs `exec` tool in the engine; output parsing by the model |
| MCP server | Typed tools; some engines prefer it | Another deployment + per-engine MCP config; teclaw MCP via Center only; duplicate surface |
| Engine-native tool (like Hermes `skill_manage`) | Tightest UX | Per-engine implementation; violates engine neutrality; unreviewed self-writes are exactly what we must avoid |

Generate an MCP adapter from OpenAPI later (P6) if an engine lacks `exec`.

## 6. Authentication and authorization

OpenAPI v1 refuses `bot` principals today, deliberately. ADR 0021 proposes
re-admitting bots **only** for the evolution surface with explicit scopes:

| Scope | Granted to | Allows |
| --- | --- | --- |
| `genome:read:self` | subject bot | read own `active` revision and diff history |
| `experience:write:self` | subject bot | observations and feedback for itself |
| `inbox:write:self` | subject bot | submit draft patches for itself |
| `run:request:self` | subject bot (opt-in by owner) | request a run of an *enabled* strategy, within budget |
| `evolution:runner` | runner bot / worker | claim jobs for registered plugin ids; read job inputs; upload outputs |
| — | nobody but owner/admin/policy | promote, rollback, change policy, enable strategies, touch locked genes |

Bot credentials come from the existing Passport/AgentPass issuance; the
gateway signs `X-Avernet-Principal` with `kind: bot` and scopes; Backend and
Evolution check scopes through the R12 authorization hook. `self` is bound to
the bot id in the credential, never to a request parameter.

## 7. SDK ergonomics for deterministic pipelines

```python
# Illustrative only
from avernet_evolution import Client

c = Client.from_env()
run = c.runs.start(bot="bot_123", strategy="clawevolve/bot-evolution", budget={"max_usd": 10})
run = run.wait(timeout="2h")
for cand in run.candidates(accepted=True):
    report = cand.report()
    if report.risk_tier <= policy.auto_tier and report.gate.passed:
        cand.approve(reason="nightly auto-policy")
```

Same calls the CLI makes; same calls the UI backend makes.

## 8. Open questions

- Q1: Is `avn` a new binary, or a subcommand group of an existing CLI?
  (No general Avernet CLI exists today; `bcs-cli` is BCS-scoped.)
  Recommendation: new `avn` in Rust following `bcs-cli` conventions, with
  room for other platform commands later.
- Q2: Should subject bots be allowed `run:request:self` by default? Recommend
  off by default, on per owner policy with a daily budget.
- Q3: Webhook vs SSE for run events — reuse whatever the gateway already
  supports for other async resources.
