# Plan — `/openapi/v1/bots` Path Normalization

Implements [`spec.md`](./spec.md).

## Shape of the change

Every path change in the spec is expressible as a **router prefix change plus a
route-decorator path change**, because FastAPI composes the two. Nothing about
handler bodies, dependencies, schemas or the `Injected(...)` wiring moves. The
mechanical rule per component:

| Component | Router prefix today | Router prefix after | Route paths |
| --- | --- | --- | --- |
| `identity` | `/openapi/v1/bots/identity` | unchanged | `/bot/{bot_id}…` → `/{bot_id}…` |
| `skills` | `/openapi/v1/bots` | `/openapi/v1/bots/skills` | rewritten (see below) |
| `connection` | `/openapi/v1/bots/{bot_id}` | `/openapi/v1/bots/connection` | `/connection` → `/{bot_id}` |
| `engine` | `/openapi/v1/bots/{bot_id}/engine` | `/openapi/v1/bots/engine` | `/x` → `/{bot_id}/x` |
| `approvals` | `/openapi/v1/bots/{bot_id}/approvals` | `/openapi/v1/bots/approvals` | `/x` → `/{bot_id}/x` |
| `sessions` | `/openapi/v1/bots/{bot_id}/sessions` | `/openapi/v1/bots/sessions` | `""` → `/{bot_id}`, `/{session_id}…` → `/{bot_id}/{session_id}…` |
| `models` | `/openapi/v1/bots/{bot_id}/models` | `/openapi/v1/bots/models` | `""` → `/{bot_id}`, `/{model_id:path}` → `/{bot_id}/{model_id:path}` |
| `bots`, `mcp`, `resources`, `routines` | — | unchanged | unchanged |
| `channels` | `/openapi/v1/bots/channels` | **deleted** | **deleted** |

`bot_id` is already a declared handler parameter on every affected route (it has
to be — the handlers resolve the bot through it), so moving where the segment
appears in the path template changes no signature.

### Skills, concretely

`skills/router.py` today carries the prefix `/openapi/v1/bots` and spells the
component in each route path, which is why it is the only component whose file
does not state its own address. After:

```python
router = APIRouter(prefix="/openapi/v1/bots/skills", tags=["skills"])

@router.get("/catalog")                       # list catalog
@router.get("/catalog/{skill_id}")            # catalog detail
@router.get("/{bot_id}")                      # a bot's installed skills
@router.post("/{bot_id}")                     # install
@router.delete("/{bot_id}/{skill_id}")        # uninstall
```

**Declaration order inside the file is load-bearing** and must stay as written:
`/catalog` and `/catalog/{skill_id}` are literals competing with `/{bot_id}` and
`/{bot_id}/{skill_id}` at the same depth, and FastAPI resolves first-registered.
This is the same ordering contract `openapi_v1/__init__.py` already documents at
the mount level; the comment in the file must say so.

## Files

### Backend — routers

- `openapi_v1/identity/router.py` — three decorator paths, module docstring.
- `openapi_v1/skills/router.py` — prefix, five decorator paths, module docstring
  (which currently claims the catalog lives at `/openapi/v1/skills`, an address
  that has never existed), plus the ordering comment.
- `openapi_v1/engine_runtime/connection/router.py` — prefix + one decorator
  path, module docstring.
- `openapi_v1/engine_runtime/engine/router.py` — prefix + three decorator paths,
  module docstring (also references `PUT /openapi/v1/bots/{bot_id}` and `POST
  /openapi/v1/bots/{bot_id}/restart`, which are **bots**-component paths and
  stay as written).
- `openapi_v1/engine_runtime/approvals/router.py` — prefix + three decorator
  paths, module docstring.
- `openapi_v1/engine_runtime/sessions/router.py` — prefix + six decorator paths,
  module docstring.
- `openapi_v1/engine_runtime/models/router.py` — prefix + two decorator paths,
  module docstring.
- `openapi_v1/engine_runtime/__init__.py` — the docstring's "all mounted under
  `/openapi/v1/bots/{bot_id}/…`" is now false for all five.

### Backend — mounting

- `openapi_v1/__init__.py` — drop the `channels` import and its `_SUBGROUPS`
  entry; rewrite the module docstring and the `_SUBGROUPS` / `_ENGINE_RUNTIME_GROUPS`
  ordering comments.

  The ordering constraint **narrows but does not disappear**. After the change
  the only single-segment literals under `/openapi/v1/bots` are `resources`,
  `routines`, `check-name` and `ceiling`; the rest of the components are only
  reachable at two segments or more. `/openapi/v1/bots/{bot_id}` still matches
  any single segment, so the sub-group routers must still be mounted before the
  bots router or `GET /openapi/v1/bots/resources` resolves to "get the bot named
  `resources`". The engine-runtime groups' freedom relative to each other is
  also preserved — they are now literal-prefixed, so they cannot shadow one
  another at all.

- `openapi_v1/channels/` — delete the package (`__init__.py`, `router.py`,
  `schemas.py`).

### Backend — core docstrings that quote the addresses

- `core/engine_runtime/relay.py` and `core/engine_runtime/README.md` describe
  the caller as "the `/openapi/v1/bots/{bot_id}/…` runtime handler". Prose only,
  but it is the prose a reader uses to find the handler.

### Backend — tests

Address-only edits; no assertion about behaviour changes.

- `tests/…/openapi_v1/identity/test_identity_handlers.py`
- `tests/…/openapi_v1/engine_runtime/test_approvals.py`
- `tests/…/openapi_v1/engine_runtime/test_connection.py`
- `tests/…/openapi_v1/engine_runtime/test_engine_models.py`
- `tests/…/openapi_v1/engine_runtime/test_routing.py`
- `tests/…/openapi_v1/engine_runtime/test_sessions.py`
- `tests/…/openapi_v1/engine_runtime/test_tenant_isolation.py`
- `tests/…/openapi_v1/engine_runtime/test_schema_docs.py`
- `tests/…/openapi_v1/test_openapi_error_schema.py`

The engine-runtime tests carry a `not-my-bot` / `other` cross-tenant case that
asserts a *different bot's* path is refused. Those literals move with the
component (`/openapi/v1/bots/engine/other/status`, not
`/openapi/v1/bots/other/engine/status`), and the assertion they make is
unchanged.

### New test — the rule itself

A new `tests/…/openapi_v1/test_path_convention.py` asserts the rule against the
live app rather than against a hand-maintained list, so a future route that
breaks it fails here instead of in review:

1. Every `/openapi/v1/` path's **first segment after `/openapi/v1/bots`** is a
   literal (not `{…}`), drawn from the known component set — or the path is
   exactly `/openapi/v1/bots`, or its first segment is `{bot_id}` and the route
   belongs to the bots component.
2. No path contains `/bot/`.
3. No path contains `channels`.
4. The reserved-name set in the docs equals the set of literals actually
   occupying that position.

Point 1 needs a precise formulation, since `/openapi/v1/bots/{bot_id}/status`
must pass and `/openapi/v1/bots/{bot_id}/sessions` must fail. The check is on
the **set of paths**, not on any one path: collect every literal that appears in
the first position after `/openapi/v1/bots`; that is the component set. Then
assert that for every path whose first segment is `{bot_id}`, the *second*
segment is not a member of the component set. `status`, `passport`, `restart`,
`auth-status` and `engine-config` are bots-owned sub-resources and are not
component names; `sessions`, `engine`, `models`, `approvals`, `connection` and
`skills` are, and after this change none of them appears in that position.

### Gateway — published artifact

- `src/gateway/configs/schemas/bots.openapi.json` — regenerated by running the
  backend's `scripts/dump_openapi.py` and publishing through
  `src/gateway/scripts/gate_and_publish_openapi.py --allow-breaking`. Using the
  real gate (rather than copying the file) is what proves the artifact is
  exactly what the pipeline would produce, and it prints the breaking-change
  list that belongs in the PR description.

  The gateway's own `tests/fixtures/bots.openapi.json` is a three-path
  hand-written fixture for the served-doc tests and is **not** regenerated — it
  is not a copy of the artifact.

  Gateway routing needs no change: `DomainMap.domain_for` keys on the first
  segment after `/openapi/v1`, which is `bots` for every path here, before and
  after.

### Docs

- `src/backend/docs/openapi-v1/README.md` + `README.zh-CN.md` — the endpoint
  tables per component, the mount-ordering note, and the channels sections. The
  channels rows are removed and the "parked" note replaced with a line recording
  that the component was deleted and why, so nobody re-adds it expecting the
  stub to still be there. A new **Addressing rule** section states the rule and
  the reserved-name set, since that is the thing a future component author needs
  and there is currently no place that says it.
- `src/backend/docs/openapi-v1/engine-surface.md` + `.zh-CN.md` — the
  endpoint-by-endpoint Track C inventory, which quotes every engine-runtime
  address.
- `src/gateway/configs/schemas/README.md` — check whether it states a path count
  or shape; update if so.

## Approach notes

**Why not aliases.** Considered mounting the old paths as deprecated aliases.
Rejected: the surface has no reachable external caller yet (`route_security`
requires a Google-resolved `user` identity), so there is no contract to keep,
and an alias at `/openapi/v1/bots/{bot_id}/sessions` re-introduces exactly the
wildcard-vs-component ambiguity the change removes — with the added cost that
the published document would then describe two addresses for one resource.

**Why `catalog` rather than leaving the catalog at the bare `/skills`.** Under
the rule, `/openapi/v1/bots/skills/{bot_id}` is the bot-scoped address; a
catalog detail at `/openapi/v1/bots/skills/{skill_id}` occupies the same slot
with a different meaning, and no ordering rule can disambiguate two wildcards.
The alternatives were to exempt skills from the rule or to invent a
`skill-catalog` component; a reserved literal is the smaller change and matches
`check-name`/`ceiling`, which the surface already uses this way.

**Verification order.** The dump is the ground truth: `scripts/dump_openapi.py`
builds the real app, so a route that did not move shows up as a stale path in
the diff. Run it after the router edits and before touching docs, so the doc
tables are written from the actual output rather than from this plan.

## Risks

- **Silent shadowing if a router is mounted in the wrong order.** Mitigated by
  the new convention test plus the existing mount-order comment; a wrong order
  makes `GET /openapi/v1/bots/resources` return a bots-shaped 404 rather than a
  list, which the existing resources tests already catch.
- **A test that greps the app's OpenAPI for a hard-coded path count.** Handled
  by running the full backend module gate rather than only the openapi_v1
  subtree.
- **The compat gate refuses the publish.** Expected — every rename is a removal.
  `--allow-breaking` is the documented escape for a coordinated change, and the
  reason goes in the PR description as the script's docstring requires.
