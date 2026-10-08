# Tasks — `/openapi/v1/bots` Path Normalization

Implements [`plan.md`](./plan.md). Each task is independently verifiable; run
the dump (task 12) after any router edit to see the surface change.

## Routers

- [x] **1. `identity` — drop `/bot/`.** In `openapi_v1/identity/router.py`,
      change the three decorator paths `/bot/{bot_id}…` → `/{bot_id}…`. Update
      the module docstring. Verify: dump shows
      `/openapi/v1/bots/identity/{bot_id}` and
      `/openapi/v1/bots/identity/{bot_id}/{file_type}`, and no path contains
      `/bot/`.

- [x] **2. `skills` — own prefix, catalog literal, bot_id first.** In
      `openapi_v1/skills/router.py`, set the prefix to
      `/openapi/v1/bots/skills` and rewrite the five decorator paths to
      `/catalog`, `/catalog/{skill_id}`, `/{bot_id}`, `/{bot_id}`,
      `/{bot_id}/{skill_id}`, **declared in that order**. Add the comment
      recording that the order is load-bearing. Fix the module docstring, which
      claims the catalog lives at `/openapi/v1/skills`. Verify: dump shows the
      five new addresses and none of the old ones.

- [x] **3. `connection` — component before `{bot_id}`.** In
      `openapi_v1/engine_runtime/connection/router.py`, prefix
      `/openapi/v1/bots/{bot_id}` → `/openapi/v1/bots/connection`, decorator
      `/connection` → `/{bot_id}`. Update the module docstring.

- [x] **4. `engine`.** In `openapi_v1/engine_runtime/engine/router.py`, prefix
      → `/openapi/v1/bots/engine`, decorators `/status`, `/capabilities`,
      `/available` → `/{bot_id}/…`. Update the module docstring — but leave its
      references to `PUT /openapi/v1/bots/{bot_id}` and `POST
      /openapi/v1/bots/{bot_id}/restart` alone; those are bots-component paths
      and do not move.

- [x] **5. `approvals`.** In `openapi_v1/engine_runtime/approvals/router.py`,
      prefix → `/openapi/v1/bots/approvals`, decorators `/mode` (GET, PUT) and
      `/modes` → `/{bot_id}/…`. Update the module docstring.

- [x] **6. `sessions`.** In `openapi_v1/engine_runtime/sessions/router.py`,
      prefix → `/openapi/v1/bots/sessions`, decorators `""` → `/{bot_id}`,
      `/{session_id}` → `/{bot_id}/{session_id}`, `/{session_id}/messages` →
      `/{bot_id}/{session_id}/messages` (six routes total). Update the module
      docstring.

- [x] **7. `models`.** In `openapi_v1/engine_runtime/models/router.py`, prefix
      → `/openapi/v1/bots/models`, decorators `""` → `/{bot_id}` and
      `/{model_id:path}` → `/{bot_id}/{model_id:path}`. Update the module
      docstring. Verify the `:path` converter still swallows a slashed model id
      (`openai/gpt-5.3`) — the existing test covers this.

- [x] **8. `engine_runtime/__init__.py` docstring.** Its "all mounted under
      `/openapi/v1/bots/{bot_id}/…`" is false for all five groups after tasks
      3–7. Restate it as the new rule.

## Removal

- [x] **9. Delete `channels`.** Remove `openapi_v1/channels/` (`__init__.py`,
      `router.py`, `schemas.py`). Remove the import and the `_SUBGROUPS` entry
      in `openapi_v1/__init__.py`. Verify: `grep -rn channel
      src/backend/src/agentclaw/community/adapters/http/openapi_v1` is empty,
      and the dump has no `/openapi/v1/bots/channels*` path.

## Mounting

- [x] **10. Rewrite the mount docstring and ordering comments** in
      `openapi_v1/__init__.py`. State the addressing rule, state that
      `resources`, `routines`, `check-name` and `ceiling` are the remaining
      single-segment literals that the bots `{bot_id}` wildcard would otherwise
      swallow, and that this is why sub-groups mount first. Note that the
      engine-runtime groups are now literal-prefixed and can no longer shadow
      one another.

- [x] **11. Core docstrings.** Update the address quoted in
      `core/engine_runtime/relay.py` and `core/engine_runtime/README.md`.

## Ground truth

- [x] **12. Dump and diff the surface.** From `src/backend`:
      `DEPLOY_PROFILE=community uv run python scripts/dump_openapi.py /tmp/after.json`.
      Assert 41 paths; diff the sorted path list against the spec's tables and
      confirm every row landed. This output is what tasks 14–16 are written
      from — do not write the doc tables from the spec.

## Tests

- [x] **13. Update the address literals** in the nine test files listed in the
      plan. Behaviour assertions do not change. Take care with the
      cross-tenant cases: `other`/`not-my-bot` moves *with* the component
      (`/openapi/v1/bots/engine/other/status`), and the refusal it asserts is
      unchanged.

- [x] **14. New `tests/…/openapi_v1/test_path_convention.py`.** Assert against
      the live app's OpenAPI, per the plan: (a) no path contains `/bot/`;
      (b) no path mentions `channels`; (c) derive the component set from the
      literals in first position after `/openapi/v1/bots`, then assert no path
      beginning `/openapi/v1/bots/{bot_id}` has a component name in its second
      segment; (d) the reserved-name list in `docs/openapi-v1/README.md` equals
      that component set, so the docs cannot drift from the routes.

- [x] **15. Run the backend module gate.** The full backend test suite, not
      just the `openapi_v1` subtree — a hard-coded path or path count could live
      anywhere.

## Published description

- [x] **16. Republish the gateway artifact.** From `src/gateway`:
      `uv run python scripts/gate_and_publish_openapi.py
      configs/schemas/bots.openapi.json /tmp/after.json --allow-breaking`.
      Record the printed breaking-change list for the PR description. Verify:
      `configs/schemas/bots.openapi.json` has 41 paths and re-running the dump
      produces a byte-identical file. Do **not** touch
      `src/gateway/tests/fixtures/bots.openapi.json`.

- [x] **17. Gateway tests.** Run the gateway module gate. `test_route_security`
      references `/openapi/v1/channels` (a different, already-absent path) and
      should be unaffected; confirm rather than assume.

## Docs

- [x] **18. `docs/openapi-v1/README.md`.** Add an **Addressing rule** section
      (the rule, the reserved-name set, why the bots component keeps the bare
      base). Rewrite the per-component endpoint tables from task 12's output.
      Delete the channels endpoint table and replace the "parked" note with one
      recording the deletion and this spec. Update the mount-ordering note.
      Add a dated Changelog line, as the file's own header requires.

- [x] **19. `docs/openapi-v1/README.zh-CN.md`.** Same changes, kept in sync.

- [x] **20. `docs/openapi-v1/engine-surface.md` + `.zh-CN.md`.** Update every
      quoted engine-runtime address in the Track C inventory.

- [x] **21. `src/gateway/configs/schemas/README.md`.** Update if it states a
      path count or the surface's shape; leave alone if it only names the file.

## Close

- [x] **22. Full-surface re-verify.** Re-run task 12 after the doc edits and
      confirm the artifact still matches; commit and push to
      `claude/openapi-v1-backend-structure-82vulr`; open the draft PR using
      `.github/pull_request_template.md`, with the breaking-change list from
      task 16 under **Compatibility and risk**.
