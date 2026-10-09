# Public API — `/openapi/v1/bots` Bot-First Addressing

## Summary

Every operation on the public `/openapi/v1/bots` surface that acts on **one bot**
is addressed `/openapi/v1/bots/{bot_id}/…`, with the bot id ahead of anything
that says which part of the bot. Thirty-nine of the surface's seventy-one
operations get a new address under that rule; twenty of them stop asking a
client to name the bot in the query string or the request body.

Every legacy address keeps answering, with its exact current contract, from a
compatibility package. Nothing is removed in this feature — removal is a later,
per-address decision driven by traffic.

No handler behaviour, response schema, status code, authorization rule or
tenant-scoping rule changes. This is an addressing change, plus three small
contract fixes named below that are cheap to make while callers are migrating
and expensive to make afterwards.

## Motivation

The surface has three addressing shapes and no rule that picks between them:

| Shape | Operations |
| --- | --- |
| `/bots/{bot_id}/<thing>` — the bot first | 15 |
| `/bots/<component>/{bot_id}` — the component first | 19 |
| bot named in the query string or the body | 20 |

The remaining 17 address no single bot and are not part of this.

So the first thing to be clear about: this is a decision, not a cleanup. There
is no established convention here to restore. The `2026-08-03` normalization
converted five components *to* component-first and left three groups untouched,
and the bots component's own sub-resources have always been bot-first.

Three things are wrong today, in descending order of cost.

**1. Twenty operations make a client name the bot in the wrong place.** Seven
resources operations, six routines operations and two skills operations take a
required `bot_id` **query parameter**; `POST /openapi/v1/bots/routines` takes it
as a **body field**. Their component roots (`/bots/resources`, `/bots/routines`,
`/bots/skills`) have no bot in the path, so there is nowhere else for it to go —
the address does not name what the operation acts on, and the parameter list has
to make up the difference.

**2. Component-first spends the bot-id namespace, and keeps spending it.**
Because the bots component owns the bare `/openapi/v1/bots/{bot_id}`, every
literal in that segment is a bot id that can never be addressed. Component-first
puts every component name there. The reserved list is fifteen names today and
grows by one with every component shipped — Track C added five at once.

**3. Seven operations hide their bot from the authorization seam.**
`require_granted_bot` reads the addressed bot off the path or the query string.
Seven operations put it somewhere it cannot see — one in the request body, four
naming only a skill, two naming the owner under a parameter the shared
dependency does not know — so for those the grant check moves into the handler.
`principal.py` carries a `TODO(#960)` on exactly this:

> those seven are an inconsistency — two mechanisms doing one job — and the
> exception has already cost one real defect, since a handler-side check is a
> place the check and the resolution can drift apart. #960 tracks removing it:
> make the address uniform on the skills group, move the body-carried `bot_id`,
> or resolve the skill before the check.

This feature does the first two. It does **not** do the third, so three of the
seven deferrals collapse and four remain — see the section below for which and
why. **This is still the reason to do the work now rather than later**: the addressing change is what makes the
authorization seam uniform, and every release the two mechanisms coexist is
another release in which they can drift.

**4. The published surface tells a client about our module boundaries.**
`/bots/sessions/{bot_id}` versus `/bots/{bot_id}/status` differ because one is
served by `engine_runtime/sessions/` with `ENGINE_RUNTIME_ERROR_RESPONSES` and
the other by `bots/` with the ordinary table. That distinction is real for us and
meaningless to a caller, for whom both are simply parts of one bot.

### Why bot-first rather than finishing component-first

Both shapes fix problem 1. The reasons the `2026-08-03` spec gives for
component-first were re-examined and two of the three do not hold:

- *"A router file can state its own address."* FastAPI accepts a path parameter
  in a router prefix — `APIRouter(prefix="/openapi/v1/bots/{bot_id}/sessions")`
  is a valid, self-describing declaration (verified against the installed
  version). The original complaint was about routes declared inside a
  bots-shaped router, which is a code-organization fact, not a property of the
  address.

- *"The wildcard eats the namespace."* This argues the other way. Component-first
  is what puts component names in the same segment a bot id is read from; that is
  the mechanism that produced the fifteen-name reserved list.

- *"It blocks a second owner under the same base."* The cited document
  (`src/bcs/docs/plans/2026-08-03-bcn-collaboration-paths-design.md`) carries
  **Superseded** in its own header. The residue of this reason is real but
  narrow, and is recorded under Non-Goals below.

What bot-first adds beyond parity: the reserved list drops from fifteen to seven
and stops growing, a client gets one base URL per bot, and the fifteen operations
that are already bot-first stop being the odd ones out.

## User Stories

- As an integrator, I want every operation on one bot to start with that bot's
  address, so that I can build one base URL per bot instead of learning where
  each component wants the id.
- As an integrator already in production, I want my current calls to keep
  working unchanged while I migrate, and I want the document to tell me which
  addresses are going away.
- As an operator, I want to know when nobody is calling a legacy address any
  more, so that removing it is a fact rather than a guess.
- As a backend engineer, I want a bot id that happens to spell `sessions` to be
  addressable, and I want adding a component not to cost another reserved word.

## Requirements

### The addressing rule

An operation that acts on **one bot** is addressed:

```text
/openapi/v1/bots/{bot_id}/<thing>/…
```

`{bot_id}` is the first segment after the base. `<thing>` is a literal naming
what about the bot is being addressed — and it is one flat namespace, whether the
operation is served by the bots component or by another module.

An operation that names **no single bot** — a listing, an account-level read, a
query that may span bots — keeps a literal in the `{bot_id}` segment and takes no
bot path parameter.

### Where a bot id may be carried

| Location | When |
| --- | --- |
| **path** | The operation addresses that bot. Under the rule above, that is every bot-scoped operation. |
| **query** | It is a *filter* over a result set that could legitimately span bots. |
| **body** | Never. A body describes the resource being sent; the bot is not part of any resource on this surface. |

The query row is what makes the Bot Logs group coherent rather than an
exception: `GET /openapi/v1/bots/logs/traces` takes `bot_id` and `user_id` as
joint filters over a tenant-level trace query. It does not address a bot, so it
keeps both in the query and keeps its address.

### Path changes — component and bot swap places (19 operations)

| Method | Today | After |
| --- | --- | --- |
| GET, PUT | `/openapi/v1/bots/approvals/{bot_id}/mode` | `/openapi/v1/bots/{bot_id}/approvals/mode` |
| GET | `/openapi/v1/bots/approvals/{bot_id}/modes` | `/openapi/v1/bots/{bot_id}/approvals/modes` |
| GET | `/openapi/v1/bots/connection/{bot_id}` | `/openapi/v1/bots/{bot_id}/connection` |
| GET | `/openapi/v1/bots/engine/{bot_id}/available` | `/openapi/v1/bots/{bot_id}/engine/available` |
| GET | `/openapi/v1/bots/engine/{bot_id}/capabilities` | `/openapi/v1/bots/{bot_id}/engine/capabilities` |
| GET | `/openapi/v1/bots/engine/{bot_id}/status` | `/openapi/v1/bots/{bot_id}/engine/status` |
| GET | `/openapi/v1/bots/identity/{bot_id}` | `/openapi/v1/bots/{bot_id}/identity` |
| GET, PUT | `/openapi/v1/bots/identity/{bot_id}/{file_type}` | `/openapi/v1/bots/{bot_id}/identity/{file_type}` |
| GET | `/openapi/v1/bots/models/{bot_id}` | `/openapi/v1/bots/{bot_id}/models` |
| GET | `/openapi/v1/bots/models/{bot_id}/{model_id}` | `/openapi/v1/bots/{bot_id}/models/{model_id}` |
| GET, POST | `/openapi/v1/bots/sessions/{bot_id}` | `/openapi/v1/bots/{bot_id}/sessions` |
| GET, PATCH, DELETE | `/openapi/v1/bots/sessions/{bot_id}/{session_id}` | `/openapi/v1/bots/{bot_id}/sessions/{session_id}` |
| GET, DELETE | `/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages` | `/openapi/v1/bots/{bot_id}/sessions/{session_id}/messages` |

### Path changes — the bot becomes the address (20 operations)

Every row loses its required `bot_id` query parameter. `POST …/routines` loses
`bot_id` from its request body instead.

| Method | Today | After |
| --- | --- | --- |
| GET, DELETE | `/openapi/v1/bots/resources` | `/openapi/v1/bots/{bot_id}/resources` |
| GET | `/openapi/v1/bots/resources/download` | `/openapi/v1/bots/{bot_id}/resources/download` |
| GET | `/openapi/v1/bots/resources/preview` | `/openapi/v1/bots/{bot_id}/resources/preview` |
| GET | `/openapi/v1/bots/resources/stat` | `/openapi/v1/bots/{bot_id}/resources/stat` |
| POST | `/openapi/v1/bots/resources/mkdir` | `/openapi/v1/bots/{bot_id}/resources/mkdir` |
| POST | `/openapi/v1/bots/resources/upload` | `/openapi/v1/bots/{bot_id}/resources/upload` |
| GET | `/openapi/v1/bots/routines` | `/openapi/v1/bots/{bot_id}/routines` |
| POST | `/openapi/v1/bots/routines` | `/openapi/v1/bots/{bot_id}/routines` |
| GET, PATCH, DELETE | `/openapi/v1/bots/routines/{routine_id}` | `/openapi/v1/bots/{bot_id}/routines/{routine_id}` |
| POST | `/openapi/v1/bots/routines/{routine_id}/run` | `/openapi/v1/bots/{bot_id}/routines/{routine_id}/run` |
| GET | `/openapi/v1/bots/routines/{routine_id}/runs` | `/openapi/v1/bots/{bot_id}/routines/{routine_id}/runs` |
| GET | `/openapi/v1/bots/skills` | `/openapi/v1/bots/{bot_id}/skills` |
| POST | `/openapi/v1/bots/skills/upload` | `/openapi/v1/bots/{bot_id}/skills` |
| GET, DELETE | `/openapi/v1/bots/skills/{skill_id}` | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` |
| POST | `/openapi/v1/bots/skills/{skill_id}/activate` | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate` |
| POST | `/openapi/v1/bots/skills/{skill_id}/deactivate` | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate` |

Two of those rows are choices rather than translations:

**Skill upload becomes a POST to the collection.** A literal `upload` would sit
in the same segment as `{skill_id}` and reserve it as an unusable skill id.
POST-to-the-collection is what the operation means anyway. Its upsert semantics
are unchanged: 201 with `operation: "created"`, or 200 with
`operation: "updated"` when the bot already has a skill of that name.

**The four `{skill_id}` operations gain a `{bot_id}` segment they do not
strictly need**, since a skill id already resolves its bot and owner. It is worth
paying: the address says what it is, and the grant check becomes the same on all
six skills operations instead of a separate mode.

### Operations that keep their address (32)

Not exceptions — none of them addresses a single bot, or they already do.

- **The bots component's twenty.** `/openapi/v1/bots`, `/openapi/v1/bots/{bot_id}`,
  the sub-resources beneath it, and the three account-level reads `/check-name`,
  `/ceiling`, `/authorized`. Already bot-first; this feature generalizes their
  shape rather than changing it.
- **Bot Logs — five.** A trace query keyed by dimension. Its `bot_id` is a filter
  (see the location table above); four of the five have no bot dimension at all.
- **MCP — six.** Account-level configuration and a tenant-wide catalogue.
- **Load test — one.** Reads and writes nothing.

### Reserved names

The fenced list in `docs/openapi-v1/README.md` shrinks to the literals that still
occupy the segment a bot id is read from:

```text
authorized  ceiling  check-name  loadtest  logs  mcp
```

`messages` stays on the second, unrouted list. The nine names freed —
`approvals`, `connection`, `engine`, `identity`, `models`, `resources`,
`routines`, `sessions`, `skills` — become addressable bot ids, and a component
added later costs no reserved word.

A **second** list is introduced: the literals occupying the segment after
`{bot_id}`. These reserve nothing (that segment holds no wildcard) but they must
stay unique, so a component added later cannot collide with a bots sub-resource.

### Legacy addresses

Every address in the two tables above keeps answering, **with its current
contract byte for byte** — the same parameter names in the same locations, the
same request and response schemas, the same status codes. A legacy address is
not a thin alias for the new one; it owns the old contract and translates.

- Legacy operations are marked `deprecated: true` in the published description.
- Legacy responses carry `Deprecation: true` and a `Sunset` date
  (RFC 9745, RFC 8594).
- No redirects. A 3xx cannot move a value from the query string into a path
  segment, and clients drop bodies across them.
- Nothing is removed in this feature.

### Contract fixes folded in

Three fixes that are cheap while callers are migrating and expensive afterwards.
Each is independent of the addressing change and may be dropped without
affecting it. On the legacy addresses, the old spelling is preserved.

**`engine-config` joins the engine component.**
`/openapi/v1/bots/{bot_id}/engine-config` becomes
`/openapi/v1/bots/{bot_id}/engine/config`, beside `/engine/status`,
`/engine/available` and `/engine/capabilities`. It keeps the ordinary error
table — an engine-config read cannot produce the 501 or 504 the engine-runtime
groups document — so it is served by its own router mount at that path.

**`session_key` moves out of the approvals write body.** It is a query parameter
on `GET …/approvals/mode` and a body field on `PUT` of the same address. It
becomes a query parameter on both; the `PUT` body carries `mode` alone.

**`owner_entity_id` is renamed `owner_id`.** Same description, same semantics as
the `owner_id` on nineteen other operations; only the two skills operations
spell it differently. This one is **not** optional in the way the other two are:
it is what lets `_addressed_owner` read the owner those operations act on, which
is the last of the seven handler-side grant checks (below).

### One authorization seam — narrowing `TODO(#960)` from seven to four

> **Amended during implementation.** This section originally said all seven
> deferrals collapse and `SKILL_SCOPED_OPERATIONS` is deleted. That was wrong,
> and shipping it as written would have been a security regression: the first
> attempt mounted all six skills operations under the shared dependency and
> refused an application holding a *valid* grant on a shared bot. Four
> operations genuinely cannot be checked by the shared dependency, and this
> section now says which and why — so that a later change does not delete their
> handler-side checks believing the dependency covers them. **It does not.**

With the addressing change in place, most grant-checked operations carry their
bot where `require_granted_bot` can read it, and those that name an owner spell
it `owner_id`. So:

- `_defers_to_its_handler` is deleted, along with `BODY_BOT_ID_OPERATIONS` and
  `OWNER_ADDRESSED_OPERATIONS` in `admission.py`.
- The two skills *collection* operations move to `GRANT_CHECKED_ADDRESSED_BOT`,
  which is what they have always meant, and their handler-side
  `caller.require_bot(...)` is removed — the dependency now binds the same
  `(bot, owner)` they act on.
- The routines create takes its bot on the path, so its handler-side check goes
  too.

**`SKILL_SCOPED_OPERATIONS` stays, holding the four `{skill_id}` operations.**
They resolve by `(skill, actor)`, so the addressed bot's *owner* arrives on the
record rather than on the wire — a collaborator reaches a skill on someone
else's bot routinely, and the owner cannot be defaulted to the caller. There is
nothing for the shared dependency to look a grant up against until the record
has been read, so the check belongs after that read or nowhere. They are mounted
without the group-level dependency rather than exempted from one, and
`test_admission_inventory.py` asserts the set is exactly those four.

Closing the remaining four needs a different change — either a skills read keyed
by `(bot, owner, skill)` rather than `(skill, actor)`, or a way for the
dependency to defer to a *record read* rather than to a handler. Neither belongs
in an addressing refactor.

The property the deferral list existed to preserve survives verbatim: an
operation whose bot cannot be seen is **refused**, not waved through. The
refusal branch in `require_granted_bot` stays as the backstop, and
`test_self_checked_routes_refuse.py` drives every self-checking operation with
an ungranted application to prove the handler-side check is really there.

## Non-Goals

- **Removing any legacy address.** Removal is a later, per-address decision made
  when the access log shows no traffic. This feature ships the deprecation, the
  headers and the counting, not the deletion.
- **Changing handler behaviour, schemas, status codes, authorization, or tenant
  scoping.** A request to a new address and the same request to its legacy
  address must produce identical results.
- **Re-addressing Bot Logs, MCP or the load-test group.**
- **Whether MCP belongs under `/openapi/v1/bots` at all.** It is account-level
  and arguably misfiled, but that is a different change.
- **Relaxing the gateway's domain-pattern grammar.** The gateway refuses a
  `match` pattern containing a parameter, so under this rule a *bot-scoped*
  component can no longer be split onto its own upstream with a config line. The
  capability has never been used at that level; the two socket domains already
  mounted beneath `/openapi/v1/bots/` (`bots-messages-ws`, `bots-loadtest-ws`)
  are unaffected because their literals are not bot-scoped. Confirmed with the
  owner that the engine-runtime groups will not move to the engine proxy within
  the year; if that changes, the escape hatch is a top-level domain of their own,
  as BCN took.

## Open Questions

None blocking. Two recorded for the reviewer:

1. **Sunset date.** The `Sunset` header needs a concrete date. Proposed: twelve
   months from the release that ships this. It is a published promise, so it
   wants a decision rather than a default.
2. **Whether to ship the three contract fixes in this feature or immediately
   after.** They are separable by design. Shipping them together means one
   migration for a caller instead of two; shipping them after keeps this
   feature purely about addresses.

## Success Criteria

- Every bot-scoped operation on `/openapi/v1/bots` is addressed
  `/openapi/v1/bots/{bot_id}/…`; a test asserts it against the generated
  document, not against a hand-maintained list.
- No operation on the surface carries `bot_id` in the request body, and the only
  one carrying it in the query is `GET /openapi/v1/bots/logs/traces`, where it is
  a filter.
- Every legacy address answers exactly as it does today, verified by a test that
  drives the same request through both addresses and compares the responses.
- Every legacy operation is `deprecated: true` in the published description and
  answers with `Deprecation` and `Sunset` headers.
- The reserved-name list in `docs/openapi-v1/README.md` is **fifteen** routed
  names while the retiring addresses answer, and the convention test still
  derives it from the routes. Six is the *post-removal* figure: nine of the
  fifteen are held only by the component-first addresses, and the list drops to
  six the day those are deleted. Stated both ways deliberately — a criterion
  demanding six today would be unmeetable while the compatibility window is
  open, and the way to "meet" it would be to weaken the test or the docs.
- `admission.py` has an entry for every new address, and
  `BODY_BOT_ID_OPERATIONS` and `OWNER_ADDRESSED_OPERATIONS` are gone.
  `SKILL_SCOPED_OPERATIONS` remains, holding exactly the four `{skill_id}`
  operations and no more.
- `_defers_to_its_handler` is deleted and `TODO(#960)` is narrowed from seven
  operations to four. `test_admission_inventory.py` asserts that the set of
  self-checking operations is exactly those four, and
  `test_self_checked_routes_refuse.py` asserts each of them actually refuses an
  ungranted application — the structural check cannot see inside a handler, so
  something has to.
- `src/gateway/configs/schemas/bots.openapi.json` is regenerated and matches what
  the backend publishes.
