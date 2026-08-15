# Tasks — `/openapi/v1/bots` Bot-First Addressing

Implements [`plan.md`](./plan.md). Groups run in order; tasks inside a group are
independent. After any router edit, run the dump (task 30) to see the surface
change — it is the fastest check that a prefix edit did what you meant.

```bash
cd src/backend && DEPLOY_PROFILE=test uv run python scripts/dump_openapi.py /tmp/surface.json
```

## Ground truth — write these first

- [x] **1. The invariant guard.** New
      `tests/…/openapi_v1/test_no_duplicate_request_fields.py`: over the
      generated public document, assert no operation publishes one field name in
      two of {path, query, header, cookie, body}, resolving `$ref` for request
      bodies and path-item-level parameters. Passes today; it is what keeps the
      original defect from coming back. Verify: green before any router edit.

- [x] **2. The parity harness.** New
      `tests/…/openapi_v1/test_legacy_parity.py` with the pair table empty and a
      helper that, given `(legacy_method, legacy_path, legacy_kwargs)` and the
      new equivalent, drives both through the test client and asserts identical
      status, envelope `code`/`message`, and `data`. Rows land as each group is
      re-addressed. Verify: collects and passes with an empty table.

- [x] **3. Pin the current surface.** Dump the document to
      `/tmp/before.json` and keep it for the duration — every later task diffs
      against it, and the 39/32 split in the spec is what the diff must show.

## New addresses — already bot-scoped (no handler edits)

For each: change the router prefix, strip `/{bot_id}` from the decorator paths,
update the module docstring. The handler keeps its `bot_id: BotIdPath`
parameter. Verify each with the dump.

- [x] **4. `identity`.** Prefix → `/openapi/v1/bots/{bot_id}/identity`;
      decorators `/{bot_id}` → `""`, `/{bot_id}/{file_type}` → `/{file_type}`.

- [x] **5. `connection`.** Prefix → `/openapi/v1/bots/{bot_id}/connection`;
      decorator `/{bot_id}` → `""`.

- [x] **6. `engine`.** Prefix → `/openapi/v1/bots/{bot_id}/engine`; decorators
      `/{bot_id}/available|capabilities|status` → `/available|/capabilities|/status`.

- [x] **7. `models`.** Prefix → `/openapi/v1/bots/{bot_id}/models`; decorators
      `/{bot_id}` → `""`, `/{bot_id}/{model_id}` → `/{model_id}`.

- [x] **8. `sessions`.** Prefix → `/openapi/v1/bots/{bot_id}/sessions`;
      decorators `/{bot_id}` → `""`, `/{bot_id}/{session_id}` → `/{session_id}`,
      `/{bot_id}/{session_id}/messages` → `/{session_id}/messages`.

- [x] **9. `approvals`.** Prefix → `/openapi/v1/bots/{bot_id}/approvals`;
      decorators `/{bot_id}/mode` → `/mode`, `/{bot_id}/modes` → `/modes`.

## New addresses — bot moves out of the query

- [x] **10. `resources`.** Prefix → `/openapi/v1/bots/{bot_id}/resources`;
      decorators drop nothing (they are already `""`, `/download`, `/preview`,
      `/stat`, `/mkdir`, `/upload`). On all seven handlers change
      `bot_id: str = Query(...)` to `bot_id: BotIdPath`. Leave `path` a query
      parameter — it is a workspace-relative path and cannot be a segment.
      Verify: dump shows seven `…/{bot_id}/resources…` addresses and none of
      them publishes a `bot_id` query parameter.

- [x] **11. `routines` — addresses.** Prefix →
      `/openapi/v1/bots/{bot_id}/routines`; decorators `""`, `/{routine_id}`,
      `/{routine_id}/run`, `/{routine_id}/runs`. On all seven handlers change
      `bot_id` to `BotIdPath`.

- [x] **12. `routines` — the body field.** Remove `bot_id` from `RoutineCreate`
      in `openapi_v1/routines/schemas.py`, and remove the `bot_id = body.bot_id`
      line and the inline `caller.require_bot(...)` from `create_routine`,
      together with the comment explaining why the check ran there. Verify: the
      published `RoutineCreate` schema has no `bot_id`; task 1 still green.

- [x] **13. `skills` — addresses.** Prefix → `/openapi/v1/bots/{bot_id}/skills`;
      decorators `""` (GET list, POST upload), `/{skill_id}`,
      `/{skill_id}/activate`, `/{skill_id}/deactivate`. Change `bot_id` to
      `BotIdPath` on the two that had it, and **add** it to the four
      `{skill_id}` operations. Verify: `POST …/{bot_id}/skills` still answers
      201 `created` / 200 `updated`, and no `…/skills/upload` address remains.

- [x] **14. `skills` — `owner_entity_id` → `owner_id`.** Rename the parameter on
      the list and upload operations, reusing the `owner_id` description the
      engine-runtime groups publish. Remove the two inline
      `caller.require_bot(...)` calls and their comments. Verify: no
      `owner_entity_id` anywhere under `openapi_v1/` outside `deprecated/`.

## Contract fixes

- [x] **15. `engine-config` → `/{bot_id}/engine/config`.** Move the two
      handlers out of `openapi_v1/bots/router.py` onto their own router mounted
      at `/openapi/v1/bots/{bot_id}/engine` with the **ordinary** error table —
      not `ENGINE_RUNTIME_ERROR_RESPONSES`, which documents a 501 and 504 an
      engine-config read cannot produce. Verify: the dump shows
      `…/{bot_id}/engine/config` alongside `…/{bot_id}/engine/status`, and the
      config operations document no 501.

- [x] **16. `session_key` out of the approvals write body.** In
      `engine_runtime/approvals/`, move it from the PUT body model to a query
      parameter with the same description the GET publishes; the body carries
      `mode` alone. Verify: task 1 still green and the two operations now agree
      on where `session_key` lives.

## The deprecated package

- [x] **17. Scaffolding.** New `openapi_v1/deprecated/` with `_shim.py` holding
      the route-registration helper (sets `deprecated=True` and the
      `" (deprecated)"` tag suffix) and `__init__.py` exposing
      `build_deprecated_router()` and `LEGACY_ROUTES`, the `(method, path)` set
      built from the registrations rather than restated.

- [x] **18. Re-register the eighteen.** `deprecated/engine_runtime.py`: one line
      per route binding the *existing* handler to its old path, for identity,
      connection, engine, models, sessions, and the approvals GET. Not the
      approvals PUT — its body changed, so it is task 19.

- [x] **19. Shim the approvals PUT.** Old address, old body (with
      `session_key`), delegating to the new handler.

- [x] **20. Shim `resources` (7).** Old address, `bot_id` as a required query
      parameter, delegating.

- [x] **21. Shim `routines` (7).** Old addresses; the create keeps
      `bot_id` in its body via a `LegacyRoutineCreate` model declared in the
      package.

- [x] **22. Shim `skills` (6).** Old addresses including `…/skills/upload`, and
      `owner_entity_id` preserved as the parameter name.

- [x] **23. Shim `engine-config` (2).** The old `/{bot_id}/engine-config`
      address.

- [x] **24. Move the second authorization mechanism into the package.** The
      legacy routers mount **without** `_GRANT_CHECKED`; each shim whose bot is
      not visible to the shared dependency performs the same
      `caller.require_bot(bot_id, owner_id=…)` call the handlers make today,
      before any service call. Verify: an application caller with no grant gets
      the same masked 404 on every legacy address it gets on the new one —
      extend `test_app_only_refusals.py`.

- [x] **25. Mount it.** In `openapi_v1/__init__.py`, include the deprecated
      router. Order it with the other literal groups, before the bots wildcard
      router.

- [x] **26. Deprecation headers.** New `openapi_v1/deprecation.py` middleware
      stamping `Deprecation: true` and `Sunset: <http-date>` when the matched
      route is in `LEGACY_ROUTES`; register it in `adapters/http/app.py`. The
      sunset date is one module constant — use the date agreed at the review
      gate. Verify: new `test_deprecation_headers.py` asserts every legacy
      operation carries both headers and is `deprecated: true` in the document,
      and that no new operation is.

## Closing `TODO(#960)`

- [x] **27. `admission.py`.** Add entries for the 39 new addresses, keeping every
      legacy entry at its current mode. Move the two skills reads to
      `GRANT_CHECKED_ADDRESSED_BOT`. Delete `BODY_BOT_ID_OPERATIONS`,
      `SKILL_SCOPED_OPERATIONS` and `OWNER_ADDRESSED_OPERATIONS` — anything the
      deprecated package still needs moves into `deprecated/_shim.py`. Verify:
      `test_admission_inventory.py` green.

- [x] **28. `principal.py`.** Delete `_defers_to_its_handler` and the
      `TODO(#960)` paragraph; rewrite the `require_granted_bot` docstring to say
      that every operation now carries its bot where the dependency can see it,
      and that the refuse-when-absent branch is the backstop for one added
      later. Keep that branch. Verify: `test_principal_seam.py` green, extended
      to assert no route defers.

## Tests, docs, artifact

- [x] **29. Convention test.** Rewrite `test_path_convention.py` for the new
      rule: every bot-scoped operation is `/openapi/v1/bots/{bot_id}/…`; the
      routed reserved list is the six literals still in the `{bot_id}` segment;
      a **second** list covers the literals in the segment after `{bot_id}` and
      asserts they are unique. Both parsed from `README.md`, as today.

- [x] **30. Fill the parity table.** One row per legacy/new pair — 39 of them.
      This is the task that proves the compatibility promise; do not treat a
      re-registered route as trivially equal, since `admission.py` and the
      mount-level dependencies differ between the two.

- [ ] **31. Update the affected suites.** `test_explicit_user_id.py`,
      `test_admission_inventory.py`, `test_principal_seam.py`,
      `test_schema_docs.py`, `test_openapi_error_schema.py`,
      `test_skills_contract.py`, `test_skills_endpoints.py`,
      `test_bots_endpoints.py`, and the `engine_runtime/`, `resources/`,
      `routines/`, `identity/` sub-suites. Addresses and counts, not behaviour.

- [ ] **32. Docs.** `docs/openapi-v1/README.md` and `.zh-CN.md`: the addressing
      rule, both fenced name lists, and ~88 path references each.
      `engine-surface.md` and `.zh-CN.md`: 16 each. Rewrite the mount-order
      paragraph in `openapi_v1/__init__.py` — `resources` and `routines` no
      longer publish a bare single-segment collection root, so the
      wildcard-shadowing hazard it describes is gone. Add the deprecation
      window and the sunset date.

- [ ] **33. Regenerate the published artifact.** Run `scripts/dump_openapi.py`
      into `src/gateway/configs/schemas/bots.openapi.json`. Verify: it matches
      the backend's generated document exactly, and the operation count is 71
      new + 39 legacy = 110.

- [ ] **34. Gateway agreement.** Run
      `src/gateway/tests/unit/core/authn/test_route_security.py`. No `REFUSED`
      operation is re-addressed, so it should be green with no config change;
      confirm rather than assume, and say so in the PR.

## Close

- [ ] **35. Full module gates.** `OCB_PRE_PUSH_RUN_CI=1` for the backend module,
      and the whole `openapi_v1` suite.

- [ ] **36. PR.** Title `refactor(openapi-v1): address bot-scoped operations
      bot-first`. Body per `.github/pull_request_template.md` — Problem /
      Solution / Validation. Note that it closes `#960`, that nothing is
      removed, and that the artifact diff is large by design.

## Groups

Execution units. Groups run in order; tasks inside a group run in order.

| Group | Tasks | Code review |
| --- | --- | --- |
| **A. Ground truth** | 1–3 | yes |
| **B. New addresses — already bot-scoped** | 4–9 | yes |
| **C. New addresses — bot out of the query** | 10–14 | yes |
| **D. Contract fixes** | 15–16 | yes |
| **E. The deprecated package** | 17–26 | yes |
| **F. Closing `TODO(#960)`** | 27–28 | yes |
| **G. Tests and docs** | 29–32 | yes |
| **H. Artifact and gates** | 33–36 | no (generated + docs) |

Decisions taken at the review gate: the `Sunset` date is **2027-08-15**, twelve
months from the date this specification was approved; the three contract fixes
(tasks 14–16) ship in this feature rather than after it.
