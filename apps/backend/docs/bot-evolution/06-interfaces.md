# Interfaces — API, SDK, CLI, and bot-driven evolution

> 中文版：[06-interfaces.zh-CN.md](06-interfaces.zh-CN.md)

> Status: DRAFT. Answers "API/SDK or CLI, and how does a bot drive RSI?"

> **Postponed: bot callers.** How a bot talks to the platform is not
> decided yet, so DR-3 is postponed
> ([decisions/0003](decisions/0003-bot-principal-for-evolution-surface.md)).
> Everything below that has a bot calling the platform (actors C and D,
> the bot skill in §4, §5, and the bot scopes in §6) is kept as input for
> that later decision, not as first-iteration scope. In the first
> iteration the callers are pipelines and humans (actors A and B), and
> strategies run as platform job workers, not as bots.

## 1. Recommendation in one paragraph

Build **one resource API** (OpenAPI, under `/openapi/v1/evolution/*` plus the
Genome endpoints under `/openapi/v1/bots/{id}/genome/*`), and derive
everything else from it: **generated client SDKs** for deterministic code, a
separate **Strategy (plugin) SDK** for people who write evolution approaches,
and a **thin `avn` CLI** over the client SDK that serves humans and CI.
If bots later call the platform (postponed, see above), the proposal is
that they use the same CLI through a shipped `SKILL.md`, authenticated as a
**bot principal with narrow scopes**, like `bcs-cli` today, rather than a
separate "bot API": a bot would be just a caller with fewer permissions.
An MCP server can be generated from the same API later if an engine needs it.

## 2. Who drives evolution — four actors

The vagueness goes away once the actors are separated. They need different
*permissions*, not different *APIs*.

| Actor | Example | Surface | Typical calls | May promote? |
| --- | --- | --- | --- | --- |
| **A. Deterministic pipeline** | Nightly job, CI after a skill change, a product backend | Client SDK / REST | start run, poll, read report, approve per policy | Only within auto-promote policy configured by owner |
| **B. Human operator** | Bot owner, tenant admin, researcher | UI, CLI | everything, review queue, rollback | **Yes** (review) |
| **C. Runner bot** (bot *as the optimizer*) *(postponed)* | ClawEvolve tune agent, a "coach" bot evolving other bots | Job Protocol via CLI + skill | claim job, read input, materialise sandbox, submit patch / scores | No |
| **D. Subject bot** (bot *improving itself*) *(postponed)* | Support bot noticing it keeps failing refunds | CLI + skill | read own active genome, record observation, submit draft patch to inbox, request a run | **Never** |

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
POST   /bots/{bot}/genome/promotions               {revision, reason}              (owner / gate; going back = promoting an earlier revision)
GET    /bots/{bot}/genome/content/{digest}         → bytes (authorized against the bot)
PUT    /bots/{bot}/genome/content                  bytes → {digest}

# Evolution (apps/evolution)
GET    /evolution/strategies                       ?engine=&gene=
POST   /evolution/strategies                       register / new version
POST   /bots/{bot}/evolution/runs                  {strategy, version?, params?, budget?, trigger}
                                                   Idempotency-Key header → 202 {run_id}
GET    /bots/{bot}/evolution/runs/{run}            status, iterations, budget used
GET    /bots/{bot}/evolution/runs/{run}/candidates
POST   /bots/{bot}/evolution/runs/{run}:cancel
GET    /bots/{bot}/evolution/candidates/{cand}/report   diff + eval comparison + gate decision
POST   /bots/{bot}/evolution/candidates/{cand}:approve  | :reject   (review queue)
GET    /bots/{bot}/evolution/policy                enabled strategies, schedules, auto-promote ceiling
PUT    /bots/{bot}/evolution/policy

# Experience (apps/evolution)
POST   /bots/{bot}/experience/observations         fast-loop note from subject bot (postponed)
POST   /bots/{bot}/experience/feedback             rating / correction / outcome
GET    /bots/{bot}/experience/episodes?revision=&since=&outcome=

# Inbox (fast loop; postponed with DR-3)
POST   /bots/{bot}/evolution/inbox                 draft patch from subject bot
GET    /bots/{bot}/evolution/inbox

# Evaluation (apps/evolution, read mostly)
GET    /evolution/suites/{suite}                   cases visible per caller role
POST   /bots/{bot}/evolution/evaluations           evaluate a revision on a suite (operator only)

# Jobs (Job Protocol for strategy workers) — see 05-strategy-sdk.md §9
```

Async pattern: starting a run returns `202` with a **run id**. Submission
is idempotent and the platform guarantees it: a repeated POST with the same
`Idempotency-Key` returns the same run id and starts nothing new. From then
on the run id is the only handle; callers look up status with
`GET …/runs/{run}`. No callback channel is needed. The CLI's `--wait` only
repeats that lookup. A run survives crashes and restarts under the same id
([05-strategy-sdk.md §7](05-strategy-sdk.md#7-run-lifecycle)).

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
- **Auth from environment like `bcs-cli`**: OAuth device flow / token for
  humans and CI; explicit precedence documented. (A session file under
  `$BOT_DATA_DIR` for bots is postponed with DR-3.)
- **Deliverable to every engine** *(when bots call it; postponed)*: single
  static binary (Rust, like `bcs-cli`) or Python zipapp; delivered to bots
  via Manifest `cli_tools`, which already works on teclaw.

Command tree (illustrative):

```text
avn genome  show|log|diff|refs|promote|content get|put|patch apply --dry-run
avn evolve  run start|status|cancel|report  ·  strategies list|show  ·  policy get|set
avn evolve  review list|show|approve|reject
avn evolve  inbox submit|list                     # subject bot (postponed)
avn evolve  observe "<note>" --episode <id>       # subject bot (postponed)
avn experience episodes|feedback
avn job     claim|input|heartbeat|upload|complete|fail   # strategy job workers
avn strategy dev|test|publish                     # strategy authors (wraps strategy SDK harness)
```

### The bot skill *(postponed with DR-3)*

Ship `skills/avernet-evolution/SKILL.md` (agentskills.io format) that tells a
bot *when* and *how* to use the subset of commands it is allowed:

- Subject-bot skill: "After a task fails or the user corrects you, run
  `avn evolve observe` with a one-line lesson and the episode id. If you
  believe a persona or skill change would prevent a repeated failure, draft it
  with `avn evolve inbox submit`. You cannot apply changes to yourself."
- Runner-bot skill: the job loop, the sandbox rules, and the output contract.

Progressive disclosure keeps this cheap in context: the description is
always loaded; the body only when relevant.

## 5. Why CLI (+skill) rather than MCP or a native tool, for bots *(postponed with DR-3)*

| Option | For | Against |
| --- | --- | --- |
| **CLI + SKILL.md** (recommended first) | Precedent (`bcs-cli` + `bcs-coordination` skill); delivery path exists (`cli_tools`, incl. teclaw); same binary for humans and CI; engine-neutral; testable (singlebox already gates `bcs-cli` leaf-command coverage) | Needs `exec` tool in the engine; output parsing by the model |
| MCP server | Typed tools; some engines prefer it | Another deployment + per-engine MCP config; teclaw MCP via Center only; duplicate surface |
| Engine-native tool (like Hermes `skill_manage`) | Tightest UX | Per-engine implementation; violates engine neutrality; unreviewed self-writes are exactly what we must avoid |

Generate an MCP adapter from OpenAPI later (P6) if an engine lacks `exec`.

## 6. Authentication and authorization

OpenAPI v1 refuses `bot` principals today, deliberately, and the first
iteration keeps it that way. Callers are users, tenant admins, and
pipelines with their existing credentials; strategy job workers are
platform services, not bots. The scope table below is DR-3's proposal for
admitting bots **only** to the evolution surface. It is postponed until the
way bots talk to the platform is decided:

| Scope | Granted to | Allows |
| --- | --- | --- |
| `genome:read:self` | subject bot | read own `active` revision and diff history |
| `experience:write:self` | subject bot | observations and feedback for itself |
| `inbox:write:self` | subject bot | submit draft patches for itself |
| `run:request:self` | subject bot (opt-in by owner) | request a run of an *enabled* strategy, within budget |
| `evolution:runner` | runner bot / worker | claim jobs for registered strategy ids; call the Job Protocol for runs it has claimed |
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
run_id = c.runs.start(bot="bot_123", strategy="clawevolve/bot-evolution",
                      budget={"max_usd": 10}, idempotency_key="nightly-2026-10-08")
# safe to repeat: the same key returns the same run_id
run = c.runs.get(run_id)            # status lookup by id; repeat until run.status is terminal
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
- Q2 *(postponed with DR-3)*: Should subject bots be allowed
  `run:request:self` by default? Recommend off by default, on per owner
  policy with a daily budget.
