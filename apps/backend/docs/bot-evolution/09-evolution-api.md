# Evolution API

> 中文版：[09-evolution-api.zh-CN.md](09-evolution-api.zh-CN.md)

> Status: DRAFT. Service in the [bot evolution architecture](design.md).
> The public access layer: the conventions every evolution endpoint shares,
> the index of all public endpoints, and the OpenAPI documents that publish
> them.

## 1. Purpose and scope

The bot evolution platform is reached through **one resource API** under
the public prefix `/openapi/v1`, covering the Genome endpoints and the
evolution endpoints, and described by OpenAPI documents. That API is the
whole public surface: the UI backend, pipelines, and CI call it over HTTP.

Client SDKs and a CLI are out of scope until a use case appears. If one is
added later, it is generated from the OpenAPI documents and adds no
capability the API lacks, so callers cannot drift apart.

**This doc owns:**

| Owned here | What it means |
| --- | --- |
| Shared API conventions | Envelope, request ids, JSON rules, path ids and custom methods, idempotency keys, ETags and `If-Match`, error codes, pagination, long-running work by id |
| The endpoint index | Which public endpoints exist and which doc owns each one ([§10](#10-endpoint-index)) |
| The OpenAPI documents and their gateway routing | Which document each serving service publishes, how the gateway routes each path to its service, the compatibility gate, and the contract tests ([§11](#11-openapi-documents-and-gateway-routing)) |

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
`apps/backend/docs/openapi-v1/README.md`). The deliverable of work item
RSI-07 in [work-items.md](work-items.md) is the OpenAPI documents for these
endpoints, with their gateway routing and shared conformance tests.

## 2. Domain model

The types here are the shared wire conventions, not the evolution
resources themselves (those are defined in their owning docs).

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `Envelope[T]` | The uniform response wrapper: `code`, `message`, `data`, `request_id` | This doc (reuses OpenAPI v1) | Per response |
| `ErrorEnvelope` | The same wrapper on failure, with `data` always `null` | This doc (reuses OpenAPI v1) | Per response |
| `Page[T]` / `PageParams` | A page of a list result (`total`, `items`) and the controls (`page`, `page_size`) | This doc (reuses OpenAPI v1) | Per request |
| `BotRef` | The full identity of one bot: `owner_id` plus `bot_id`, because a `bot_id` alone is not unique across users ([§2.6](#26-botref-bot-identity)) | This doc; used by every evolution record | Fixed for the bot's life |
| `IdempotencyRecord` | The platform's memory of one idempotency key. The **key** is the value of the client's `Idempotency-Key` header, e.g. `nightly-bot_123-2026-10-08`; the record maps it to the **result id**, the id of what the first request with that key created or started, e.g. the run `run_7f3`, plus a fingerprint of the request. A retry with the same key gets `run_7f3` back instead of starting a second run ([§2.3](#23-idempotencyrecord)) | This doc; stored by each service | Created on the first request with a key; kept for the retention window |
| `Precondition` | A version check on writes. A `GET` of a resource that clients replace (today only the bot's bindings) returns an `ETag` header, a version tag such as `"bind-v12"`; the client sends it back in an `If-Match` header on its `PUT`, and the server refuses the write with `412` if the resource changed since that read ([§2.4](#24-precondition-etag-and-if-match)) | This doc | Per resource version |
| `Operation` | A long-running unit of work started by a request and looked up by id (`queued`, `running`, `succeeded`, `failed`, `cancelled`) | Concept defined in [03-strategy.md](03-strategy.md); the public resource and its lookup endpoint are owned here ([§10.8](#108-operations--this-doc)) | Created by a `202` response; terminal once finished |

### 2.1 Envelope and ErrorEnvelope

Every JSON response, success or failure, is an envelope. This is the
existing OpenAPI v1 shape; the evolution endpoints adopt it unchanged so
that a caller handles the Genome endpoints and the evolution endpoints the
same way. `code` is six digits: the HTTP status (three) followed by a
business subcode (three), for example `200000` (OK), `202000` (Accepted),
`404000` (not found). Binary content (for example
`GET /bots/{bot_id}/genome/content/{digest}`) bypasses the envelope; that is
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
// An error envelope: the bindings changed since the caller read them
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
from enum import StrEnum

class IdempotentResultKind(StrEnum):
    """What a key can create or start (one value per row of the §5.3 table)."""
    RUN = "run"
    OPERATION = "operation"
    REVISION = "revision"
    PROMOTION = "promotion"
    REVIEW_DECISION = "review_decision"   # an approve or reject of a review item
    FEEDBACK = "feedback"

@dataclass(frozen=True)
class IdempotencyRecord:
    bot: BotRef            # scope: the bot addressed by the request (owner + bot id, §2.6)
    key: str               # the client's Idempotency-Key header value
    fingerprint: str       # sha256 of the RFC 8785 canonical form of {method, path, body}
    result_kind: IdempotentResultKind
    result_id: str         # id of what the first request created or started, e.g. "run_7f3"
    created_at: datetime
    expires_at: datetime   # end of the retention window (open decision A-2, §15)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "key": "nightly-bot_123-2026-10-08",
  "fingerprint": "sha256:5d1e…",
  "result_kind": "run",
  "result_id": "run_7f3",
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

It prevents a **lost update**, a race between two writers of the same
resource. Example: the owner opens bot_123's bindings in the UI and a
pipeline reads them too; both get `ETag: "bind-v12"`. The pipeline raises
`bind_01`'s budget and saves first (`If-Match: "bind-v12"` matches; the
bindings become `bind-v13`). The owner then saves a new trigger for
`bind_02` with `If-Match: "bind-v12"`. Without the check, the owner's
save would replace the whole list with their stale copy and silently undo
the budget change. With it, the owner gets `412`, re-reads `bind-v13`,
and reapplies the trigger change on top (the loop in [§6](#6-concurrency-etags-if-match-and-compare-and-swap)).

```python
@dataclass(frozen=True)
class Precondition:
    etag: str              # opaque, strong, quoted on the wire: "\"bind-v12\""
    resource: str          # the path it belongs to
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// What a caller keeps after GET /bots/bot_123/evolution/bindings, to send back as If-Match
{"etag": "\"bind-v12\"", "resource": "/openapi/v1/bots/bot_123/evolution/bindings"}
```

### 2.5 Operation (public view)

An **operation** is work that can outlast a short HTTP request: an
evaluation, a ledger export. Starting it returns `202 Accepted` with
`{operation_id}` at once; the caller then looks up its status by id with
`GET /bots/{bot_id}/evolution/operations/{operation}`, the single public
operation resource ([§10.8](#108-operations--this-doc)). The same concept is
used inside strategies (agent sessions, train evaluations); see
[03-strategy.md](03-strategy.md). Runs follow the same pattern with their
own status values ([06-evolution-run.md](06-evolution-run.md)).

```python
class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

class OperationKind(StrEnum):
    """The public operation kinds."""
    EVALUATION = "evaluation"          # an ad-hoc evaluation (07-verification.md)
    LEDGER_EXPORT = "ledger_export"    # a ledger export (05-experiment-ledger.md)

@dataclass(frozen=True)
class OperationError:
    code: int              # same code space as ErrorEnvelope.code
    message: str

@dataclass(frozen=True)
class Operation:
    id: str                         # "op_19a"
    kind: OperationKind             # which kind of work; decides the shape of `result`
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
  "result": {"digest": "sha256:e7a2…", "format": "filesystem", "entries": 412},
  "error": null
}
```

### 2.6 BotRef (bot identity)

A `bot_id` is unique only within one owner: two users can each have a bot
called `bot_123`. OpenAPI v1 already handles this today: it addresses a bot
as `/openapi/v1/bots/{bot_id}/…` and names the bot's owner with the
`entity_id` query parameter, which defaults to the caller
(`apps/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py`;
`resolve_owner_id` in `…/openapi_v1/engine_runtime/params.py`). Every
evolution record therefore stores the pair, as one `BotRef`, and every
internal service method takes a `BotRef`, never a bare bot id. Run,
candidate, operation, revision, and ledger-entry ids are different: they
are globally unique on their own.

```python
@dataclass(frozen=True)
class BotRef:
    owner_id: str          # the user (entity) who owns the bot; the `entity_id` of OpenAPI v1
    bot_id: str            # the bot's id within that owner, as in /bots/{bot_id}/…; not unique on its own
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// How a record names its bot (field "bot" of an IdempotencyRecord, Run, LedgerEntry, …)
{"owner_id": "user_owner_5", "bot_id": "bot_123"}
```

On the wire the pair is split the way OpenAPI v1 splits it: `bot_id` in the
path and the owner as `entity_id` ([§4.1](#41-paths-resources-and-custom-methods)).

## 3. Callers

In the first iteration the API has three kinds of callers. They use the
same endpoints over HTTP; what each may do is decided by the owning docs
(for promotion and approval, [08-promotion.md](08-promotion.md)), not by a
separate API per caller.

| Caller | Example | Surface | Typical calls |
| --- | --- | --- | --- |
| **Deterministic pipeline / CI** | Nightly job, CI after a skill change, a product backend | HTTP requests to `/openapi/v1` | Start a run, look up its status, read candidate reports, approve within the owner's auto-promote policy |
| **UI backend** | The evolution pages of the workbench (AgentEvolve UI in the interim) | HTTP requests to `/openapi/v1` | Everything a human does in the UI: bindings editing, review queue, reports, promotions, ledger browsing |
| **Human operator** | Bot owner, tenant admin, researcher | The workbench UI, which calls the API through the UI backend | Everything, including review, promotion, and going back to an earlier revision |

Strategy job workers are platform services and use the internal Job
Protocol through the Strategy SDK, not this public API
([06-evolution-run.md](06-evolution-run.md), [03-strategy.md](03-strategy.md)).

Bot callers (a bot driving its own evolution, or a bot acting as a job
runner, and the skill or MCP adapter they would use) are postponed with
DR-3 ([decisions/0003-bot-principal-for-evolution-surface.md](decisions/0003-bot-principal-for-evolution-surface.md)).

## 4. Request and response conventions

### 4.1 Paths, resources, and custom methods

- **Prefix.** Every public path is under `/openapi/v1`. Paths in this design
  set are written relative to it.
- **Resource-oriented.** Collections are plural nouns
  (`/bots/{bot_id}/evolution/runs`); a member is the collection plus its id.
  `GET` reads, `POST` on a collection creates or starts, `PUT` replaces a
  whole mutable resource.
- **Bot addressing.** Bot-scoped paths carry the bot id in the path
  (`/bots/{bot_id}/…`) and the bot's owner in the **`entity_id` query
  parameter**, which defaults to the caller, exactly as OpenAPI v1 does
  today ([§2.6](#26-botref-bot-identity)). A `bot_id` is not unique across
  users, so the server always resolves the pair `(entity_id or caller,
  bot_id)` before doing anything else. Most examples in this design set omit
  `entity_id` because the caller is the owner; a collaborator or a pipeline
  acting on someone else's bot adds it, for example
  `POST /bots/bot_123/evolution/runs?entity_id=user_owner_5`. Whether that
  caller may act on the bot is decided by the existing OpenAPI v1 access
  check, not by this layer.
- **Custom methods** for actions that are not plain create or replace use a
  colon suffix on the resource: `:cancel`, `:approve`, `:reject`,
  `:export`. They are always `POST`.
- **Ids that contain a colon or a slash.** Revision and candidate ids are
  content hashes such as `sha256:a90b…`; strategy ids contain a `/`
  (`acme/correction-fixer`). Inside a path segment clients percent-encode
  the colon (`sha256%3Ac41e…`) and the slash (`acme%2Fcorrection-fixer`).
  The server splits a custom method on the **last** colon of the final
  segment, so `/candidates/sha256%3Ac41e…:approve` is unambiguous. Callers
  take ids as they appear in responses and encode them when they build a
  path.
- **Per-bot sequence numbers.** A revision's `seq` (shown to humans as
  `r41`) is not an identity ([01-genome.md](01-genome.md)). The API takes
  revision ids; an endpoint accepts the `r41` form only where its owning
  doc says so (for example `POST /bots/{bot_id}/genome/promotions` in
  [08-promotion.md](08-promotion.md)). Elsewhere a caller resolves `r41`
  through `GET /bots/{bot_id}/genome/revisions` first.

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

A caller logs the request id of every failed request; it is what support
needs to find that request.

### 4.3 JSON

- Request and response bodies are JSON (`application/json`), UTF-8.
  Everything new in this design is JSON; the Bot Config Manifest stays YAML
  as today ([01-genome.md](01-genome.md)). The one exception is strategy
  registration, which uploads agent-definition directories as
  `multipart/form-data` ([03-strategy.md](03-strategy.md)).
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
- Callers ignore response fields they do not know.
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
"key" in the endpoint index ([§10](#10-endpoint-index)).

### 5.1 Rules for the client

- The key is a **client-chosen string**, 1 to 255 printable ASCII
  characters; the recommended alphabet is `[A-Za-z0-9._:/-]`. It travels in
  a header and is sent as is; only ids inside a path are percent-encoded
  ([§4.1](#41-paths-resources-and-custom-methods)).
- It is **identical for every retry of one logical request** and
  **different for different requests**. Generate it once per logical
  action, before the first attempt, and reuse it on every retry.
- It must **not** be something that changes between retries. A timestamp
  taken at send time, or a random value generated per attempt, makes every
  retry look like a new request and defeats the purpose.
- If the process may restart before it has the answer, **persist the key**
  with the job's state, or derive it deterministically from what makes the
  request unique. A key held only in memory cannot protect a retry made by
  a new process.
- Retry automatically only `GET`, `PUT` with `If-Match`, and `POST` with a
  key. Never retry a `POST` sent without a key: the first attempt may have
  succeeded.
- On a transient failure, back off exponentially with jitter, and honour
  `Retry-After` when the response carries it. Which codes are transient is
  listed in [§7.2](#72-retry-guidance).

Good keys by caller:

| Caller | Key | Example |
| --- | --- | --- |
| Pipeline (one-off logical request) | A UUID created **once, before the first attempt**, and stored with the job's state | `0b6f7c52-3d0e-4c1b-9a63-8f2a7d0e5c11` |
| Pipeline (scheduled, one per period) | Deterministic from what makes the request unique | `nightly-bot_123-2026-10-08` |
| UI backend (one user action) | A UUID created when the user opens the action (for example the approve dialog), so a double click or a network retry records one decision | `4b0f6c1e-9a7d-4f53-8d1e-2c6a9e1b7f02` |
| Platform trigger (a binding firing on schedule) | `<binding_id>/<scheduled_fire_time>` | `bind_01/2026-10-08T02:00:00Z` |
| Strategy (internal, Job Protocol) | `<run_id>/<own step>` | `run_7f3/round-2/tune` |

The scheduled fire time in a trigger key is the time the schedule named,
not the time the trigger actually ran, so a trigger that fires late or
twice still produces one run.

### 5.2 Rules for the platform

- The platform stores **`(owner_id, bot_id, key) → result id`**, together
  with a fingerprint of the request (`sha256` of the canonical `{method,
  path, body}`). For bot-scoped paths the scope is the addressed bot: the
  `bot_id` in the path and its owner (`entity_id`, or the caller when it is
  omitted), so two owners' bots with the same `bot_id` never share keys.
- The record is written **in the same transaction** as the resource it
  points to, so a crash can never leave a created run without its key, or a
  key without its run.
- **Same key, same request** (matching fingerprint): the platform returns
  the original result: the endpoint's success status (each endpoint has
  one, e.g. `202` for a run, so it is not stored) and the same result id, with the resource's **current** state in `data` (a run that
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
  retry. The window is an open decision ([§15](#15-open-decisions)); after
  it expires, the key may be reused.
- **Naturally idempotent creates.** Some creates are idempotent by content
  and need no key: a candidate id is the content hash of its patch, a
  revision id is the hash of `{spec, policy}`, and registering a strategy
  version that already exists with identical content returns that version.
  They still accept a key for uniformity.

### 5.3 Where keys are used

| Endpoint | Key creates or starts |
| --- | --- |
| `POST /bots/{bot_id}/evolution/runs` | A run (`202`, run id) |
| `POST /bots/{bot_id}/evolution/evaluations` | An evaluation operation (`202`, operation id) |
| `POST /bots/{bot_id}/evolution/ledger:export` | An export operation (`202`, operation id) |
| `POST /bots/{bot_id}/genome/revisions` | A revision (also content-idempotent) |
| `POST /bots/{bot_id}/genome/promotions` | A promotion (moving `active`) |
| `POST /bots/{bot_id}/evolution/candidates/{candidate}:approve` and `:reject` | A review decision |
| `POST /bots/{bot_id}/evolution/runs/{run}:cancel` | A cancellation (also idempotent by state) |
| `POST /bots/{bot_id}/evolution/operations/{operation}:cancel` | An operation cancellation (also idempotent by state) |
| `POST /bots/{bot_id}/experience/feedback` | A feedback record |
| `POST /evolution/strategies` | A strategy version (also content-idempotent) |

`PUT` requests are idempotent by definition (the same body produces the
same state) and use `If-Match` instead of a key.

## 6. Concurrency: ETags, If-Match, and compare-and-swap

Two writers changing the same resource must not silently overwrite each
other. The API uses two mechanisms, chosen per resource by its owning doc:

**ETag and `If-Match`** for whole-resource replacement (`PUT`). Today's
`PUT /config-manifest` has neither. The genome does not change that
endpoint: edits made through the genome API are protected by
compare-and-swap on refs instead, and a direct Manifest edit is detected
afterwards as drift ([01-genome.md](01-genome.md) §5.5).

- A `GET` of a mutable resource returns an `ETag` header (a strong,
  opaque, quoted tag).
- A `PUT` must send `If-Match: <etag>`. If the resource changed since, the
  server answers `412` (`412000`). If `If-Match` is missing, it answers
  `428 Precondition Required` (`428000`), so a client cannot skip the check
  by accident.
- The first mutable resource on the public surface is the bot's bindings
  (`GET|PUT /bots/{bot_id}/evolution/bindings`).

A caller changes such a resource with a **read-modify-write loop**:
`GET` the resource and keep its `ETag`; apply the change to that copy;
`PUT` it with `If-Match` set to the kept tag. On `412`, someone else wrote
in between: `GET` again (new body, new `ETag`), reapply the change to the
fresh copy, and `PUT` again. After a few rounds without success the caller
stops and reports the conflict rather than looping forever.

**Compare-and-swap in the body** for ref moves. Ref updates carry
`expected_revision`: the revision the client believes the ref points to
now. A mismatch is `409` (`409010`). This is the mechanism defined by the
Genome Registry for `PUT /bots/{bot_id}/genome/refs/draft`
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
the shared ones and the **families** callers branch on:

| Code | Meaning | Retry? |
| --- | --- | --- |
| `400000` | Request body or query fails validation (schema, enum, range) | No: fix the request |
| `404000` | Resource not found (bot, run, revision, candidate, strategy, suite, entry) | No |
| `409001` | Idempotency key reused with a different request | No: client bug |
| `409002` | A request with this idempotency key is still in progress | Yes, same key |
| `409010` | Ref compare-and-swap failed: `expected_revision` is stale | After re-reading |
| `409011` | Strategy version already registered with different content | No |
| `409020` | Action conflicts with the resource's state (e.g. approving a rejected candidate, cancelling a finished run) | No |
| `412000` | `If-Match` does not match the current `ETag` | After re-reading |
| `428000` | `If-Match` required but missing | After adding it |
| `403xxx` / `422xxx` (policy family) | The request violates the bot's bindings, its genome policy, or a platform rule: locked gene, gene outside a binding's `allowed_genes`, binding check failed, evolution frozen by a kill switch, strategy disabled, candidate not promotable under the gate | No |
| `403100`-range (budget family) | A budget ceiling refuses the request (per-bot or per-tenant daily or monthly ceiling, max promotions per day) | Not until the ceiling resets |
| `429000` | Rate limited | Yes, after `Retry-After` |
| `502000`, `503000`, `504000` | Upstream or service unavailable | Yes, same key |
| `500000` | Unexpected server error | Only with an idempotency key |

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
- `POST` without a key is never retried automatically.
- `PUT` with `If-Match` is safe to retry; a `412` after a retry means
  someone else wrote in between (or the first attempt succeeded): re-read
  and decide ([§6](#6-concurrency-etags-if-match-and-compare-and-swap)).
- Back off exponentially with jitter, honouring `Retry-After`
  ([§5.1](#51-rules-for-the-client)).

## 8. Pagination and filtering

- Lists use `page` (1-based) and `page_size` (1 to 100, default 20), and
  return `Page {total, items}`, as OpenAPI v1 does today
  (`PageParams` in `contracts.py`).
- Filters are query parameters named after the field they filter
  (`status=`, `revision=`, `outcome=`, `run=`, `strategy=`); time ranges
  use `since=` and `until=` (RFC 3339). Each owning doc lists its filters.
- Default ordering is newest first by creation time unless the owning doc
  says otherwise; ordering is stable for equal timestamps (by id).
- To read a whole collection, a caller requests `page=1, 2, …` with the
  same filters until it has `total` items or receives an empty page.
- Large, append-only collections (episodes, ledger entries) may need cursor
  pagination instead of page numbers, because `total` is expensive and pages
  shift as entries are added. That is an open decision
  ([§15](#15-open-decisions)); until it is decided, the existing convention
  applies.

## 9. Long-running work by id

No request is held open while work runs. Work that can outlast a short
request follows one pattern:

1. **Start returns an id at once.** The start request records the work and
   answers `202 Accepted` (`202000`). For an operation, `data` is
   `{operation_id}` and the `Location` header names
   `/bots/{bot_id}/evolution/operations/{operation}`; for a run, `data` is the
   run as it is now (status `queued`) and `Location` names the run.
2. **Status is looked up by id.** The caller repeats a short `GET` on that
   URL until the status is terminal. Every lookup returns promptly. The
   response may carry `Retry-After` as a hint for the next lookup.
3. **Start is idempotent.** With the same idempotency key, a repeated start
   returns the same id ([§5](#5-idempotency-keys)).
4. **The id is the only handle.** There is no callback or webhook channel.
   Waiting means repeating the lookup; a caller that gives up after its own
   timeout leaves the work running, and a later lookup by the same id (or a
   repeated start with the same key) finds it again.
5. **The work survives crashes.** A run survives worker crashes and
   restarts under the same run id ([06-evolution-run.md](06-evolution-run.md));
   operations are persisted by the platform independently of the caller.

| Started by | Returns | Look up with | Statuses |
| --- | --- | --- | --- |
| `POST /bots/{bot_id}/evolution/runs` | run id | `GET /bots/{bot_id}/evolution/runs/{run}` | `queued \| running \| completed \| failed \| cancelled \| budget_exhausted` |
| `POST /bots/{bot_id}/evolution/evaluations` | operation id | `GET /bots/{bot_id}/evolution/operations/{operation}`; its `result` carries the `evaluation_id`, read with `GET /bots/{bot_id}/evolution/evaluations/{evaluation}` | `queued \| running \| succeeded \| failed \| cancelled` |
| `POST /bots/{bot_id}/evolution/ledger:export` | operation id | `GET /bots/{bot_id}/evolution/operations/{operation}` | `queued \| running \| succeeded \| failed \| cancelled` |

Candidates and verdicts follow the same "by id" rule one level down: a
candidate's report is looked up by candidate id
(`GET /bots/{bot_id}/evolution/candidates/{candidate}`), and there is no
blocking call that waits for a verdict.

## 10. Endpoint index

All public paths are under the prefix **`/openapi/v1`** and are written
relative to it. Each endpoint's full schema, examples, and errors are in
its owning doc; this section lists every public endpoint, names its owning
doc, and shows the shared conventions once per group. Which service serves
each group follows "Where it runs" in [§1](#1-purpose-and-scope). Legend:
**key** = takes `Idempotency-Key`; **202** = starts long-running work
looked up by id; **ETag** = `If-Match` required on write; **CAS** =
compare-and-swap in the body.

### 10.1 Genome — [01-genome.md](01-genome.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot_id}/genome/revisions` | List revisions (`status=`, `parent=`) | Page |
| `POST /bots/{bot_id}/genome/revisions` | Record a revision from `{base, patch}` or `{manifest}` | key; content-idempotent |
| `GET /bots/{bot_id}/genome/revisions/{rev}` | One revision | Immutable ETag |
| `GET /bots/{bot_id}/genome/revisions/{rev}/diff?against=` | Diff two revisions | |
| `GET /bots/{bot_id}/genome/refs` | Named refs (`active`, `previous`, `canary`, `draft`, …) | |
| `PUT /bots/{bot_id}/genome/refs/draft` | Move the owner's `draft` ref | CAS (`expected_revision`) |
| `GET /bots/{bot_id}/genome/content/{digest}` | Content bytes by digest | Binary, no envelope |
| `PUT /bots/{bot_id}/genome/content` | Upload content, returns its digest | Content-idempotent |

Shared-convention example: compare-and-swap on a ref.

```http
PUT /openapi/v1/bots/bot_123/genome/refs/draft
Content-Type: application/json
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:7c1e…", "expected_revision": "sha256:a90b…"}   // r42 replaces r41, only if draft is still r41
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

### 10.2 Experience — [02-experience.md](02-experience.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot_id}/experience/episodes` | List episodes (`revision=`, `since=`, `outcome=`) | Page |
| `GET /bots/{bot_id}/experience/episodes/{episode}` | One episode | |
| `POST /bots/{bot_id}/experience/feedback` | Record a rating, correction, or outcome | key |
| `GET /bots/{bot_id}/experience/feedback` | List feedback | Page |

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

### 10.3 Strategy registry — [03-strategy.md](03-strategy.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /evolution/strategies` | List registered strategies (`engine=`, `conformance=`) | Page |
| `POST /evolution/strategies` | Register a strategy version (with its agent definitions) | key; content-idempotent |
| `GET /evolution/strategies/{id}/versions/{version}` | One registration record and its conformance status | Immutable ETag |
| `GET /evolution/capabilities` | The capability catalog | |

Shared-convention example: re-registering an existing version with
different content is a conflict, not a silent overwrite. The request is
`multipart/form-data` with a `registration` part and one
`definition:<name>` part per agent definition, as
[03-strategy.md](03-strategy.md) defines; only the `registration` part is
shown, shortened.

```http
POST /openapi/v1/evolution/strategies
Content-Type: multipart/form-data; boundary=…
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Part "registration" (other needs and the definition parts omitted)
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {"experience.sessions@1": {}, "evaluation.train@1": {}}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 409: version 2.0.0 already exists with a different record; register 2.0.1 instead
{
  "code": 409011,
  "message": "Strategy version already registered with different content",
  "data": null,
  "request_id": "2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c"
}
```

### 10.4 Experiment ledger — [05-experiment-ledger.md](05-experiment-ledger.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot_id}/evolution/ledger` | List entries (filters such as `run=`, `strategy=`, `since=`) | Page |
| `GET /bots/{bot_id}/evolution/ledger/{entry}` | One entry | Immutable ETag |
| `POST /bots/{bot_id}/evolution/ledger:export` | Export entries | key; 202 + operation id |

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

### 10.5 Evolution Run — [06-evolution-run.md](06-evolution-run.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot_id}/evolution/bindings` | The bot's bindings (list of bindings) | Returns ETag |
| `PUT /bots/{bot_id}/evolution/bindings` | Replace the bindings (binding checks run here) | ETag (`If-Match` required) |
| `POST /bots/{bot_id}/evolution/runs` | Start a run of one binding | key; 202 + run id |
| `GET /bots/{bot_id}/evolution/runs` | List runs (`status=`) | Page |
| `GET /bots/{bot_id}/evolution/runs/{run}` | Run status, rounds, budget used | |
| `POST /bots/{bot_id}/evolution/runs/{run}:cancel` | Cancel a run | key; idempotent by state |
| `GET /bots/{bot_id}/evolution/runs/{run}/candidates` | Candidates the run submitted | |

Shared-convention example 1: idempotent run submission. The first request
and a retry with the same key get the same run id. The caller here is a
pipeline acting on a bot it does not own, so it names the owner with
`entity_id` ([§4.1](#41-paths-resources-and-custom-methods)).

```http
POST /openapi/v1/bots/bot_123/evolution/runs?entity_id=user_owner_5
Content-Type: application/json
Idempotency-Key: nightly-bot_123-2026-10-08
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"binding": "bind_01", "budget": {"max_usd": 10, "max_wall_clock_s": 3600}}   // body {binding, params?, budget?}, defined in 06-evolution-run.md
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 202 on the first attempt; Location: /openapi/v1/bots/bot_123/evolution/runs/run_7f3?entity_id=user_owner_5
{
  "code": 202000,
  "message": "Accepted",
  "data": {"run_id": "run_7f3", "status": "queued"},
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
  "data": {"run_id": "run_7f3", "status": "running"},
  "request_id": "5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f"
}
```

Shared-convention example 2: `If-Match` on the bindings.

```http
PUT /openapi/v1/bots/bot_123/evolution/bindings
Content-Type: application/json
If-Match: "bind-v12"
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
// 200 with the new ETag header: ETag: "bind-v13"
{
  "code": 200000,
  "message": "OK",
  "data": {"bindings": [{"id": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0"}]},
  "request_id": "6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a"
}
```

A stale tag gets `412000` ([§2.1](#21-envelope-and-errorenvelope)); a
missing `If-Match` gets `428000`. (The response `data` above is shortened
for the example; the owning doc defines the full bindings shape.)

**Internal (not part of the public API).** The Job Protocol under
`/evolution/v1/...`, which job-worker strategies use to claim runs and call
their context, is owned by [06-evolution-run.md](06-evolution-run.md). It
is not exposed through the gateway and is not in the published OpenAPI
documents. It follows the same conventions: JSON, idempotent starts with
keys of the form `<run_id>/<step>`, and long-running calls answered with
`202` and an operation id.

### 10.6 Verification — [07-verification.md](07-verification.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /evolution/suites` | List suites | Page |
| `GET /evolution/suites/{suite}` | One suite; case contents subject to split visibility | |
| `POST /bots/{bot_id}/evolution/evaluations` | Operator-only ad-hoc evaluation of a revision on chosen suites | key; 202 + operation id |
| `GET /bots/{bot_id}/evolution/evaluations/{evaluation}` | Evaluation result (the id comes from the operation's `result`) | |

Shared-convention example: start, then look up the operation by id.

```http
POST /openapi/v1/bots/bot_123/evolution/evaluations
Content-Type: application/json
Idempotency-Key: adhoc-bot_123-r42-2026-10-08
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:7c1e…", "suites": ["support-core"], "splits": ["validation", "regression"]}   // shortened; full body in 07-verification.md
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
the evaluation is read with `GET /bots/{bot_id}/evolution/evaluations/ev_310`.

### 10.7 Promotion — [08-promotion.md](08-promotion.md)

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot_id}/evolution/candidates/{candidate}` | Candidate report: diff, verification, gate decision | |
| `GET /bots/{bot_id}/evolution/review-queue` | Candidates waiting for human review | Page |
| `POST /bots/{bot_id}/evolution/candidates/{candidate}:approve` | Approve a candidate | key; state-checked |
| `POST /bots/{bot_id}/evolution/candidates/{candidate}:reject` | Reject a candidate | key; state-checked |
| `POST /bots/{bot_id}/genome/promotions` | Move `active` (including going back to an earlier revision) | key; CAS on `active` |

Shared-convention example: a custom method on a colon-containing id.

```http
POST /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…:approve
Content-Type: application/json
Idempotency-Key: review-bot_123-sha256:c41e-approve
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "Reviewed diff and verification report; escalation fix is correct."}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// 200; approving again with the same key replays this answer (data shortened; full shape in 08-promotion.md)
{
  "code": 200000,
  "message": "OK",
  "data": {
    "review": {"candidate_id": "sha256:c41e…", "status": "approved", "promotion_id": "prm_5d2"},
    "promotion": {"id": "prm_5d2", "ref": "active", "revision_id": "sha256:7c1e…", "status": "applying"}
  },
  "request_id": "9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d"
}
```

Approving a candidate that was already rejected is `409020`; approving one
the caller may not approve, or one the gate does not allow, is in the
policy family. Who may approve which risk tier is defined in
[08-promotion.md](08-promotion.md).

### 10.8 Operations — this doc

The single public operation resource, shared by every endpoint that answers
`202` with `{operation_id}` (ad-hoc evaluations in
[07-verification.md](07-verification.md), ledger exports in
[05-experiment-ledger.md](05-experiment-ledger.md)). Served by
`apps/evolution`.

| Method and path | Purpose | Conventions |
| --- | --- | --- |
| `GET /bots/{bot_id}/evolution/operations/{operation}` | Operation status and, once finished, result or error | |
| `POST /bots/{bot_id}/evolution/operations/{operation}:cancel` | Cancel an operation | key; idempotent by state |

#### GET /bots/{bot_id}/evolution/operations/{operation}

Look up an operation by id: `Operation{id, kind, status, result?, error?,
created_at, updated_at}` (§2.5). Called by pipelines and the UI backend,
repeatedly while they wait for the operation to finish (§9). Every lookup
returns promptly; the response may carry `Retry-After`.

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

#### POST /bots/{bot_id}/evolution/operations/{operation}:cancel

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

## 11. OpenAPI documents and gateway routing

The public API is published as OpenAPI documents and reached through the
gateway (`apps/gateway`), which forwards each `/openapi/v1` path to the
service that serves it. This section describes the existing practice and
what the evolution endpoints add to it. Items marked *(proposed)* do not
exist yet.

### 11.1 Existing practice

- **Documents are build outputs.** The documents in
  `apps/gateway/configs/schemas/*.openapi.json` are checked in but not
  hand-written: each is produced by its upstream's `scripts/dump_openapi.py`
  (Backend, BaaS, and BCS each have one) and published by the gateway's
  `scripts/gate_and_publish_openapi.py`, which refuses a
  backward-incompatible change unless `--allow-breaking` is passed for a
  coordinated one (`apps/gateway/configs/schemas/README.md`;
  `apps/gateway/scripts/dump_and_publish.sh` runs both ends for every
  upstream).
- **Backend publishes `bots.openapi.json`**: its public surface under
  `/openapi/v1/bots`, `/openapi/v1/org`, and the other prefixes listed in
  that README. Backend's `dump_openapi.py` narrows the document to public
  paths and the components they reference.
- **Gateway domains.** `apps/gateway/configs/application.yaml` declares
  `upstreams.domains`. A domain without `match:` claims
  `/openapi/v1/<name>/**`; a domain with a `match:` pattern claims that
  pattern, and the more specific pattern wins (more literal segments
  outrank fewer). That is how carve-outs work today: `collaboration-publish`
  (`/openapi/v1/collaboration/bots/{bot_uuid}/public/**`) sends one subtree
  of the BCS-served `collaboration` namespace to Backend. A domain pattern
  is literal and `{param}` segments ending in `**`. Who may call a path is
  set separately in `route_security` in the same file.
- **Namespace test.**
  `apps/backend/tests/community/contracts/gateway/test_public_namespace.py`
  fails if Backend serves an `/openapi/v1` route outside the declared
  prefixes; extending those prefixes must land together with the matching
  gateway domain and `route_security` entries.

### 11.2 One document per serving service

*(Proposed.)* Each serving service publishes its own document; there is no
merged document. This follows the existing practice above and resolves open
decision A-6 ([§15](#15-open-decisions)).

| Document | Published by | Contains |
| --- | --- | --- |
| `bots.openapi.json` (existing, extended) | Backend, with its existing `dump_openapi.py` | The Genome endpoints (§10.1) and the Promotion endpoints: candidate report, review queue, approve/reject, promotions (§10.7) |
| `evolution.openapi.json` (new) | `apps/evolution`, with its own `scripts/dump_openapi.py` | Experience, Strategy registry, Experiment ledger, Evolution Run, Verification, Operations (§10.2 to §10.6, [§10.8](#108-operations--this-doc)), and the level-3 Meta-evolution endpoints of [10-meta-evolution.md](10-meta-evolution.md) when they are built |

`evolution.openapi.json` is gated and published the same way as the
existing documents, through `gate_and_publish_openapi.py` and an entry in
`dump_and_publish.sh`. The internal Job Protocol (`/evolution/v1/...`) is
in neither document.

### 11.3 Gateway routing

*(Proposed.)* Paths are full gateway paths.

| Path pattern | Served by | Gateway entry |
| --- | --- | --- |
| `/openapi/v1/bots/{bot_id}/genome/**` | Backend | Existing `bots` domain (`/openapi/v1/bots/**`); no new entry |
| `/openapi/v1/bots/{bot_id}/evolution/**` | `apps/evolution` | New domain with this `match:`, server `evolution` |
| `/openapi/v1/bots/{bot_id}/evolution/candidates/**` | Backend | New carve-out domain, server `backend`; outranks the line above |
| `/openapi/v1/bots/{bot_id}/evolution/review-queue/**` | Backend | New carve-out domain, server `backend`; `**` also matches the bare `…/review-queue` |
| `/openapi/v1/bots/{bot_id}/experience/**` | `apps/evolution` | New domain with this `match:`, server `evolution` |
| `/openapi/v1/evolution/**` | `apps/evolution` | New domain `evolution` (no `match:` needed) and a new `route_security` entry |

Checked against the index: the candidate report and `:approve`/`:reject`
(`…/evolution/candidates/{candidate}` and
`…/evolution/candidates/{candidate}:approve`) fall under the `candidates`
carve-out and reach Backend, with the promotions under `genome`. A run's
candidate list (`…/evolution/runs/{run}/candidates`) stays under `runs` and
reaches `apps/evolution`, which owns runs. Bindings, runs, evaluations,
ledger, and operations reach `apps/evolution`; strategies, capabilities,
suites, and the meta-evolution paths are under `/openapi/v1/evolution/**`.

The bot-scoped paths are under `/openapi/v1/bots/**` and keep its existing
`route_security` rule. Only `/openapi/v1/evolution` is a new public prefix:
its gateway domain, its `route_security` entry, and a `servers.evolution`
base URL land in one change. Backend serves nothing under it, so Backend's
namespace test is unchanged; `apps/evolution` gets the same namespace test
with its own declared prefixes (`/openapi/v1/bots` for its bot-scoped
paths, `/openapi/v1/evolution`).

```yaml
# Illustrative excerpt of upstreams.domains in apps/gateway/configs/application.yaml (proposed)
evolution:
  server: evolution
  schema:
    source: file
    path: schemas/evolution.openapi.json
    refresh_seconds: 300
evolution-bots:
  match: /openapi/v1/bots/{bot_id}/evolution/**
  server: evolution
  protocols: [http]
evolution-promotion:
  match: /openapi/v1/bots/{bot_id}/evolution/candidates/**
  server: backend
  protocols: [http]
```

### 11.4 Compatibility gate

The publish gate enforces [§4.4](#44-compatibility-within-v1) mechanically:
a release whose document removes or renames a path or field, or tightens a
request, fails `gate_and_publish_openapi.py`. `--allow-breaking` is for a
coordinated change that moves to a new endpoint version, with the reason
recorded in the PR.

### 11.5 Contract tests

- Each service tests that the routes it serves match the document it
  publishes, so the document never describes a surface the service does
  not serve.
- The conventions (envelope, `Idempotency-Key` replay, `409001` on key
  reuse, `If-Match` and `412000`/`428000`, the error codes of
  [§7.1](#71-shape-and-code-space)) are checked by shared conformance tests
  that run against both services (work item RSI-07).

## 12. Service interface

The conventions interface is what each serving module (Backend,
`apps/evolution`) uses in its HTTP adapter so that every endpoint behaves
the same way.

```python
from collections.abc import Awaitable, Callable
from typing import Protocol

class IdempotencyStore(Protocol):
    """Implements §5. Writes happen in the caller's transaction."""

    async def lookup(self, bot: BotRef, key: str) -> IdempotencyRecord | None:
        """The record for (owner_id, bot_id, key), or None if the key is new or expired."""

    async def record(self, record: IdempotencyRecord) -> None:
        """Stores (owner_id, bot_id, key) -> result id in the same transaction that creates the resource.
        Raises KeyInProgress if another request holds the key and has not committed."""

class IdempotentHandler(Protocol):
    async def handle(self, *, bot: BotRef, key: str | None, fingerprint: str,
                     create: Callable[[], Awaitable[tuple[str, int]]],
                     current: Callable[[str], Awaitable[dict]]) -> tuple[int, dict, bool]:
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

## 13. Examples

Each example is a sequence of raw HTTP requests. Response bodies are
shortened to the fields the example uses; the owning docs have the full
shapes.

### 13.1 Nightly pipeline

A scheduled job starts tonight's run on `bot_123`, owned by
`user_owner_5`, waits for it, and approves what the owner's policy lets the
pipeline approve. Because the pipeline is not the owner, every request
carries `entity_id=user_owner_5`. The key is deterministic, so if the job
itself crashes and is rerun, step 1 re-attaches to the same run instead of
starting a second one.

1. Start the run.

   ```http
   POST /openapi/v1/bots/bot_123/evolution/runs?entity_id=user_owner_5
   Content-Type: application/json
   Idempotency-Key: nightly-bot_123-2026-10-08
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   {"binding": "bind_01"}
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 202; Location: /openapi/v1/bots/bot_123/evolution/runs/run_7f3?entity_id=user_owner_5
   {"code": 202000, "message": "Accepted", "data": {"run_id": "run_7f3", "status": "queued"},
    "request_id": "4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e"}
   ```

2. Poll the status by id, every few minutes or as `Retry-After` suggests,
   until it is terminal. If the job's own time limit passes first, it
   stops; the run continues, and a rerun of the job repeats step 1 with the
   same key and finds it.

   ```http
   GET /openapi/v1/bots/bot_123/evolution/runs/run_7f3?entity_id=user_owner_5
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 200; failed, cancelled, or budget_exhausted would end the job here with nothing to approve
   {"code": 200000, "message": "OK", "data": {"id": "run_7f3", "status": "completed", "candidate_count": 1},
    "request_id": "a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5"}
   ```

3. List the run's candidates, then read each report.

   ```http
   GET /openapi/v1/bots/bot_123/evolution/runs/run_7f3/candidates?entity_id=user_owner_5
   GET /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…?entity_id=user_owner_5
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // data of the report: the gate wants a human for T2, above the auto-promote ceiling T1
   {"candidate_id": "sha256:c41e…", "verdict": "accept",
    "gate": {"id": "gd_204", "outcome": "needs_review", "risk": {"tier": "T2", "raised_by": []},
             "auto_promote_ceiling": "T1"}}
   ```

4. For a candidate whose gate outcome is `needs_review`, try to approve it
   within the policy the owner gave the pipeline. The approval key is
   derived from the run key and the candidate id, so a rerun replays it.

   ```http
   POST /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…:approve?entity_id=user_owner_5
   Content-Type: application/json
   Idempotency-Key: nightly-bot_123-2026-10-08/approve/sha256:c41e
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   {"reason": "nightly auto-policy"}
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 403, policy family: the owner lets this pipeline approve up to T1 only.
   // 403000 stands in for the policy_denied subcode, which 08-promotion.md sets.
   {"code": 403000, "message": "Policy denied", "data": null,
    "request_id": "d3e4f5a6b7c8d9e0f1a2b3c4d5e6f708"}
   ```

   The pipeline does not retry a policy-family error. It logs the
   candidate id and the `request_id` and leaves the candidate in the review
   queue for a human (§13.3).

### 13.2 Editing the bindings safely

The owner's UI backend raises the budget of `bind_01`, while another writer
changes the bindings at the same time (§6).

1. Read the bindings and keep the `ETag`.

   ```http
   GET /openapi/v1/bots/bot_123/evolution/bindings
   ```

   Response `200` with header `ETag: "bind-v12"`.

2. Send the changed document with `If-Match`.

   ```http
   PUT /openapi/v1/bots/bot_123/evolution/bindings
   Content-Type: application/json
   If-Match: "bind-v12"
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 412: someone else wrote first; nothing was changed
   {"code": 412000, "message": "Precondition failed: the resource changed since it was read",
    "data": null, "request_id": "e4f5a6b7c8d9e0f1a2b3c4d5e6f70819"}
   ```

3. Read again (`ETag: "bind-v13"`), reapply the budget change to the fresh
   copy, and send it with `If-Match: "bind-v13"`. The answer is `200` with
   `ETag: "bind-v14"`. After a few `412`s in a row the UI stops and shows the
   conflict to the user instead of retrying.

### 13.3 Review queue in the UI backend

1. List open review items, one page at a time (§8).

   ```http
   GET /openapi/v1/bots/bot_123/evolution/review-queue?status=open&page=1&page_size=20
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // data
   {"total": 1, "items": [{"candidate_id": "sha256:c41e…", "revision_seq": 42, "risk_tier": "T2",
                           "run_id": "run_7f3", "status": "open"}]}
   ```

2. Open one report (diff, verification, gate decision) when the reviewer
   selects it.

   ```http
   GET /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…
   ```

3. Approve. The UI backend creates the key when the reviewer opens the
   approve dialog and reuses it on every retry, so a double click or a
   network retry records one decision.

   ```http
   POST /openapi/v1/bots/bot_123/evolution/candidates/sha256%3Ac41e…:approve
   Content-Type: application/json
   Idempotency-Key: 4b0f6c1e-9a7d-4f53-8d1e-2c6a9e1b7f02
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   {"reason": "Reviewed diff and verification report; escalation fix is correct."}
   ```

   The answer is the `200` shown in §10.7:
   the review item `approved` and a promotion of `r42` in status `applying`.
   A `409020` means another reviewer decided first; the UI re-reads the
   report and shows that decision.

### 13.4 Going back to an earlier revision

Going back is promoting an earlier revision; there is no separate rollback
call.

1. Read the refs to find the revision to go back to and the one `active`
   points at now.

   ```http
   GET /openapi/v1/bots/bot_123/genome/refs
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // data, shortened: active is r42, previous is r41
   {"refs": [{"name": "active", "revision": "sha256:7c1e…"}, {"name": "previous", "revision": "sha256:a90b…"}]}
   ```

2. Promote `r41`, with compare-and-swap on `active`.

   ```http
   POST /openapi/v1/bots/bot_123/genome/promotions
   Content-Type: application/json
   Idempotency-Key: goback-bot_123-r42-to-r41
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   {"revision": "sha256:a90b…", "ref": "active", "expected_revision": "sha256:7c1e…",
    "reason": "r42 raised refund escalations"}
   ```

   ```jsonc
   // Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
   // 201, data shortened
   {"id": "prm_6a0", "ref": "active", "revision_id": "sha256:a90b…", "revision_seq": 41,
    "from_revision_id": "sha256:7c1e…", "going_back": true, "status": "applying"}
   ```

   A `409010` means `active` moved since step 1: read the refs again and
   decide. For a service bot the revision is published as the next version
   through the existing publish flow ([08-promotion.md](08-promotion.md)).

## 14. Interactions

| Other part | Direction | What flows |
| --- | --- | --- |
| [01-genome.md](01-genome.md) (Genome Registry, Backend) | API → Genome | Revision, ref, diff, and content calls; CAS on refs; content-idempotent revision creates |
| [02-experience.md](02-experience.md) | API → Experience | Episode queries, feedback records |
| [03-strategy.md](03-strategy.md) (Strategy Registry) | API → Registry | Strategy registration (`POST /evolution/strategies`), listing, capability catalog; the Strategy SDK for strategy authors is owned there |
| [05-experiment-ledger.md](05-experiment-ledger.md) | API → Ledger | Entry queries, exports as operations |
| [06-evolution-run.md](06-evolution-run.md) | API → Run | Bindings (ETag), run submission (idempotency key → run id), status by id, cancellation; budgets, kill switches, and binding checks produce the policy and budget error families; the internal Job Protocol uses the same conventions |
| [07-verification.md](07-verification.md) | API → Verification | Suites, ad-hoc evaluations as operations |
| [08-promotion.md](08-promotion.md) | API → Promotion | Candidate reports, review queue, approve/reject, promotions (including going back) |
| Pipelines and CI | Caller → API | HTTP; deterministic idempotency keys; error codes |
| UI backend | Caller → API | HTTP; ETags on bindings edits; one key per user action |
| Human operators | Caller → API | The workbench UI, through the UI backend |
| Gateway (`apps/gateway`) | Routes API → Backend, `apps/evolution` | Domain routing per path, the published OpenAPI documents, the compatibility gate ([§11](#11-openapi-documents-and-gateway-routing)) |
| Backend OpenAPI v1 adapter | Reused by this layer | `Envelope`, `ErrorEnvelope`, `Page`, `PageParams`, envelope builders and error mapping |

## 15. Open decisions

| ID | Question | Current position |
| --- | --- | --- |
| A-2 | How long are idempotency records kept? | Proposed: at least 30 days, longer than any caller's retry horizon (a nightly job retried the next day must still re-attach). Decide with RSI-07 |
| A-3 | Cursor pagination for large append-only collections (episodes, ledger entries)? | Proposed: keep page numbers for v1 consistency; add an opaque `cursor` for these two collections if `total` or shifting pages become a problem |
| A-4 | Structured error details (for example which capability has no provider in a binding check)? | OpenAPI v1 pins `data` to `null` on errors and uses fixed messages. Options: keep that and encode the detail in the subcode; or allow a `data.details` object for evolution errors only. Undecided |
| A-5 | A uniform validate-only mode (`dry_run`) for writes? | A dry run of `POST /bots/{bot_id}/genome/revisions` (check a patch against its base without recording a revision, [01-genome.md](01-genome.md)) needs one; whether other endpoints get it is up to their owning docs |
| A-6 | One merged OpenAPI document or one per service? | Resolved: one document per serving service, no merged document ([§11.2](#112-one-document-per-serving-service)) |
| A-8 | Event delivery (Q3 in [work-items.md](work-items.md); Q1 there, the CLI binary, is moot now that the CLI is out of scope) | Current design has no callback or webhook channel: status by id is the only mechanism. A push channel would be an addition, not a replacement |

Q2 in [work-items.md](work-items.md) (whether subject bots may request runs
by default) belongs to the postponed bot callers ([§3](#3-callers)).
