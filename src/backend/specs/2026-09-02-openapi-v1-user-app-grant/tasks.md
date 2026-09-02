# Tasks: Public API — User-Level Authorization for Applications

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Branch: `claude/user-level-admission-control-po0rcj`, based on `dev`.

## Task 1: The record  `[x]`

- `core/user_app_grant/models.py`: `UserAppGrantModel` (`ac_user_app_grant`,
  live only, unique on tenant/app/user/env), `UserAppGrantLogModel`
  (`ac_user_app_grant_log`, append-only), `UserAppGrantRecord`,
  `UserGrantAction`; both tables register the tenant guard.
- `sql/2026_09_02_user_app_grant.sql` matches the models column for column.
- `core/schema.py` imports the models.

## Task 2: Repository and service  `[x]`

- `core/repository/protocols/bot/user_app_grant.py`,
  `core/repository/implementations/bot/user_app_grant.py`: grant (idempotent,
  race-safe), revoke (locked, logged), `list_for_user`, `find`.
- `core/user_app_grant/services/grant_service.py` behind
  `UserAppGrantServiceProtocol`; `api/user_app_grant_service.py` re-export;
  `di/modules/user_app_grant_module.py` registered in the container;
  conformance pair registered; module boundary README.

## Task 3: The admission seam  `[x]`

- `ActingCaller.user_grants` + `require_user()`; `granted_bot_ids()` always
  narrows to the delegator's own bots.
- `principal.py`: `_grant_reader(request, protocol)` resolves both readers;
  `require_granted_user`; `DelegatedUserIdDep`.
- `test_admission_inventory.py` maps `USER_GATED → require_granted_user`.

## Task 4: Every USER_GATED route on the dependency  `[x]`

- ceiling, routines aggregate, repository catalog (4), Skill Center sync,
  Spaces (6), Space skills (10), work orders and notifications (9), local
  devices (2). `_require_user_delegation` deleted from five routers.
- Skill README relabelled `REFUSED` and declares `refuse_app_only_caller`.

## Task 5: The HTTP group  `[x]`

- `authorized_apps/user_router.py` at `/openapi/v1/org/user/authorized-apps`:
  grant (user + app), list, withdraw; `UserAuthorizedApp` schema; mounted;
  `ADMISSION` rows `REFUSED`; `AUTHORIZATION` rows `NoCheck`; error mappings.

## Task 6: Gateway  `[x]`

- `POST /openapi/v1/org/user/authorized-apps`: user + app required.
- `GET /openapi/v1/bots/skills/{skill_id}/readme`: user required.
- `test_route_security.py`: consent rule, list/withdraw posture, `_HUMAN_ONLY`.

## Task 7: Tests and docs  `[x]`

- New: `tests/community/core/user_app_grant/test_grant_service.py`,
  `tests/.../authorized_apps/test_user_router.py`.
- Updated: `test_app_only_listings.py`, `routines/test_owner_routines.py`,
  `test_explicit_user_id.py` (228), `test_admission_inventory.py`.
- Docs: `docs/openapi-v1/README.md` (new section + changelog),
  `core/bot_app_grant/README.md` cross-reference.
