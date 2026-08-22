# Plan: Adopt the Collaborator Authorization Seam

## Approach

Land the migration as **one prerequisite change plus nine per-group changes**,
each its own PR. A group's change is always the same three moves: derive the bar
from the code, flip that group's rows to `Check(level)`, delete the permission
check that bar came from — leaving type gates, edit locks and audit writes
untouched. The prerequisite goes first because two groups cannot flip without
it.

**The seam is not modified.** Everything adjudicated here is adjudicable by it
as it stands: the bot on the path, the owner from `OwnerIdDep`. Two sets of
operations cannot be — harness, whose bot comes from the request body, and the
six retiring skills addresses, where two carry the bot in the query and four
resolve it from the skill record inside the handler. Both are excluded rather
than accommodated, and the legacy six keep their check by moving it into the
`deprecated/` package, where the same file already does exactly this for the
grant.

## Affected Components

- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py` — the table; every group edits its own rows here.
- `.../openapi_v1/bot_access.py` — the seam. Gains path-or-query bot resolution.
- `.../openapi_v1/engine_runtime/gating.py` + `core/engine_runtime/relay.py` + `core/engine_runtime/gate.py` — 26 rows; the check is `require_bot_operator` inside `relay.resolve_bot`.
- `core/engine_runtime/connection.py:205` — 1 row; the same check, placed to guard *credential composition*.
- `core/skill_center/authorization_hook.py` — 19 rows; a clean `can_manage_bot` boolean.
- `core/skill_center/services/bot_skill_asset_service.py` — 10 rows; several resolvers each calling `check_collaborator_permission(..., MEMBER)`.
- `core/service_bot/services/service_publication_facade.py` — 16 rows; `_resolve_bot` returns a `level` used downstream.
- `.../openapi_v1/channels/router.py` — 6 rows; `_require_admin` beside `_require_edit_lock`.
- `core/bot_collaborator/services/collaborator_service.py` — 5 rows; the editors endpoints, checked by the service they manage.
- `.../openapi_v1/render_screens/gating.py` — 3 rows; `require_editable_bot`.
- `.../openapi_v1/authorized_apps/{gating,router}.py` — 3 rows; needs `OwnerIdDep` first.
- `.../openapi_v1/diagnostics/router.py` — 2 rows; router-local `_authorize`.
- `.../openapi_v1/bot_chats/router.py` — 2 rows; becomes `NoCheck`, no check to delete.

## Data Model Changes

None.

## API / Interface Changes

No route, request or response shape changes. Two caller-visible behaviour
changes, both intended:

```jsonc
// The 22 retiring deprecated/ addresses whose replacement is in scope.
// Before: checked incidentally, because the check sat inside the shared handler.
// After:  checked by the seam, same bar as the replacement. Same masked 404.
```

```diff
# .../openapi_v1/authorized_apps/router.py — 3 handlers
- owner_id: str = Query(...)          # or absent entirely
+ owner_id: OwnerIdDep
# Required so _assert_check_rows_are_enforceable admits the row: the gate must
# adjudicate the same owner the handler acts on.
```

## Key Files & Functions

### Prerequisite — make the seam's limit an enforced one

The seam decides before the handler runs, so it can only adjudicate an operation
whose bot is on the path. That is true today and stays true; what is missing is
that nothing *refuses* a `Check` row on an operation that does not qualify.

```diff
# .../openapi_v1/authorization.py — _assert_check_rows_are_enforceable
 # existing: refuse Check on a WebSocket route
 # existing: refuse Check whose handler does not consume OwnerIdDep
+# new: refuse Check on a route that does not declare {bot_id} on its path.
+#      The gate reads BotIdPath; a route without one would have the gate
+#      adjudicate a value the handler never saw. This is what makes the
+#      harness and legacy-skills exclusions structural rather than
+#      documented — neither can acquire a Check row by accident.
```

### Prerequisite — keep the legacy skills addresses checked

Today these six are checked by accident: they call the same handlers as their
replacements, and those handlers go through `bot_skill_asset_service`, which
does the collaborator check. Task 9 deletes it from the service. So the check
has to be somewhere the legacy addresses still reach, or six bot-scoped
addresses go from checked to unchecked.

The seam cannot be that somewhere. It is a **route-level dependency** — FastAPI
runs it before the handler, holding only the raw request. Four of the six name
no bot in the request at all; the bot is in the database, found by looking the
skill up. The other two carry `bot_id` in the query, which the gate does not
read.

`deprecated/skills.py` can, because its code runs **inside** the request, after
the lookup, already holding the bot and the owner. There are **two** insertion
points, because the two shapes take different routes through the file:

```diff
# deprecated/skills.py:226 — _bot_behind, used by the four {skill_id} routes
     record = query_service.get_local_skill(skill_id=skill_id, actor_id=user_id)
     _require_skills_grant(caller, record)
+    _require_skills_collaborator(
+        collaborators, bot_id=record["bolt_id"], owner_id=record["user_id"],
+        user_id=user_id,
+    )
     return str(record["bolt_id"]), str(record["user_id"])
```

```diff
# deprecated/skills.py:109 — _check_collection_grant, used by the collection
# and upload shims, which never touch _bot_behind
 def _check_collection_grant(caller, *, bot_id, owner_id, user_id) -> None:
-    if not caller.is_application:
-        return
-    caller.require_bot(bot_id, owner_id=owner_id or user_id)
+    if caller.is_application:
+        caller.require_bot(bot_id, owner_id=owner_id or user_id)
+    _require_skills_collaborator(
+        collaborators, bot_id=bot_id, owner_id=owner_id or user_id,
+        user_id=user_id,
+    )
# The existing check is a *grant* check and returns immediately for a human
# caller, so it covers none of the collaborator dimension on its own.
```

**This file has already been bitten by exactly this.** `_collection_shim`'s
docstring records it: the replacements get their check from
`dependencies=_GRANT_CHECKED` on their own routes, but `legacy_route` registers
an *endpoint*, not a route, and route-level dependencies are not carried across
— "*without `_check_collection_grant` an application holding no grant at all
would read and write skills through them. That is not hypothetical: it is
exactly what this file did for one commit, caught in review.*" Moving the skills
group onto the seam is the same move that caused it, one dimension over.

### Per group — the shape of one migration

```diff
# core/skill_center/authorization_hook.py — representative of the simplest case
 def can_manage_bot(self, *, bot_id: str, owner_id: str, actor_id: str) -> bool:
-    if actor_id == owner_id:
-        return True
-    permission = self._collaborators.check_collaborator_permission(
-        bot_id, owner_id, actor_id, PermissionLevel.MEMBER
-    )
-    return bool(permission.get("has_permission"))
# The hook and its callers go with it; the seam now answers this question.
```

```diff
# .../openapi_v1/authorization.py — that group's rows
-    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets"):
-        ServiceChecked(PermissionLevel.MEMBER, "…core.skill_center.authorization_hook"),
+    ("GET", "/openapi/v1/bots/{bot_id}/skill-sets"): Check(PermissionLevel.MEMBER),
```

### Per group — what must survive the deletion

```diff
# core/engine_runtime/relay.py:138 — resolve_bot
     bot = self._bot_service.get_bot(bot_id, owner_id)
     facts = BotFacts.from_record(bot, bot_id=bot_id, owner_id=owner_id)
-    require_bot_operator(self._collaborators, bot_pk=..., caller_id=caller_id, ...)
     return facts
# KEEP: gating.resolve_operable_bot's require_operable_bot(...) — bot-TYPE
# gating (→501), which #1323 says is not an authorization question.
```

```diff
# core/service_bot/services/service_publication_facade.py:135 — _resolve_bot
     level = resolve_operable_permission_level(...)
-    if level < required_level:
-        raise ServicePublicationNotFoundError("bot not found")
     if require_service and bot.get("bot_type") != "service":
         raise ServicePublicationUnsupportedError("bot is not a service bot")
     return bot, level
# `level` STAYS COMPUTED: callers use it for lock applicability (:242-:266) and
# an OWNER branch (:250). Deleting the computation breaks the lock, not the bar.
```

```diff
# .../openapi_v1/channels/router.py:300 — a channel write
-    _require_admin(..., required_level=PermissionLevel.ADMIN)
     _require_edit_lock(locks, ...)     # KEEP — #1323 Decisions 1
```

For `collaborator_service` the deletions are `_check_operable_permission(...)`
only; `_editor_policy.require_capability` and `require_team_space_member`
(`:392`, `:393`) are capability and space checks, not collaborator bars.

## Dependencies

None. No manifest change.

## Risks & Mitigations

- **Risk:** `connection.py:205` places its check inside `build`, deliberately —
  its own comment says the rule is "about what may be *composed*, not about how
  it is served", so any future caller of `build` is covered. The thing composed
  is an operator credential granting `operator.admin` over every session on the
  device. A route-level gate protects today's only caller, not tomorrow's.
  **Mitigation:** treat this row as the one argued exception — flip the row to
  `Check(MEMBER)` and **keep** the in-`build` check, or delete it only with a
  test asserting `build` has no second caller. Flagged for the review gate.
- **Risk:** deleting a permission check also deletes the masked-404 that hid
  bot existence, if the remaining error path answers differently.
  **Mitigation:** each group's tests assert the refusal body is byte-identical
  to an absent bot's, as #1323's own `ENVELOPE_ERRORS` test does.
- **Risk:** double audit, and **only in the skill-centre group**. The seam
  writes a `BotCollabLog` row on every non-`GET` by a non-owner;
  `skill_center/services/skill_set_control_plane.py:61` and
  `local_skill_upload_service.py:89` take an `audit_log_repo` and write their
  own. Adopting the seam there makes both fire. Verified by grep rather than
  inherited from #1323, whose "the audit record in 2 services" gave the count
  and left the two unnamed — the publication facade and `collaborator_service`
  write **no** audit rows at all (zero references each).
  **Mitigation:** Tasks 10 and 11 delete the service-side write and assert
  exactly one row per mutating request; no other group has an audit to
  reconcile.
- **Risk:** `authorization.py` is a merge-conflict magnet and the base moves
  fast (10 commits in 30 minutes on 2026-08-22, two touching this file).
  **Mitigation:** one group per PR, rebased and landed before the next starts.
- **Risk:** a group's bar is finer than one number — the publication facade uses
  MEMBER for most and OWNER for four operations, already reflected per row.
  **Mitigation:** derivation is per row, not per module; the task records the
  `required_level=` argument each row's handler passes.

## Alternatives Considered

- **Additive migration** (seam on top, service check retained), then a second
  pass to delete. Rejected by the user: it reaches the end state twice as
  slowly and leaves the table claiming an enforcement split across two places.
- **`Check` rows only for the 16 path-addressed twins**, accepting six
  unguarded legacy skills addresses. Rejected: it knowingly ships bot-scoped
  addresses with no collaborator check.
- **Deleting the six legacy skills addresses** instead of adjudicating them.
  Rejected: it retires addresses ahead of the deprecation schedule the rest of
  the package is on.
- **One 93-row commit.** Rejected: every group is a behaviour change with its
  own argument, and a single PR makes the conflict window in `authorization.py`
  as wide as the review.
- **Teaching the seam to read the bot from the query string**, so the two
  query-addressed legacy skills routes could be adjudicated. Rejected once the
  count was checked: it would have served 2 rows, not the 6 first assumed, and
  the other 4 name no bot at all so they need a non-seam answer regardless.
  Carrying the check in `deprecated/skills.py` covers all 6 by one mechanism,
  needs no seam change, and dies with the package.
- **Deleting the 6 legacy skills addresses** instead. Rejected: same reason as
  the rest of the package — it retires addresses ahead of their schedule.
- **Leaving `bot_skill_asset_service`'s check in place** to cover them.
  Rejected: it double-checks every current caller for the sake of six retiring
  ones.

## Rollout

No flag, no migration. Order is a dependency order, not a preference:

```bash
# 1. prerequisite — refuse a Check row the seam cannot key on; retire the
#    no-adopter assertion. No row changes mode.
# 2. bot_chats     (2 → NoCheck)      no check to delete; smallest correction
# 3. diagnostics   (2 → Check)        router-local, no lock, no audit, no twins
# 4. render_screens(3 → Check)        same shape, one bar
# 5. authorized_apps(3 → Check)       needs OwnerIdDep on 3 handlers first
# 6. skill_center hook (19 → Check)   + delete skill_set_control_plane's audit
# 7. legacy skills                    move the collaborator check into
#                                     deprecated/skills.py, before 8 removes it
# 8. bot_skill_assets  (10 → Check)   + delete local_skill_upload_service's audit
# 9. engine_runtime    (26 → Check)   keep the type gate
# 10. engine twins     (16 → Check)   path-addressed, no seam change needed
# 11. connection        (1 → Check)   the argued exception; see Risks
# 12. publication facade (16 → Check) keep `level` and the lock
# 13. channels         (6 → Check)    keep the edit lock
# 14. collaborator_service (5 → Check) keep capability and space checks
```

Steps 2–4 are deliberately first and deliberately dull: they put the `Check`
path into production against real callers at the smallest blast radius before
anything with a lock, an audit or a twin depends on it.

## Test Strategy

Per group, in that group's PR:

```python
# tests/community/adapters/http/openapi_v1/test_authorization_inventory.py
def test_scaffolding_row_count_falls_by_this_groups_rows(): ...
def test_every_check_row_declares_where_its_handler_reads_the_bot(): ...

# tests/community/adapters/http/openapi_v1/test_bot_access.py
def test_query_addressed_gate_refuses_below_the_bar(): ...
def test_query_addressed_gate_rejects_a_request_naming_bot_id_twice(): ...

# per group, e.g. tests/community/adapters/http/openapi_v1/test_channels_*.py
def test_below_bar_caller_is_refused_byte_identically_to_absent_bot(): ...
def test_edit_lock_still_refuses_when_another_collaborator_holds_it(): ...
def test_one_audit_row_per_mutating_request(): ...
def test_legacy_twin_admits_and_refuses_exactly_as_its_replacement(): ...
```

The surface-wide assertion that no live operation carries the gate
(`test_no_live_operation_carries_the_gate`, #1323) must be **deleted in the
prerequisite PR** — it encodes "the seam has no adopter", which stops being true
at step 2. Replaced by its inverse: every `ServiceChecked` row that remains is a
harness row.
