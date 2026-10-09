# Evolution API and clients

> 中文版：[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)

> Status: DRAFT. Service in the [bot evolution architecture](design.md).
> The public access layer: the conventions every evolution endpoint shares,
> the index of all public endpoints, the generated SDKs, and the `avn` CLI.

## 1. Purpose and scope

The bot evolution platform is reached through **one resource API**: an
OpenAPI document under the public prefix `/openapi/v1`, covering the Genome
endpoints and the evolution endpoints. Everything else a caller uses is
derived from that one API:

- **Generated client SDKs** (Python and TypeScript) for deterministic code:
  pipelines, CI, and the UI backend.
- **A thin `avn` CLI** over the client SDK for humans and CI scripts.

The previous design text phrased this as "derive everything else from one
API": if a capability is not in the API, no SDK or CLI has it, so callers
cannot drift apart.

**This doc owns:**

| Owned here | What it means |
| --- | --- |
| Shared API conventions | Envelope, request ids, JSON rules, path ids and custom methods, idempotency keys, ETags and `If-Match`, error codes, pagination, long-running work by id |
| The endpoint index | Which public endpoints exist and which doc owns each one ([§13](#13-api)) |
| Generated SDKs | Packages, generation, the hand-written convenience layer (retries, waiting, pagination, error types) |
| The `avn` CLI | Command tree mapped to API calls, machine output, stable exit codes, non-interactive rules, self-description |

**This doc does not own:**

| Not owned here | Owner |
| --- | --- |
| The resources and their full request/response schemas | Each owning doc: [01-genome.md](01-genome.md), [02-experience.md](02-experience.md), [03-strategy.md](03-strategy.md), [05-experiment-ledger.md](05-experiment-ledger.md), [06-evolution-run.md](06-evolution-run.md), [07-verification.md](07-verification.md), [08-promotion.md](08-promotion.md) |
| The internal Job Protocol (`/evolution/v1/...`) used by strategy job workers | [06-evolution-run.md](06-evolution-run.md) |
| The Strategy (plugin) SDK `avernet-evolution-strategy`, its harness and conformance kit | [03-strategy.md](03-strategy.md) |
| Who may approve or promote, risk tiers, the gate | [08-promotion.md](08-promotion.md) |
| Authentication and authorization | Not covered in this design set; it is postponed and will get its own design |

**Where it runs.** The API is served by two services behind one prefix:
the Genome and Promotion endpoints (including the candidate report,
review-queue, and approve/reject paths) by **Backend** (beside the Manifest
and the apply pipeline), and the Experience, Strategy registry, Evolution
Run, Verification, Ledger, Meta-evolution, and shared operation endpoints
by the proposed new module **`apps/evolution`** (open decision D-1 in
[design.md](design.md)). This doc presents both as one public `/openapi/v1`
surface.
The conventions in this doc are implemented once per service, in each
service's HTTP adapter layer, so that core logic stays transport-agnostic.
Backend's existing OpenAPI v1 adapter already provides the envelope and
pagination types this doc reuses
(`apps/backend/src/agentclaw/community/adapters/http/openapi_v1/contracts.py`:
`Envelope`, `ErrorEnvelope`, `Page`, `PageParams`;
`responses.py`: `envelope`, `page`, `created`, `accepted`, `deleted`, and the
`@envelope_errors` decorator; documented in
`apps/backend/docs/openapi-v1/README.md`). The SDK packages and the `avn`
binary are new deliverables (work item RSI-07 in [work-items.md](work-items.md)).

## 2. Domain model

The types here are the shared wire conventions, not the evolution
resources themselves (those are defined in their owning docs).

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `Envelope[T]` | The uniform response wrapper: `code`, `message`, `data`, `request_id` | This doc (reuses OpenAPI v1) | Per response |
| `ErrorEnvelope` | The same wrapper on failure, with `data` always `null` | This doc (reuses OpenAPI v1) | Per response |
| `Page[T]` / `PageParams` | A page of a list result (`total`, `items`) and the controls (`page`, `page_size`) | This doc (reuses OpenAPI v1) | Per request |
| `IdempotencyRecord` | The platform's memory of one idempotency key: `(bot, key) → resource id` plus a fingerprint of the request | This doc; stored by each service | Created on the first request with a key; kept for the retention window |
| `Precondition` | An `ETag` returned on a read of a mutable resource, and the `If-Match` header that sends it back on a write | This doc | Per resource version |
| `Operation` | A long-running unit of work started by a request and looked up by id (`queued`, `running`, `succeeded`, `failed`, `cancelled`) | Concept defined in [03-strategy.md](03-strategy.md); the public resource and its lookup endpoint are owned here ([§13.8](#138-operations--this-doc)) | Created by a `202` response; terminal once finished |
| `ExitCode` | The `avn` CLI's stable process exit codes | This doc | Fixed per major CLI version |
| `CommandSchema` | Machine-readable description of one `avn` command (`--help --output json`) | This doc | Per CLI release |

### 2.1 Envelope and ErrorEnvelope

Every JSON response, success or failure, is an envelope. This is the
existing OpenAPI v1 shape; the evolution endpoints adopt it unchanged so
that one client library handles both the Genome endpoints and the
evolution endpoints. `code` is six digits: the HTTP status (three) followed
by a business subcode (three), for example `200000` (OK), `202000`
(Accepted), `404000` (not found). Binary content (for example
`GET /bots/{bot}/genome/content/{digest}`) bypasses the envelope; that is
the one exception, as in OpenAPI v1 today.

```python
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

@dataclass(frozen=True)
class Envelope(Generic[T]):
    code: int            # HTTP status (3 digits) + business subcode (3 digits), e.g. 202000
    message: str         # human-readable, always English, e.g. "Accepted"
    data: T | None       # the payload; null on errors (and on empty results where documented)
    request_id: str      # trace id of this HTTP request; mirrors the X-Trace-Id response header

@dataclass(frozen=True)
class ErrorEnvelope:
    code: int            # e.g. 409001; the subcode identifies the failure (§7)
    message: str         # fixed public message per code, never an internal exception text
    data: None           # always null
    request_id: str
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// A success envelope (GET /bots/bot_123/evolution/runs/run_7f3)
{
  "code": 200000,
  "message": "OK",
  "data": {"run_id": "run_7f3", "status": "running"},
  "request_id": "4f2c9a7e1b3d4e5f8a9b0c1d2e3f4a5b"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// An error envelope: the policy changed since the caller read it
{
  "code": 412000,
  "message": "Precondition failed: the resource changed since it was read",
  "data": null,
  "request_id": "9a1b2c3d4e5f60718293a4b5c6d7e8f9"
}
```

### 2.2 Page and PageParams

List endpoints use the existing OpenAPI v1 pagination: 1-based `page`,
`page_size` from 1 to 100 (default 20), and a `Page` payload with the total
count and the items.

```python
@dataclass(frozen=True)
class PageParams:
    page: int = 1          # 1-based
    page_size: int = 20    # 1..100

@dataclass(frozen=True)
class Page(Generic[T]):
    total: int             # number of items matching the query (all pages)
    items: list[T]         # items on this page; present, possibly empty
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// data of GET /bots/bot_123/evolution/runs?status=completed&page=2&page_size=2
{
  "total": 5,
  "items": [
    {"run_id": "run_7c1", "status": "completed", "created_at": "2026-10-06T02:00:04Z"},
    {"run_id": "run_7d9", "status": "completed", "created_at": "2026-10-07T02:00:03Z"}
  ]
}
```

### 2.3 IdempotencyRecord

An **idempotency key** is a string the client chooses for one logical
request (for example "start tonight's run for bot_123"). The client sends
the same key on every retry of that request, and a different key for a
different request. The platform remembers which resource the key produced,
so a retry returns that resource instead of creating a second one. The
rules are in [§5](#5-idempotency-keys).

```python
from datetime import datetime

@dataclass(frozen=True)
class IdempotencyRecord:
    bot: str               # scope: the bot in the request path
    key: str               # the client's Idempotency-Key header value
    fingerprint: str       # sha256 of the RFC 8785 canonical form of {method, path, body}
    resource_kind: str     # "run" | "operation" | "revision" | "promotion" | "review_decision" | "feedback"
    resource_id: str       # what the first request created or started
    first_status: int      # HTTP status of the first response (201 or 202)
    created_at: datetime
    expires_at: datetime   # end of the retention window (open decision, §16)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bot": "bot_123",
  "key": "nightly-bot_123-2026-10-08",
  "fingerprint": "sha256:5d1e…",
  "resource_kind": "run",
  "resource_id": "run_7f3",
  "first_status": 202,
  "created_at": "2026-10-08T02:00:03Z",
  "expires_at": "2026-11-07T02:00:03Z"
}
```

### 2.4 Precondition (ETag and If-Match)

An **ETag** is a version tag the server returns with a resource. A client
that wants to change the resource sends the tag back in an **`If-Match`**
header; if the resource changed in the meantime, the server refuses the
write with `412 Precondition Failed` instead of silently overwriting the
other writer's change.

```python
@dataclass(frozen=True)
class Precondition:
    etag: str              # opaque, strong, quoted on the wire: "\"pol-7\""
    resource: str          # the path it belongs to
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// What the SDK keeps after GET /bots/bot_123/evolution/policy
{"etag": "\"pol-7\"", "resource": "/openapi/v1/bots/bot_123/evolution/policy"}
```

### 2.5 Operation (public view)

An **operation** is work that can outlast a short HTTP request: an
evaluation, a ledger export. Starting it returns `202 Accepted` with
`{operation_id}` at once; the caller then looks up its status by id with
`GET /bots/{bot}/evolution/operations/{operation}`, the single public
operation resource ([§13.8](#138-operations--this-doc)). The same concept is
used inside strategies (agent sessions, train evaluations); see
[03-strategy.md](03-strategy.md). Runs follow the same pattern with their
own status values ([06-evolution-run.md](06-evolution-run.md)).

```python
from typing import Literal

OperationStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

@dataclass(frozen=True)
class OperationError:
    code: int              # same code space as ErrorEnvelope.code
    message: str

@dataclass(frozen=True)
class Operation:
    id: str                         # "op_19a"
    kind: str                       # e.g. "evaluation", "ledger_export"
    status: OperationStatus
    created_at: datetime
    updated_at: datetime
    result: dict | None             # present only when status == "succeeded"; shape per kind (owning doc),
                                    # e.g. {"evaluation_id": ...} for an evaluation
    error: OperationError | None    # present only when status == "failed"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "op_19a",
  "kind": "ledger_export",
  "status": "succeeded",
  "created_at": "2026-10-08T09:30:00Z",
  "updated_at": "2026-10-08T09:31:12Z",
  "result": {"digest": "sha256:e7a2…", "format": "jsonl", "entries": 412},
  "error": null
}
```

### 2.6 ExitCode and CommandSchema

```python
from enum import IntEnum

class ExitCode(IntEnum):
    OK = 0
    ERROR = 1             # proposed: any failure not listed below (unmapped 5xx, internal CLI error)
    USAGE = 2             # bad arguments, or a 400 validation error from the API
    NOT_FOUND = 3         # 404
    CONFLICT = 4          # 409 (incl. compare-and-swap, idempotency key reuse) and 412 / 428
    POLICY_DENIED = 5     # the request violates the bot's evolution policy or a platform rule
    BUDGET_EXCEEDED = 6   # a budget ceiling refuses the request
    TRANSIENT = 7         # network failure, 429, 502, 503, 504, or --wait timed out; safe to retry

@dataclass(frozen=True)
class CommandSchema:
    command: list[str]              # e.g. ["evolve", "run", "start"]
    summary: str
    api: list[str]                  # the API calls it makes, e.g. ["POST /bots/{bot}/evolution/runs"]
    arguments: list[dict]           # name, type, required, repeated, description
    flags: list[dict]               # name, type, default, description
    destructive: bool               # True if --yes is required
    output_schema: str              # JSON Schema id of the data it prints
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// avn evolve run start --help --output json
{
  "command": ["evolve", "run", "start"],
  "summary": "Start an evolution run for a bot (idempotent)",
  "api": ["POST /bots/{bot}/evolution/runs", "GET /bots/{bot}/evolution/runs/{run}"],
  "arguments": [],
  "flags": [
    {"name": "--bot", "type": "string", "required": true, "description": "Bot id"},
    {"name": "--binding", "type": "string", "required": true, "description": "Binding of the bot's evolution policy to run"},
    {"name": "--idempotency-key", "type": "string", "required": false, "description": "Reuse on retries; generated and printed when omitted"},
    {"name": "--wait", "type": "bool", "default": false, "description": "Repeat the status lookup until the run is terminal"},
    {"name": "--timeout", "type": "duration", "default": "2h", "description": "Give up waiting (the run continues) and exit 7"}
  ],
  "destructive": false,
  "output_schema": "avn/run@1"
}
```

## 3. Callers

In the first iteration the API has three kinds of callers. They use the
same endpoints; what each may do is decided by the owning docs (for
promotion and approval, [08-promotion.md](08-promotion.md)), not by a
separate API per caller.

| Caller | Example | Surface | Typical calls |
| --- | --- | --- | --- |
| **Deterministic pipeline / CI** | Nightly job, CI after a skill change, a product backend | Client SDK or `avn` in a script | Start a run, look up its status, read candidate reports, approve within the owner's auto-promote policy |
| **UI backend** | The evolution pages of the workbench (AgentEvolve UI in the interim) | Client SDK (TypeScript) | Everything a human does in the UI: policy editing, review queue, reports, promotions, ledger browsing |
| **Human operator** | Bot owner, tenant admin, researcher | UI, `avn` | Everything, including review, promotion, and going back to an earlier revision |

Strategy job workers are platform services and use the internal Job
Protocol through the Strategy SDK, not this public API
([06-evolution-run.md](06-evolution-run.md), [03-strategy.md](03-strategy.md)).

Bot callers (a bot driving its own evolution, or a bot acting as a job
runner, and the CLI skill or MCP adapter they would use) are postponed with
DR-3 ([decisions/0003-bot-principal-for-evolution-surface.md](decisions/0003-bot-principal-for-evolution-surface.md)).

## 4. Request and response conventions

### 4.1 Paths, resources, and custom methods

- **Prefix.** Every public path is under `/openapi/v1`. Paths in this design
  set are written relative to it.
- **Resource-oriented.** Collections are plural nouns
  (`/bots/{bot}/evolution/runs`); a member is the collection plus its id.
  `GET` reads, `POST` on a collection creates or starts, `PUT` replaces a
  whole mutable resource.
- **Custom methods** for actions that are not plain create or replace use a
  colon suffix on the resource: `:cancel`, `:approve`, `:reject`,
  `:export`. They are always `POST`.
- **Ids that contain a colon or a slash.** Revision and candidate ids are
  content hashes such as `sha256:a90b…`; strategy ids contain a `/`
  (`acme/correction-fixer`). Inside a path segment clients percent-encode
  the colon (`sha256%3Ac41e…`) and the slash (`acme%2Fcorrection-fixer`).
  The server splits a custom method on the **last** colon of the final
  segment, so `/candidates/sha256%3Ac41e…:approve` is unambiguous. The SDKs
  and `avn` do the encoding; callers pass ids as they appear in responses.
- **Per-bot sequence numbers.** A revision's `seq` (shown to humans as
  `r41`) is not an identity ([01-genome.md](01-genome.md)). The API takes
  revision ids; `avn` additionally accepts the `r41` form and resolves it
  through the Genome endpoints before calling.

### 4.2 Request ids

Every response carries a **request id**: the `request_id` envelope field,
mirrored in the `X-Trace-Id` response header (existing OpenAPI v1
behaviour). It identifies one HTTP request for logs and support. It is not
an idempotency key:

| | Request id | Idempotency key |
| --- | --- | --- |
| Chosen by | The server | The client |
| Identifies | One HTTP attempt | One logical request, across all its retries |
| Same on retry? | No, each attempt has its own | Yes, by definition |
| Used for | Tracing, logs, support | Not creating or starting the same thing twice |

The SDKs expose the request id on every response and every error; `avn`
prints it on failure (stderr) and includes it in JSON output.

### 4.3 JSON

- Request and response bodies are JSON (`application/json`), UTF-8.
  Everything new in this design is JSON; the Bot Config Manifest stays YAML
  as today ([01-genome.md](01-genome.md)).
- Field names are `snake_case` and stable within `/openapi/v1`.
- Timestamps are RFC 3339 strings in UTC (`2026-10-08T02:00:03Z`);
  durations are integer seconds with an `_s` suffix (`max_wall_clock_s`).
- Content that is hashed (revisions, patches, candidate ids) is
  canonicalized with RFC 8785 and contains no floats
  ([01-genome.md](01-genome.md)). Responses are not required to be
  canonical, but clients must not re-serialize a hashed document and expect
  the same hash unless they canonicalize it.
- Each resource has a JSON Schema; the OpenAPI document references them.

### 4.4 Compatibility within v1

- Changes inside `/openapi/v1` are additive: new endpoints, new optional
  request fields, new response fields.
- Clients ignore response fields they do not know. The generated SDKs keep
  unknown fields available rather than failing.
- Status enums documented as closed (run status, operation status, verdict
  status) do not gain values within v1; a new value would be a breaking
  change. Other enums are open, and clients treat unknown values as opaque.
- Removing or renaming a field, or tightening validation, needs a new
  version of the endpoint.

## 5. Idempotency keys

Any `POST` that creates something or starts work accepts an
**`Idempotency-Key`** request header. The run submission is idempotent and
the platform guarantees it: a repeated `POST` with the same key returns the
same run id and starts nothing new. The same holds for every endpoint marked
"key" in the index ([§13](#13-api)).

### 5.1 Rules for the client

- The key is a **client-chosen string**, 1 to 255 printable ASCII
  characters; the recommended alphabet is `[A-Za-z0-9._:/-]`.
- It is **identical for every retry of one logical request** and
  **different for different requests**.
- It must **not** be something that changes between retries. A timestamp
  taken at send time, or a random value generated per attempt, makes every
  retry look like a new request and defeats the purpose.

Good keys by caller:

| Caller | Key | Example |
| --- | --- | --- |
| Pipeline (one-off logical request) | A UUID created **once, before the first attempt**, and stored with the job's state | `0b6f7c52-3d0e-4c1b-9a63-8f2a7d0e5c11` |
| Pipeline (scheduled, one per period) | Deterministic from what makes the request unique | `nightly-bot_123-2026-10-08` |
| Platform trigger (a binding firing on schedule) | `<binding_id>/<scheduled_fire_time>` | `bind_01/2026-10-08T02:00:00Z` |
| Strategy (internal, Job Protocol) | `<run_id>/<own step>` | `run_7f3/round-2/tune` |

The scheduled fire time in a trigger key is the time the schedule named,
not the time the trigger actually ran, so a trigger that fires late or
twice still produces one run.

### 5.2 Rules for the platform

- The platform stores **`(bot, key) → resource id`**, together with a
  fingerprint of the request (`sha256` of the canonical `{method, path,
  body}`). For bot-scoped paths the scope is the bot in the path.
- The record is written **in the same transaction** as the resource it
  points to, so a crash can never leave a created run without its key, or a
  key without its run.
- **Same key, same request** (matching fingerprint): the platform returns
  the original result: the same status code (`201` or `202`) and the same
  resource id, with the resource's **current** state in `data` (a run that
  started an hour ago comes back as `running`, not `queued`). The response
  carries the header `Idempotent-Replayed: true` (proposed). Nothing is
  created, started, or charged again.
- **Same key, different request** (fingerprint differs, including a
  different endpoint): `409` with code `409001`. This catches a client bug
  that reused a key; the platform never guesses which request was meant.
- **Concurrent duplicates.** Two requests with the same key that arrive
  together resolve to one resource: the second waits briefly for the first
  to commit and then replays it, or answers `409` with code `409002`
  ("request with this key in progress") if the first has not committed
  within a short bound. `409002` is retryable with the same key.
- **Retention.** A record is kept at least as long as callers might
  retry. The window is an open decision ([§16](#16-open-decisions)); after
  it expires, the key may be reused.
- **Naturally idempotent creates.** Some creates are idempotent by content
  and need no key: a candidate id is the content hash of its patch, a
  revision id is the hash of `{spec, policy}`, and registering a strategy
  version that already exists with identical content returns that version.
  They still accept a key for uniformity.

### 5.3 Where keys are used

| Endpoint | Key creates or starts |
| --- | --- |
| `POST /bots/{bot}/evolution/runs` | A run (`202`, run id) |
| `POST /bots/{bot}/evolution/evaluations` | An evaluation operation (`202`, operation id) |
| `POST /bots/{bot}/evolution/ledger:export` | An export operation (`202`, operation id) |
| `POST /bots/{bot}/genome/revisions` | A revision (also content-idempotent) |
| `POST /bots/{bot}/genome/promotions` | A promotion (moving `active`) |
| `POST /bots/{bot}/evolution/candidates/{candidate}:approve` and `:reject` | A review decision |
| `POST /bots/{bot}/evolution/runs/{run}:cancel` | A cancellation (also idempotent by state) |
| `POST /bots/{bot}/evolution/operations/{operation}:cancel` | An operation cancellation (also idempotent by state) |
| `POST /bots/{bot}/experience/feedback` | A feedback record |
| `POST /evolution/strategies` | A strategy version (also content-idempotent) |

`PUT` requests are idempotent by definition (the same body produces the
same state) and use `If-Match` instead of a key.

## 6. Concurrency: ETags, If-Match, and compare-and-swap

Two writers changing the same resource must not silently overwrite each
other. The API uses two mechanisms, chosen per resource by its owning doc:

**ETag and `If-Match`** for whole-resource replacement (`PUT`). Today's
`PUT /config-manifest` has neither, which is one of the gaps the Genome
Registry closes ([01-genome.md](01-genome.md)).

- A `GET` of a mutable resource returns an `ETag` header (a strong,
  opaque, quoted tag).
- A `PUT` must send `If-Match: <etag>`. If the resource changed since, the
  server answers `412` (`412000`). If `If-Match` is missing, it answers
  `428 Precondition Required` (`428000`), so a client cannot skip the check
  by accident.
- The first mutable resource on the public surface is the bot's evolution
  policy (`GET|PUT /bots/{bot}/evolution/policy`).

**Compare-and-swap in the body** for ref moves. Ref updates carry
`expected_revision`: the revision the client believes the ref points to
now. A mismatch is `409` (`409010`). This is the mechanism defined by the
Genome Registry for `PUT /bots/{bot}/genome/refs/draft`
([01-genome.md](01-genome.md)); promotions move `active` under the same
rule ([08-promotion.md](08-promotion.md)).

**Immutable resources** (revisions, content blobs, strategy versions,
ledger entries) never change, so they need no write precondition. Their
`ETag` is derived from their content id, and clients may send
`If-None-Match` to get `304 Not Modified` (proposed) instead of the body.

**Resources the client never writes** (runs, operations, review items)
change only through custom methods (`:cancel`, `:approve`, `:reject`),
which are checked against the resource's state: approving a candidate that
was already rejected is `409` (`409020`), approving one that is already
approved replays the earlier decision.

## 7. Errors

### 7.1 Shape and code space

Every documented failure is an `ErrorEnvelope` ([§2.1](#21-envelope-and-errorenvelope))
with a six-digit code: HTTP status plus a business subcode. Messages are
fixed per code (Backend's `ENVELOPE_ERRORS` mapping already enforces this
for OpenAPI v1), so they are safe to show and stable to match on, but
clients branch on `code`, never on `message`.

Each owning doc defines the subcodes for its own failures. This doc fixes
the shared ones and the **families** the SDKs and the CLI map onto error
types and exit codes:

| Code | Meaning | Retry? | `avn` exit code |
| --- | --- | --- | --- |
| `400000` | Request body or query fails validation (schema, enum, range) | No: fix the request | 2 |
| `404000` | Resource not found (bot, run, revision, candidate, strategy, suite, entry) | No | 3 |
| `409001` | Idempotency key reused with a different request | No: client bug | 4 |
| `409002` | A request with this idempotency key is still in progress | Yes, same key | 7 |
| `409010` | Ref compare-and-swap failed: `expected_revision` is stale | After re-reading | 4 |
| `409011` | Strategy version already registered with different content | No | 4 |
| `409020` | Action conflicts with the resource's state (e.g. approving a rejected candidate, cancelling a finished run) | No | 4 |
| `412000` | `If-Match` does not match the current `ETag` | After re-reading | 4 |
| `428000` | `If-Match` required but missing | After adding it | 4 |
| `403xxx` / `422xxx` (policy family) | The request violates the bot's evolution policy or a platform rule: locked gene, gene outside a binding's `allowed_genes`, binding check failed, evolution frozen by a kill switch, strategy disabled, candidate not promotable under the gate | No | 5 |
| `403100`-range (budget family) | A budget ceiling refuses the request (per-bot or per-tenant daily or monthly ceiling, max promotions per day) | Not until the ceiling resets | 6 |
| `429000` | Rate limited | Yes, after `Retry-After` | 7 |
| `502000`, `503000`, `504000` | Upstream or service unavailable | Yes, same key | 7 |
| `500000` | Unexpected server error | Only with an idempotency key | 1 |

The precise subcodes inside the policy and budget families are set by the
owning docs ([06-evolution-run.md](06-evolution-run.md) for budgets, kill
switches, and binding checks; [01-genome.md](01-genome.md) for locked genes;
[08-promotion.md](08-promotion.md) for the gate).

A **rejected candidate is not an error.** Verification rejecting a
candidate, or a run ending as `budget_exhausted`, is a normal result
reported in the resource's status, with `200`.

### 7.2 Retry guidance

- `GET` is always safe to retry.
- `POST` with an idempotency key is safe to retry with **the same key**
  after a transient failure (`409002`, `429`, `5xx`, network error, or no
  response at all).
- `POST` without a key is never retried automatically by the SDKs.
- `PUT` with `If-Match` is safe to retry; a `412` after a retry means
  someone else wrote in between (or the first attempt succeeded): re-read
  and decide.
- Back off exponentially with jitter, honouring `Retry-After`.

## 8. Pagination and filtering

- Lists use `page` (1-based) and `page_size` (1 to 100, default 20), and
  return `Page {total, items}`, as OpenAPI v1 does today
  (`PageParams` in `contracts.py`).
- Filters are query parameters named after the field they filter
  (`status=`, `revision=`, `outcome=`, `run=`, `strategy=`); time ranges
  use `since=` and `until=` (RFC 3339). Each owning doc lists its filters.
- Default ordering is newest first by creation time unless the owning doc
  says otherwise; ordering is stable for equal timestamps (by id).
- The SDKs offer iterators that walk all pages; `avn` offers `--page`,
  `--page-size`, and `--all`.
- Large, append-only collections (episodes, ledger entries) may need cursor
  pagination instead of page numbers, because `total` is expensive and pages
  shift as entries are added. That is an open decision
  ([§16](#16-open-decisions)); until it is decided, the existing convention
  applies.

## 9. Long-running work by id

No request is held open while work runs. Work that can outlast a short
request follows one pattern:

1. **Start returns an id at once.** The start request records the work and
   answers `202 Accepted` (`202000`). For an operation, `data` is
   `{operation_id}` and the `Location` header names
   `/bots/{bot}/evolution/operations/{operation}`; for a run, `data` is the
   run as it is now (status `queued`) and `Location` names the run.
2. **Status is looked up by id.** The caller repeats a short `GET` on that
   URL until the status is terminal. Every lookup returns promptly. The
   response may carry `Retry-After` as a hint for the next lookup.
3. **Start is idempotent.** With the same idempotency key, a repeated start
   returns the same id ([§5](#5-idempotency-keys)).
4. **The id is the only handle.** There is no callback or webhook channel;
   the SDK's `wait` helper and `avn ... --wait` only repeat the lookup.
5. **The work survives crashes.** A run survives worker crashes and
   restarts under the same run id ([06-evolution-run.md](06-evolution-run.md));
   operations are persisted by the platform independently of the caller.

| Started by | Returns | Look up with | Statuses |
| --- | --- | --- | --- |
| `POST /bots/{bot}/evolution/runs` | run id | `GET /bots/{bot}/evolution/runs/{run}` | `queued \| running \| completed \| failed \| cancelled \| budget_exhausted` |
| `POST /bots/{bot}/evolution/evaluations` | operation id | `GET /bots/{bot}/evolution/operations/{operation}`; its `result` carries the `evaluation_id`, read with `GET /bots/{bot}/evolution/evaluations/{evaluation}` | `queued \| running \| succeeded \| failed \| cancelled` |
| `POST /bots/{bot}/evolution/ledger:export` | operation id | `GET /bots/{bot}/evolution/operations/{operation}` | `queued \| running \| succeeded \| failed \| cancelled` |

Candidates and verdicts follow the same "by id" rule one level down: a
candidate's report is looked up by candidate id
(`GET /bots/{bot}/evolution/candidates/{candidate}`), and there is no
blocking call that waits for a verdict.

## 10. Generated SDKs

| Package | Language | For | Contents |
| --- | --- | --- | --- |
| `avernet-evolution` | Python | Pipelines, CI, Python backends | Generated client for the whole public evolution and Genome surface, plus the convenience layer below |
| `@avernet/evolution` | TypeScript | The UI backend, Node pipelines | The same, generated from the same OpenAPI document |

The Strategy SDK (`avernet-evolution-strategy`) is a different package for
strategy authors and is owned by [03-strategy.md](03-strategy.md).

### 10.1 Generation

- The OpenAPI document is the source of truth. The SDK models and raw
  endpoint methods are **generated** from it; no hand-written request code.
- Proposed: each service (Backend, `apps/evolution`) publishes its part of
  the document, and a build step merges them into one public document under
  `/openapi/v1`, from which both SDKs and the `avn` command schemas are
  generated (open decision, [§16](#16-open-decisions)).
- SDK versions follow the API: a minor SDK release for additive API
  changes, a major one only with a new API version.

### 10.2 The hand-written convenience layer

A small layer on top of the generated code implements the conventions in
this doc once, so every caller gets them the same way. It adds no
behaviour that the API lacks.

| Concern | Behaviour |
| --- | --- |
| Envelope | Unwraps `data`; keeps `request_id` on the result and on errors |
| Errors | Raises typed errors by code family: `ValidationError`, `NotFound`, `Conflict` (with `IdempotencyKeyReused`, `RefConflict`, `PreconditionFailed` subclasses), `PolicyDenied`, `BudgetExceeded`, `Transient`, `ServerError` |
| Idempotency | Every key-taking method has an `idempotency_key` parameter. If omitted, the SDK generates a UUID **once per method call** and reuses it for its own retries; it is exposed on the result so the caller can store it. A key generated inside one call cannot protect a retry from a *new* process, so pipelines that may restart pass their own key |
| Retries | Automatic retry with backoff for transient failures, only for `GET`, `PUT` with `If-Match`, and `POST` with a key |
| ETags | Read methods return the `ETag` with the resource; write methods take `if_match`, and a helper does read-modify-write with retry on `412` |
| Waiting | `wait(...)` repeats short status lookups until a terminal status or a timeout; never a long-held request |
| Pagination | `iter_*` methods walk all pages |
| Ids | Percent-encodes colon-containing ids in paths |

## 11. The `avn` CLI

`avn` is the command-line client for humans and CI. Proposed: a new binary
written in Rust, following the conventions of `bcs-cli`
(`apps/bcs/crates/tools/bcs-cli/`), with room for other platform commands
later (open decision A-1, [§16](#16-open-decisions)). No general Avernet CLI
exists today; `bcs-cli` is BCS-scoped.

### 11.1 Design rules

- **Thin.** Every command is one or two SDK calls. The CLI contains no
  logic the API lacks, so scripts, pipelines, and the UI cannot diverge.
- **Machine-first output.** `--output json` is the default when stdout is
  not a TTY; `--output table` is the default on a TTY. JSON output is the
  API envelope unchanged (`code`, `message`, `data`, `request_id`), one
  JSON document per command on stdout, including on failure. A one-line
  human summary of a failure goes to stderr. Field names are the API's and
  are stable.
- **Stable exit codes** ([§11.3](#113-exit-codes)).
- **Non-interactive.** No prompts unless `--interactive` is given.
  Commands that change what a bot runs or discard work are **destructive**
  and require `--yes`: `genome promote`, `evolve review approve`,
  `evolve review reject`, `evolve run cancel`, `evolve policy set`. Without
  `--yes` they exit with code 2 and change nothing.
- **`--dry-run` where the API supports it.** A dry run must be answered by
  the API (a validate-only request), not simulated by the CLI;
  `genome patch apply --dry-run` is the first such command. Which other
  endpoints get a validate-only mode is decided by their owning docs.
- **Self-describing.** `avn <cmd> --help --output json` prints the
  command's `CommandSchema` ([§2.6](#26-exitcode-and-commandschema)), so a
  script or tool can discover arguments without reading docs.
- **Idempotent by default.** Commands that start or create something take
  `--idempotency-key`; when it is omitted, `avn` generates one, uses it for
  its own retries, and prints it (in JSON output as a top-level
  `idempotency_key` field next to the envelope fields; see
  [§14.2](#142-ci-script-with-avn)), so a script can rerun the command with
  the same key.
- **Coverage gate.** Like `bcs-cli` in singlebox, every leaf command is
  covered by an end-to-end test against a real stack.

### 11.2 Command tree mapped to API calls

Paths are relative to `/openapi/v1`.

| Command | API call(s) | Notes |
| --- | --- | --- |
| `avn genome log --bot B [--status S] [--parent R]` | `GET /bots/{bot}/genome/revisions` | |
| `avn genome show --bot B [--revision R \| --ref active]` | `GET /bots/{bot}/genome/refs` (to resolve a ref or `r41`), `GET /bots/{bot}/genome/revisions/{rev}` | |
| `avn genome diff --bot B R1 --against R2` | `GET /bots/{bot}/genome/revisions/{rev}/diff?against=` | |
| `avn genome refs --bot B` | `GET /bots/{bot}/genome/refs` | |
| `avn genome draft set --bot B R --expected R0` | `PUT /bots/{bot}/genome/refs/draft` | Compare-and-swap on `expected_revision` |
| `avn genome patch apply --bot B patch.json [--dry-run]` | `POST /bots/{bot}/genome/revisions` | Records a revision from `{base, patch}` |
| `avn genome content get --bot B DIGEST` / `content put --bot B FILE` | `GET /bots/{bot}/genome/content/{digest}` / `PUT /bots/{bot}/genome/content` | Binary, no envelope on get |
| `avn genome promote --bot B --revision R --reason TEXT --yes` | `POST /bots/{bot}/genome/promotions` | Also how to go back: promote an earlier revision |
| `avn genome export --bot B --format git DIR` | `GET /bots/{bot}/genome/revisions`, `GET /bots/{bot}/genome/content/{digest}` | Writes a local git history; read-only on the platform ([01-genome.md](01-genome.md)) |
| `avn experience episodes --bot B [--revision R] [--since T] [--outcome O]` | `GET /bots/{bot}/experience/episodes` | |
| `avn experience episode show --bot B EPISODE` | `GET /bots/{bot}/experience/episodes/{episode}` | |
| `avn experience feedback add --bot B FILE` / `feedback list --bot B` | `POST` / `GET /bots/{bot}/experience/feedback` | |
| `avn evolve strategies list` / `strategies show ID --version V` | `GET /evolution/strategies` / `GET /evolution/strategies/{id}/versions/{version}` | |
| `avn evolve capabilities` | `GET /evolution/capabilities` | |
| `avn evolve policy get --bot B` | `GET /bots/{bot}/evolution/policy` | Prints the `ETag` as `etag` |
| `avn evolve policy set --bot B FILE --if-match ETAG --yes` | `PUT /bots/{bot}/evolution/policy` | `--if-match` is required |
| `avn evolve run start --bot B --binding ID [--idempotency-key K] [--wait]` | `POST /bots/{bot}/evolution/runs` (+ `GET .../runs/{run}` with `--wait`) | |
| `avn evolve run list --bot B [--status S]` | `GET /bots/{bot}/evolution/runs` | |
| `avn evolve run status --bot B RUN` | `GET /bots/{bot}/evolution/runs/{run}` | |
| `avn evolve run candidates --bot B RUN` | `GET /bots/{bot}/evolution/runs/{run}/candidates` | |
| `avn evolve run cancel --bot B RUN --yes` | `POST /bots/{bot}/evolution/runs/{run}:cancel` | |
| `avn evolve review list --bot B` | `GET /bots/{bot}/evolution/review-queue` | |
| `avn evolve review show --bot B CANDIDATE` | `GET /bots/{bot}/evolution/candidates/{candidate}` | Diff, verification, gate decision |
| `avn evolve review approve\|reject --bot B CANDIDATE --reason TEXT --yes` | `POST /bots/{bot}/evolution/candidates/{candidate}:approve` / `:reject` | |
| `avn evolve suites list` / `suites show SUITE` | `GET /evolution/suites` / `GET /evolution/suites/{suite}` | |
| `avn evolve evaluate start --bot B --revision R --suite S [--wait]` / `evaluate status --bot B ID` | `POST /bots/{bot}/evolution/evaluations` / `GET /bots/{bot}/evolution/evaluations/{evaluation}` | Operator-only ad-hoc evaluation |
| `avn evolve ledger list --bot B [filters]` / `ledger show --bot B ENTRY` | `GET /bots/{bot}/evolution/ledger` / `GET /bots/{bot}/evolution/ledger/{entry}` | |
| `avn evolve ledger export --bot B [--wait]` | `POST /bots/{bot}/evolution/ledger:export` (+ status lookup) | |
| `avn strategy dev\|test\|publish` | Strategy SDK harness; `publish` calls `POST /evolution/strategies` | Owned by [03-strategy.md](03-strategy.md) |
| `avn job claim\|heartbeat\|input\|upload\|complete\|fail` | Internal Job Protocol `/evolution/v1/...` | For debugging platform job workers; owned by [06-evolution-run.md](06-evolution-run.md) |

The previous design's `avn evolve inbox` and `avn evolve observe` commands
belonged to bot callers and are postponed with them ([§3](#3-callers)).

### 11.3 Exit codes

Exit codes are part of the CLI contract and do not change within a major
version. They reflect **the API call**, not the outcome of the work: a run
that ends `failed` or a candidate that is rejected is a successful lookup
(exit 0); scripts read `.data.status`.

| Code | Meaning | Typical cause |
| --- | --- | --- |
| 0 | OK | |
| 1 | Other failure *(proposed)* | `500`, an unmapped error, an internal CLI error |
| 2 | Usage | Bad flags or arguments, missing `--yes`, `400` validation error |
| 3 | Not found | `404` |
| 4 | Conflict | `409` (except `409002`), `412`, `428`: idempotency key reused, stale `expected_revision`, stale `ETag`, action conflicts with state |
| 5 | Policy denied | Policy family: locked gene, binding check failed, evolution frozen, strategy disabled, not promotable |
| 6 | Budget exceeded | Budget family: a ceiling refuses the request |
| 7 | Transient | Network error, `409002`, `429`, `502`/`503`/`504`, or `--wait` timed out (the work continues; rerun the lookup) |

## 12. Service interface

Two interfaces are used by other parts: the **client interface** that the
SDKs implement (and the CLI and UI backend call), and the **conventions
interface** that each serving module (Backend, `apps/evolution`) uses in its
HTTP adapter so that every endpoint behaves the same way.

### 12.1 Client interface (what the SDKs provide)

```python
from typing import AsyncIterator, Protocol

class GenomeClient(Protocol):
    async def list_revisions(self, bot: str, *, status: str | None = None,
                             parent: str | None = None, page: PageParams = PageParams()) -> Page["GenomeRevision"]: ...
    async def get_revision(self, bot: str, revision: str) -> "GenomeRevision": ...
    async def diff(self, bot: str, revision: str, *, against: str) -> dict: ...
    async def refs(self, bot: str) -> list["GenomeRef"]: ...
    async def set_draft(self, bot: str, revision: str, *, expected_revision: str) -> "GenomeRef":
        """Compare-and-swap; raises RefConflict (409010) if the draft moved."""
    async def record_revision(self, bot: str, *, base: str, patch: "GenomePatch",
                              idempotency_key: str | None = None) -> "GenomeRevision": ...
    async def promote(self, bot: str, revision: str, *, reason: str,
                      idempotency_key: str | None = None) -> "Promotion":
        """Moves `active` (also used to go back to an earlier revision). Owned by 08-promotion."""
    async def get_content(self, bot: str, digest: str) -> bytes: ...
    async def put_content(self, bot: str, data: bytes) -> str: ...  # digest

class ExperienceClient(Protocol):
    def iter_episodes(self, bot: str, *, revision: str | None = None, since: str | None = None,
                      outcome: str | None = None) -> AsyncIterator["Episode"]: ...
    async def get_episode(self, bot: str, episode: str) -> "Episode": ...
    async def add_feedback(self, bot: str, feedback: "Feedback", *,
                           idempotency_key: str | None = None) -> "Feedback": ...

class StrategiesClient(Protocol):
    async def list(self, *, engine: str | None = None, conformance: str | None = None) -> Page["StrategyRegistration"]: ...
    async def get_version(self, strategy: str, version: str) -> "StrategyRegistration": ...
    async def capabilities(self) -> list["Capability"]: ...

class PolicyClient(Protocol):
    async def get(self, bot: str) -> tuple["EvolutionPolicy", str]:
        """Returns the policy and its ETag."""
    async def put(self, bot: str, policy: "EvolutionPolicy", *, if_match: str) -> tuple["EvolutionPolicy", str]:
        """Raises PreconditionFailed (412000) if the policy changed since it was read."""

class RunsClient(Protocol):
    async def start(self, bot: str, *, binding: str, params: dict | None = None,
                    budget: dict | None = None, idempotency_key: str | None = None) -> "Run":
        """202: returns the run as it is now (status queued, or its current status on a replay)."""
    async def get(self, bot: str, run: str) -> "Run": ...
    def iter(self, bot: str, *, status: str | None = None) -> AsyncIterator["Run"]: ...
    async def candidates(self, bot: str, run: str) -> list["Candidate"]: ...
    async def cancel(self, bot: str, run: str, *, idempotency_key: str | None = None) -> "Run": ...
    async def wait(self, bot: str, run: str, *, timeout_s: int, poll_s: int = 15) -> "Run":
        """Repeats get() until the status is terminal; raises Transient on timeout."""

class VerificationClient(Protocol):
    async def list_suites(self) -> Page["Suite"]: ...
    async def get_suite(self, suite: str) -> "Suite": ...
    async def start_evaluation(self, bot: str, *, revision: str, suite: str,
                               idempotency_key: str | None = None) -> Operation: ...
    async def get_evaluation(self, bot: str, evaluation: str) -> "Evaluation": ...

class ReviewClient(Protocol):
    async def queue(self, bot: str) -> Page["ReviewItem"]: ...
    async def report(self, bot: str, candidate: str) -> dict:
        """Diff + verification + gate decision for one candidate (08-promotion)."""
    async def approve(self, bot: str, candidate: str, *, reason: str,
                      idempotency_key: str | None = None) -> "ReviewItem": ...
    async def reject(self, bot: str, candidate: str, *, reason: str,
                     idempotency_key: str | None = None) -> "ReviewItem": ...

class OperationsClient(Protocol):
    async def get(self, bot: str, operation: str) -> Operation: ...
    async def cancel(self, bot: str, operation: str, *, idempotency_key: str | None = None) -> Operation: ...
    async def wait(self, bot: str, operation: str, *, timeout_s: int = 3600, poll_s: int = 15) -> Operation:
        """Repeats get() until the status is terminal; raises Transient on timeout."""

class LedgerClient(Protocol):
    def iter_entries(self, bot: str, **filters: str) -> AsyncIterator["LedgerEntry"]: ...
    async def get_entry(self, bot: str, entry: str) -> "LedgerEntry": ...
    async def start_export(self, bot: str, *, idempotency_key: str | None = None) -> Operation: ...

class EvolutionClient(Protocol):
    """Entry point of the generated SDK (`avernet_evolution.Client`)."""
    genome: GenomeClient
    experience: ExperienceClient
    strategies: StrategiesClient
    policy: PolicyClient
    runs: RunsClient
    verification: VerificationClient
    review: ReviewClient
    ledger: LedgerClient
    operations: OperationsClient
```

### 12.2 Conventions interface (what each serving module implements)

```python
class IdempotencyStore(Protocol):
    """Implements §5. Writes happen in the caller's transaction."""

    async def lookup(self, bot: str, key: str) -> IdempotencyRecord | None:
        """The record for (bot, key), or None if the key is new or expired."""

    async def record(self, record: IdempotencyRecord) -> None:
        """Stores (bot, key) -> resource id in the same transaction that creates the resource.
        Raises KeyInProgress if another request holds the key and has not committed."""

class IdempotentHandler(Protocol):
    async def handle(self, *, bot: str, key: str | None, fingerprint: str,
                     create: "Callable[[], Awaitable[tuple[str, int]]]",
                     current: "Callable[[str], Awaitable[dict]]") -> tuple[int, dict, bool]:
        """Replays (same fingerprint), refuses (409001, different fingerprint),
        or runs `create` and records the key. Returns (status, data, replayed)."""

class Preconditions(Protocol):
    """Implements §6 for PUT on mutable resources."""

    def etag_of(self, resource_version: str) -> str: ...

    def check(self, *, if_match: str | None, current_etag: str) -> None:
        """Raises PreconditionRequired (428000) if missing, PreconditionFailed (412000) on mismatch."""
```

Both are proposed shared helpers in each service's HTTP adapter layer;
the resource services behind them stay transport-agnostic.

## 13. API

All public paths are under the prefix **`/openapi/v1`** and are written
relative to it. Each endpoint's full schema, examples, and errors are in
its owning doc; this section indexes them and shows the shared conventions
once per group. Legend: **key** = takes `Idempotency-Key`; **202** = starts
long-running work looked up by id; **ETag** = `If-Match` required on write;
**CAS** = compare-and-swap in the body.

### 13.1 Genome — [01-genome.md](01-genome.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot}/genome/revisions` | List revisions (`status=`, `parent=`) | Page |
| `POST /bots/{bot}/genome/revisions` | Record a revision from `{base, patch}` or `{manifest}` | key; content-idempotent |
| `GET /bots/{bot}/genome/revisions/{rev}` | One revision | Immutable ETag |
| `GET /bots/{bot}/genome/revisions/{rev}/diff?against=` | Diff two revisions | |
| `GET /bots/{bot}/genome/refs` | Named refs (`active`, `previous`, `canary`, `draft`, …) | |
| `PUT /bots/{bot}/genome/refs/draft` | Move the owner's `draft` ref | CAS (`expected_revision`) |
| `GET /bots/{bot}/genome/content/{digest}` | Content bytes by digest | Binary, no envelope |
| `PUT /bots/{bot}/genome/content` | Upload content, returns its digest | Content-idempotent |

Shared-convention example: compare-and-swap on a ref.

```http
PUT /openapi/v1/bots/bot_123/genome/refs/draft
Content-Type: application/json
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:b73d…", "expected_revision": "sha256:a90b…"}   // r42 replaces r41, only if draft is still r41
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 409: someone moved draft since this client read it
{
  "code": 409010,
  "message": "Ref changed: expected_revision is not the current revision",
  "data": null,
  "request_id": "0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f80"
}
```

### 13.2 Experience — [02-experience.md](02-experience.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot}/experience/episodes` | List episodes (`revision=`, `since=`, `outcome=`) | Page |
| `GET /bots/{bot}/experience/episodes/{episode}` | One episode | |
| `POST /bots/{bot}/experience/feedback` | Record a rating, correction, or outcome | key |
| `GET /bots/{bot}/experience/feedback` | List feedback | Page |

Shared-convention example: pagination and filters.

```http
GET /openapi/v1/bots/bot_123/experience/episodes?revision=sha256%3Aa90b…&outcome=user_corrected&page=1&page_size=2
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "code": 200000,
  "message": "OK",
  "data": {
    "total": 17,
    "items": [
      {"episode_id": "ep_91", "revision_id": "sha256:a90b…", "started_at": "2026-10-07T09:12:00Z",
       "outcome": {"status": "user_corrected"}},
      {"episode_id": "ep_88", "revision_id": "sha256:a90b…", "started_at": "2026-10-06T16:40:21Z",
       "outcome": {"status": "user_corrected"}}
    ]
  },
  "request_id": "1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b"
}
```

### 13.3 Strategy registry — [03-strategy.md](03-strategy.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /evolution/strategies` | List registered strategies (`engine=`, `conformance=`) | Page |
| `POST /evolution/strategies` | Register a strategy version (with its agent definitions) | key; content-idempotent |
| `GET /evolution/strategies/{id}/versions/{version}` | One registration record and its conformance status | Immutable ETag |
| `GET /evolution/capabilities` | The capability catalog | |

Shared-convention example: re-registering an existing version with
different content is a conflict, not a silent overwrite.

```http
POST /openapi/v1/evolution/strategies
Content-Type: application/json
Idempotency-Key: publish-clawevolve-bot-evolution-2.0.0
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {"experience.sessions@1": {}, "evaluate.train@1": {}}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 409: version 2.0.0 already exists with a different record; publish 2.0.1 instead
{
  "code": 409011,
  "message": "Strategy version already registered with different content",
  "data": null,
  "request_id": "2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c"
}
```

### 13.4 Experiment ledger — [05-experiment-ledger.md](05-experiment-ledger.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot}/evolution/ledger` | List entries (filters such as `run=`, `strategy=`, `since=`) | Page |
| `GET /bots/{bot}/evolution/ledger/{entry}` | One entry | Immutable ETag |
| `POST /bots/{bot}/evolution/ledger:export` | Export entries | key; 202 + operation id |

Shared-convention example: a long-running export.

```http
POST /openapi/v1/bots/bot_123/evolution/ledger:export
Content-Type: application/json
Idempotency-Key: ledger-export-bot_123-2026-10-08
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"format": "filesystem", "filter": {"since": "2026-09-01T00:00:00Z"}}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 202; Location: /openapi/v1/bots/bot_123/evolution/operations/op_19a
{
  "code": 202000,
  "message": "Accepted",
  "data": {"operation_id": "op_19a"},
  "request_id": "3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d"
}
```

### 13.5 Evolution Run — [06-evolution-run.md](06-evolution-run.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot}/evolution/policy` | The bot's evolution policy (list of bindings) | Returns ETag |
| `PUT /bots/{bot}/evolution/policy` | Replace the policy (binding checks run here) | ETag (`If-Match` required) |
| `POST /bots/{bot}/evolution/runs` | Start a run of one binding | key; 202 + run id |
| `GET /bots/{bot}/evolution/runs` | List runs (`status=`) | Page |
| `GET /bots/{bot}/evolution/runs/{run}` | Run status, rounds, budget used | |
| `POST /bots/{bot}/evolution/runs/{run}:cancel` | Cancel a run | key; idempotent by state |
| `GET /bots/{bot}/evolution/runs/{run}/candidates` | Candidates the run submitted | |

Shared-convention example 1: idempotent run submission. The first request
and a retry with the same key get the same run id.

```http
POST /openapi/v1/bots/bot_123/evolution/runs
Content-Type: application/json
Idempotency-Key: nightly-bot_123-2026-10-08
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"binding": "bind_01", "budget": {"max_usd": 10, "max_wall_clock_s": 3600}}   // body {binding, params?, budget?}, defined in 06-evolution-run.md
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 202 on the first attempt; Location: /openapi/v1/bots/bot_123/evolution/runs/run_7f3
{
  "code": 202000,
  "message": "Accepted",
  "data": {"run_id": "run_7f3", "binding": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0",
           "status": "queued", "created_at": "2026-10-08T02:00:03Z"},
  "request_id": "4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 202 on a retry 40 minutes later with the same key; header Idempotent-Replayed: true.
// Same run id, current status; no second run is started or charged.
{
  "code": 202000,
  "message": "Accepted",
  "data": {"run_id": "run_7f3", "binding": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0",
           "status": "running", "created_at": "2026-10-08T02:00:03Z"},
  "request_id": "5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f"
}
```

Shared-convention example 2: `If-Match` on the policy.

```http
PUT /openapi/v1/bots/bot_123/evolution/policy
Content-Type: application/json
If-Match: "pol-7"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bindings": [
    {"id": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0", "trigger": {"schedule": "0 2 * * *"},
     "parent": "active", "allowed_genes": ["persona", "skills"], "verification_profile": "default@1",
     "budget": {"max_usd": 20, "max_wall_clock_s": 7200}, "params": {"window_days": 7, "max_rounds": 3}}
  ]
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200 with the new ETag header: ETag: "pol-8"
{
  "code": 200000,
  "message": "OK",
  "data": {"bindings": [{"id": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0"}]},
  "request_id": "6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a"
}
```

A stale tag gets `412000` ([§2.1](#21-envelope-and-errorenvelope)); a
missing `If-Match` gets `428000`. (The response `data` above is shortened
for the example; the owning doc defines the full policy shape.)

**Internal (not part of the public API or the client SDKs).** The Job
Protocol under `/evolution/v1/...`, which job-worker strategies use to
claim runs and call their context, is owned by
[06-evolution-run.md](06-evolution-run.md). It follows the same conventions:
JSON, idempotent starts with keys of the form `<run_id>/<step>`, and
long-running calls answered with `202` and an operation id.

### 13.6 Verification — [07-verification.md](07-verification.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /evolution/suites` | List suites | Page |
| `GET /evolution/suites/{suite}` | One suite; case contents subject to split visibility | |
| `POST /bots/{bot}/evolution/evaluations` | Operator-only ad-hoc evaluation of a revision on a suite | key; 202 + operation id |
| `GET /bots/{bot}/evolution/evaluations/{evaluation}` | Evaluation result (the id comes from the operation's `result`) | |

Shared-convention example: start, then look up the operation by id.

```http
POST /openapi/v1/bots/bot_123/evolution/evaluations
Content-Type: application/json
Idempotency-Key: 0b6f7c52-3d0e-4c1b-9a63-8f2a7d0e5c11
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:b73d…", "suite": "support-refunds@3"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 202; Location: /openapi/v1/bots/bot_123/evolution/operations/op_19a
{
  "code": 202000,
  "message": "Accepted",
  "data": {"operation_id": "op_19a"},
  "request_id": "7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b"
}
```

```http
GET /openapi/v1/bots/bot_123/evolution/operations/op_19a
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200 while it runs; Retry-After: 30
{
  "code": 200000,
  "message": "OK",
  "data": {"id": "op_19a", "kind": "evaluation", "status": "running",
           "created_at": "2026-10-08T10:02:11Z", "updated_at": "2026-10-08T10:05:40Z",
           "result": null, "error": null},
  "request_id": "8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c"
}
```

Once `status` is `succeeded`, `result` is `{"evaluation_id": "ev_310"}`, and
the evaluation is read with `GET /bots/{bot}/evolution/evaluations/ev_310`.

### 13.7 Promotion — [08-promotion.md](08-promotion.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot}/evolution/candidates/{candidate}` | Candidate report: diff, verification, gate decision | |
| `GET /bots/{bot}/evolution/review-queue` | Candidates waiting for human review | Page |
| `POST /bots/{bot}/evolution/candidates/{candidate}:approve` | Approve a candidate | key; state-checked |
| `POST /bots/{bot}/evolution/candidates/{candidate}:reject` | Reject a candidate | key; state-checked |
| `POST /bots/{bot}/genome/promotions` | Move `active` (including going back to an earlier revision) | key; CAS on `active` |

Shared-convention example: a custom method on a colon-containing id.

```http
POST /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…:approve
Content-Type: application/json
Idempotency-Key: review-bot_123-sha256:c41e…-approve
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "Fixes partial-refund misses; reviewed diff and validation report"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200; approving again with the same key replays this answer
{
  "code": 200000,
  "message": "OK",
  "data": {"candidate_id": "sha256:c41e…", "run_id": "run_7f3", "decision": "approved",
           "risk_tier": "T2", "decided_at": "2026-10-08T11:20:00Z"},
  "request_id": "9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d"
}
```

Approving a candidate that was already rejected is `409020`; approving one
the gate does not allow is in the policy family (exit code 5). Who may
approve which risk tier is defined in [08-promotion.md](08-promotion.md).

### 13.8 Operations — this doc

The single public operation resource, shared by every endpoint that answers
`202` with `{operation_id}` (ad-hoc evaluations in
[07-verification.md](07-verification.md), ledger exports in
[05-experiment-ledger.md](05-experiment-ledger.md)). Served by
`apps/evolution`.

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot}/evolution/operations/{operation}` | Operation status and, once finished, result or error | |
| `POST /bots/{bot}/evolution/operations/{operation}:cancel` | Cancel an operation | key; idempotent by state |

#### GET /bots/{bot}/evolution/operations/{operation}

Look up an operation by id: `Operation{id, kind, status, result?, error?,
created_at, updated_at}` (§2.5). Called by pipelines, the UI backend, the
SDKs' `wait` helpers, and `avn … --wait`. Every lookup returns promptly; the
response may carry `Retry-After`.

```http
GET /openapi/v1/bots/bot_123/evolution/operations/op_19a
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200 after the export finished
{
  "code": 200000,
  "message": "OK",
  "data": {"id": "op_19a", "kind": "ledger_export", "status": "succeeded",
           "created_at": "2026-10-08T09:30:00Z", "updated_at": "2026-10-08T09:31:12Z",
           "result": {"digest": "sha256:e7a2…", "format": "filesystem", "entries": 412},
           "error": null},
  "request_id": "b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6"
}
```

Errors: `404000` unknown operation, or an operation of another bot.

#### POST /bots/{bot}/evolution/operations/{operation}:cancel

Cancel an operation. Idempotent by state: cancelling a cancelled operation
returns it unchanged; cancelling one that already succeeded or failed is
`409020`.

```http
POST /openapi/v1/bots/bot_123/evolution/operations/op_19a:cancel
Idempotency-Key: cancel-op_19a
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200
{
  "code": 200000,
  "message": "OK",
  "data": {"id": "op_19a", "kind": "evaluation", "status": "cancelled",
           "created_at": "2026-10-08T10:02:11Z", "updated_at": "2026-10-08T10:07:02Z",
           "result": null, "error": null},
  "request_id": "c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7"
}
```

Errors: `404000` unknown operation; `409020` the operation already finished.

## 14. Examples

### 14.1 Nightly pipeline (Python SDK)

A scheduled job starts tonight's run, waits for it, and approves
candidates that the owner's policy lets the pipeline approve. The key is
deterministic, so if the job itself crashes and is rerun, it re-attaches to
the same run instead of starting a second one.

```python
import asyncio
from avernet_evolution import Client, PolicyDenied, Transient

async def nightly(bot: str, binding: str, day: str) -> None:
    c = Client.from_env()                                   # base URL and client settings from configuration
    run = await c.runs.start(bot, binding=binding,
                             idempotency_key=f"nightly-{bot}-{day}")  # same key on every rerun of this job
    try:
        run = await c.runs.wait(bot, run.run_id, timeout_s=3 * 3600)  # short lookups by id
    except Transient:
        return                                              # still running; the next invocation re-attaches
    print(run.run_id, run.status, f"request_id={run.request_id}")
    if run.status != "completed":
        return                                              # failed / cancelled / budget_exhausted: nothing to approve

    for cand in await c.runs.candidates(bot, run.run_id):
        report = await c.review.report(bot, cand.candidate_id)
        if report["gate"]["decision"] != "needs_review":
            continue
        try:
            await c.review.approve(bot, cand.candidate_id, reason="nightly auto-policy",
                                   idempotency_key=f"nightly-{bot}-{day}/approve/{cand.candidate_id}")
        except PolicyDenied as e:                           # the owner's policy does not let pipelines approve this tier
            print("left for human review:", cand.candidate_id, e.code, e.request_id)

asyncio.run(nightly("bot_123", "bind_01", "2026-10-08"))
```

### 14.2 CI script with `avn`

A CI job runs after a skill change. It uses exit codes rather than parsing
text, and stores the idempotency key so a retried CI step reuses it.

```bash
set -u
KEY="ci-${CI_PIPELINE_ID}-bot_123"            # stable across retries of this pipeline
out=$(avn evolve run start --bot bot_123 --binding bind_01 \
        --idempotency-key "$KEY" --wait --timeout 2h --output json)
rc=$?
case $rc in
  0) status=$(echo "$out" | jq -r '.data.status')
     echo "run $(echo "$out" | jq -r '.data.run_id') ended: $status" ;;
  5) echo "policy refused the run (evolution frozen or binding invalid)"; exit 1 ;;
  6) echo "budget ceiling reached; skipping"; exit 0 ;;
  7) echo "transient; rerun this step (same key re-attaches)"; exit 1 ;;
  *) echo "$out" | jq -r '.request_id' >&2; exit 1 ;;
esac
```

The JSON printed on success:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "code": 200000,
  "message": "OK",
  "data": {"run_id": "run_7f3", "status": "completed", "binding": "bind_01"},
  "request_id": "a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5",
  "idempotency_key": "ci-48211-bot_123"    // added by avn for commands that take a key
}
```

### 14.3 Editing the policy safely (Python SDK)

Read-modify-write with `If-Match`; on `412` re-read and reapply.

```python
from avernet_evolution import Client, PreconditionFailed

async def raise_budget(c: Client, bot: str, binding_id: str, max_usd: int) -> None:
    for _ in range(3):
        policy, etag = await c.policy.get(bot)
        for b in policy.bindings:
            if b.id == binding_id:
                b.budget.max_usd = max_usd
        try:
            await c.policy.put(bot, policy, if_match=etag)
            return
        except PreconditionFailed:
            continue                                         # someone else changed it; read again
    raise RuntimeError("policy kept changing; giving up")
```

### 14.4 Review queue in the UI backend (TypeScript SDK)

```typescript
import { Client, PolicyDenied } from "@avernet/evolution";

const client = Client.fromEnv();

export async function reviewQueue(bot: string) {
  const items = [];
  for await (const item of client.review.iterQueue(bot)) {        // walks all pages
    const report = await client.review.report(bot, item.candidateId);
    items.push({ candidate: item.candidateId, tier: report.riskTier, gate: report.gate, diff: report.diff });
  }
  return items;
}

export async function approve(bot: string, candidateId: string, reason: string, key: string) {
  try {
    return await client.review.approve(bot, candidateId, { reason, idempotencyKey: key });
  } catch (e) {
    if (e instanceof PolicyDenied) return { refused: true, code: e.code, requestId: e.requestId };
    throw e;
  }
}
```

The UI generates the approval key once when the reviewer opens the
approve dialog, so a double click or a network retry records one decision.

### 14.5 Going back to an earlier revision

Going back is promoting an earlier revision; there is no separate rollback
call.

```bash
avn genome promote --bot bot_123 --revision r41 \
  --reason "r42 raised refund escalations" --yes --output json
```

`avn` resolves `r41` to its revision id through `GET /bots/{bot}/genome/refs`
and `GET /bots/{bot}/genome/revisions`, then calls
`POST /bots/{bot}/genome/promotions`. For a service bot the revision is
published as the next version through the existing publish flow
([08-promotion.md](08-promotion.md)).

## 15. Interactions

| Other part | Direction | What flows |
| --- | --- | --- |
| [01-genome.md](01-genome.md) (Genome Registry, Backend) | API → Genome | Revision, ref, diff, and content calls; CAS on refs; content-idempotent revision creates |
| [02-experience.md](02-experience.md) | API → Experience | Episode queries, feedback records |
| [03-strategy.md](03-strategy.md) (Strategy Registry) | API → Registry | Strategy registration (`avn strategy publish`), listing, capability catalog; the Strategy SDK is a separate package |
| [05-experiment-ledger.md](05-experiment-ledger.md) | API → Ledger | Entry queries, exports as operations |
| [06-evolution-run.md](06-evolution-run.md) | API → Run | Policy (ETag), run submission (idempotency key → run id), status by id, cancellation; budgets, kill switches, and binding checks produce the policy and budget error families; the internal Job Protocol uses the same conventions |
| [07-verification.md](07-verification.md) | API → Verification | Suites, ad-hoc evaluations as operations |
| [08-promotion.md](08-promotion.md) | API → Promotion | Candidate reports, review queue, approve/reject, promotions (including going back) |
| Pipelines and CI | Caller → API | Client SDK or `avn`; deterministic idempotency keys; exit codes |
| UI backend | Caller → API | TypeScript SDK; ETags on policy edits; one key per user action |
| Human operators | Caller → API | `avn` and UI |
| Backend OpenAPI v1 adapter | Reused by this layer | `Envelope`, `ErrorEnvelope`, `Page`, `PageParams`, envelope builders and error mapping |

## 16. Open decisions

| ID | Question | Current position |
| --- | --- | --- |
| A-1 | Is `avn` a new binary, or a subcommand group of an existing CLI? | Recommended: a new `avn` binary in Rust following `bcs-cli` conventions, with room for other platform commands later. No general Avernet CLI exists today |
| A-2 | How long are idempotency records kept? | Proposed: at least 30 days, longer than any caller's retry horizon (a nightly job retried the next day must still re-attach). Decide with RSI-07 |
| A-3 | Cursor pagination for large append-only collections (episodes, ledger entries)? | Proposed: keep page numbers for v1 consistency; add an opaque `cursor` for these two collections if `total` or shifting pages become a problem |
| A-4 | Structured error details (for example which capability has no provider in a binding check)? | OpenAPI v1 pins `data` to `null` on errors and uses fixed messages. Options: keep that and encode the detail in the subcode; or allow a `data.details` object for evolution errors only. Undecided |
| A-5 | A uniform validate-only mode (`dry_run`) for writes? | `genome patch apply --dry-run` needs one; whether other endpoints get it is up to their owning docs |
| A-6 | One merged OpenAPI document or one per service? | Proposed: each service publishes its part; a build step merges them into the one public document the SDKs and CLI are generated from |
| A-7 | Should `--wait` map terminal run statuses to exit codes? | Proposed: no; exit codes reflect the API call and scripts read `.data.status`. Revisit if CI users ask for it |
| A-8 | Event delivery (listed in [work-items.md](work-items.md) as Q1 to Q3: "CLI binary, default bot run requests, event delivery"; A-1 is Q1, A-8 is Q3) | Current design has no callback or webhook channel: status by id is the only mechanism. A push channel would be an addition, not a replacement |

Q2 (whether subject bots may request runs by default) belongs to the
postponed bot callers ([§3](#3-callers)).
