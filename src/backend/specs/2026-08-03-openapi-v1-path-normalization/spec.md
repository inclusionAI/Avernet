# Public API — `/openapi/v1/bots` Path Normalization

## Summary

The public `/openapi/v1/bots` surface grew one component at a time, and the
components disagree about where a path says *which component* and where it says
*which bot*. Three shapes are in use today, and only one of them is the one the
surface was designed around. This feature makes the whole surface obey a single
addressing rule, removes the `channels` component that was never implemented,
and republishes the gateway's pinned description so the served document
describes what the backend actually answers.

No handler behaviour, schema, status code, authorization rule or tenant-scoping
rule changes. This is an addressing change and nothing else.

## Motivation

The surface has one intended shape: `/openapi/v1/bots` is the base, and each
component appends its own name — `/openapi/v1/bots/identity`,
`/openapi/v1/bots/resources`, `/openapi/v1/bots/routines`. Under a component,
the bot is an ordinary path parameter.

Three components do not follow it:

- **`identity` carries a redundant `/bot/` segment.** `GET
  /openapi/v1/bots/identity/bot/{bot_id}` says "bot" twice — once in the base,
  once again before the id — and no other component does. A reader has no way
  to know from the rest of the surface that this one needs it.

- **Five components put `{bot_id}` *before* their own name.**
  `/openapi/v1/bots/{bot_id}/connection`, `/{bot_id}/engine/…`,
  `/{bot_id}/sessions/…`, `/{bot_id}/models/…`, `/{bot_id}/approvals/…` and
  `/{bot_id}/skills` are all addressed as if they were sub-resources of the
  *bots* component. They are not: they are separate components that happen to
  be bot-scoped, owned by different modules, documented separately, and mounted
  with a different error contract (`ENGINE_RUNTIME_ERROR_RESPONSES`).

- **`channels` is published but not implemented.** Six operations across two
  paths sit in the description with `NotImplementedError` handlers. They are
  part of the artifact the gateway serves, so an integrator reading the served
  document sees a channels API and gets a 500 when they call it.

Why this matters beyond tidiness:

1. **The wildcard eats the namespace.** `/openapi/v1/bots/{bot_id}` matches any
   single segment, so every component name is only reachable because the
   component routers are mounted *before* the bots router. Every component whose
   name sits behind `{bot_id}` deepens that dependence: the router file no longer
   states its own address, and a reader of `engine_runtime/sessions/router.py`
   cannot tell whether `/openapi/v1/bots/{bot_id}/sessions` is served by that
   file or by a `{bot_id}`-shaped route in the bots component.

2. **It blocks a second owner under the same base.** BCS reached the same
   problem from the other side and resolved it the same way: its bot
   control-plane operations moved from `/openapi/v1/bots/{bot_id}` to
   `/openapi/v1/bots/collaboration/{bot_id}` precisely because a bare
   `{bot_id}` under the shared base "collides with the Gateway's existing
   Backend-owned Bot surface and … make[s] runtime ownership ambiguous"
   (`src/bcs/docs/plans/2026-08-03-bcn-collaboration-paths-design.md`). The
   backend is the other half of that collision, and it has not moved.

3. **The published artifact is stale.** The gateway's pinned
   `configs/schemas/bots.openapi.json` carries 32 paths; the backend publishes
   43. Every engine-runtime path shipped in Track C (PR #630) is missing from
   the document the gateway serves. Whatever the addressing decision, the
   artifact has to be regenerated, and it is cheaper to do it once.

## User Stories

- As an integrator reading the served document, I want every path to name its
  component in the same position, so that I can predict an endpoint's address
  from the component it belongs to rather than looking each one up.
- As an integrator, I want the served document to describe only operations that
  are actually answered, so that I do not build against a channels API that
  returns 500.
- As a backend engineer adding a component, I want its router file to state its
  full address, so that I can read one file and know what it serves.
- As a gateway operator, I want the pinned description to match the backend's
  current surface, so that the served document is not missing whole components.

## Requirements

### The addressing rule

Every operation on this surface is addressed as:

```text
/openapi/v1/bots/<component>/…
```

- `<component>` is a literal segment naming the owning component, and it comes
  **before** any path parameter.
- When an operation is bot-scoped, `{bot_id}` is the **first segment after the
  component**, and no segment sits between the component and `{bot_id}`.
- The `bots` component itself owns the base — `/openapi/v1/bots` and
  `/openapi/v1/bots/{bot_id}` — because it *is* the component the base names.
  Its own sub-resources (`/status`, `/passport`, `/restart`, `/auth-status`,
  `/engine-config`) stay beneath `/openapi/v1/bots/{bot_id}`; they belong to the
  bot record, not to another component.

### Path changes

**`identity` — drop the redundant `/bot/` segment.**

| Today | After |
| --- | --- |
| `GET /openapi/v1/bots/identity/bot/{bot_id}` | `GET /openapi/v1/bots/identity/{bot_id}` |
| `GET /openapi/v1/bots/identity/bot/{bot_id}/{file_type}` | `GET /openapi/v1/bots/identity/{bot_id}/{file_type}` |
| `PUT /openapi/v1/bots/identity/bot/{bot_id}/{file_type}` | `PUT /openapi/v1/bots/identity/{bot_id}/{file_type}` |

**`connection`, `engine`, `approvals`, `sessions`, `models` — component before
`{bot_id}`.**

| Today | After |
| --- | --- |
| `GET /openapi/v1/bots/{bot_id}/connection` | `GET /openapi/v1/bots/connection/{bot_id}` |
| `GET /openapi/v1/bots/{bot_id}/engine/status` | `GET /openapi/v1/bots/engine/{bot_id}/status` |
| `GET /openapi/v1/bots/{bot_id}/engine/capabilities` | `GET /openapi/v1/bots/engine/{bot_id}/capabilities` |
| `GET /openapi/v1/bots/{bot_id}/engine/available` | `GET /openapi/v1/bots/engine/{bot_id}/available` |
| `GET|PUT /openapi/v1/bots/{bot_id}/approvals/mode` | `GET|PUT /openapi/v1/bots/approvals/{bot_id}/mode` |
| `GET /openapi/v1/bots/{bot_id}/approvals/modes` | `GET /openapi/v1/bots/approvals/{bot_id}/modes` |
| `GET|POST /openapi/v1/bots/{bot_id}/sessions` | `GET|POST /openapi/v1/bots/sessions/{bot_id}` |
| `GET|PATCH|DELETE /openapi/v1/bots/{bot_id}/sessions/{session_id}` | `GET|PATCH|DELETE /openapi/v1/bots/sessions/{bot_id}/{session_id}` |
| `GET|DELETE /openapi/v1/bots/{bot_id}/sessions/{session_id}/messages` | `GET|DELETE /openapi/v1/bots/sessions/{bot_id}/{session_id}/messages` |
| `GET /openapi/v1/bots/{bot_id}/models` | `GET /openapi/v1/bots/models/{bot_id}` |
| `GET /openapi/v1/bots/{bot_id}/models/{model_id}` | `GET /openapi/v1/bots/models/{bot_id}/{model_id}` |

**`skills` — component before `{bot_id}`, and the catalog gets a literal.**

The skills component owns two resource families: a global catalog (not
bot-scoped) and a bot's installed skills (bot-scoped). Under the rule, the
bot-scoped family takes `/openapi/v1/bots/skills/{bot_id}`, which is
indistinguishable from a catalog detail at `/openapi/v1/bots/skills/{skill_id}`
— same shape, same position, different resource. The catalog therefore takes a
literal `catalog` segment, the same device the surface already uses for
`check-name` and `ceiling`.

| Today | After |
| --- | --- |
| `GET /openapi/v1/bots/skills` | `GET /openapi/v1/bots/skills/catalog` |
| `GET /openapi/v1/bots/skills/{skill_id}` | `GET /openapi/v1/bots/skills/catalog/{skill_id}` |
| `GET /openapi/v1/bots/{bot_id}/skills` | `GET /openapi/v1/bots/skills/{bot_id}` |
| `POST /openapi/v1/bots/{bot_id}/skills` | `POST /openapi/v1/bots/skills/{bot_id}` |
| `DELETE /openapi/v1/bots/{bot_id}/skills/{skill_id}` | `DELETE /openapi/v1/bots/skills/{bot_id}/{skill_id}` |

**Unchanged:** `bots`, `mcp`, `resources`, `routines` already obey the rule.

### `channels` is removed

The component's router, schemas, package and its entry in the mounted list are
deleted, along with its two paths and six operations. Nothing else references
it. It is removed rather than left as a stub because it is *published*: an
unimplemented component that no caller can distinguish from an implemented one
is worse than an absent one.

### Reserved component names

Because the `bots` component keeps the bare `/openapi/v1/bots/{bot_id}`, a bot
whose id equals a component name would be unreachable at that address. The set
of reserved names is fixed by this spec and must be stated in the docs:
`approvals`, `ceiling`, `check-name`, `connection`, `engine`, `identity`, `mcp`,
`models`, `resources`, `routines`, `sessions`, `skills`. This constraint exists
today for the four literals already present; the change widens the set, so it
becomes a documented property rather than an accident of which components
happened to be mounted.

### Published description

The gateway's pinned artifact is regenerated from the backend's dump so the
served document matches the surface. The compatibility gate will report this as
breaking — every renamed path is a removal — and that is correct and intended;
the publish is a coordinated one.

## Non-Goals

- No handler, schema, service, repository or tenant-scoping change.
- **No compatibility aliases at the old paths.** The surface is not reachable by
  an external tenant yet (`route_security` still requires a Google-resolved
  `user` identity, which a tenant presenting an access key cannot satisfy), so
  there is no active integrator contract to preserve, and duplicate addresses
  would re-create the ambiguity this change removes. Same reasoning BCS applied
  to the same migration.
- No change to the internal `/api/...` surface.
- No change to gateway routing logic. The gateway resolves by the leading
  segment after the version base (`bots`), which every path here still carries.
- No new component and no new operation.

## Open Questions

None blocking. One decision was made without an explicit ruling and is recorded
here so it can be reversed cheaply if wrong: the skills catalog takes a literal
`catalog` segment. The alternatives were leaving the catalog at the bare
`/openapi/v1/bots/skills` (which would make skills the one component whose
`{bot_id}` is not first) or splitting the catalog into a `skill-catalog`
component (which invents a component name the module layout does not have).

## Success Criteria

- Every published path matches `/openapi/v1/bots/<literal-component>/…`, and no
  published path has a parameter before its component's literal segment.
- No published path contains the segment `bot` between `bots` and `{bot_id}`.
- `channels` appears in no route, no module, and no published path.
- The published description carries 41 paths and no `NotImplementedError`
  handler is reachable from it except the skills stubs that were already stubs.
- Existing behavioural tests pass unchanged apart from the addresses they call.
- `src/gateway/configs/schemas/bots.openapi.json` is byte-identical to a fresh
  `scripts/dump_openapi.py` run.
