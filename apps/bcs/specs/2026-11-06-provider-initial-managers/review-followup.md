# Review follow-up

> Historical implementation/validation record. The 2026-11-09 fixes and current
> transaction/migration contract are in [carolynli-followup.md](carolynli-followup.md).


## Verified defects

- Native MySQL 9.7.2, full original migration chain: `Carol`/`carol` manager
  initialization fails with `InternalError("trusted bot registration failed")`.
- Direct ordinary Provider Core registration returns success for `[alice,bob]`
  without a manager. New regression test fails on the original code.
- PR Singlebox changed-line coverage reported 75/143 (52.45%), below >65%.

## Implementation plan

1. Preserve byte-exact identity (no lowercasing or accent-folding of principals).
   Append MySQL migration 043 for the registry and active actor-reference identity
   keys/predicates using utf8mb4_bin (32 columns across 23 tables; inventory below). SQLite/Memory are already byte-exact. Do not edit deployed
   migrations. Verify full fresh chain and retained-data upgrade on native MySQL.
2. Narrow ordinary Provider Core to single-owner identity/delivery registration;
   reject multi-user input before writes. Application splits the snapshot and
   performs atomic authority initialization. A2A keeps its composite contract.
3. Add native case/accent tests, existing-Human collision tests and ordinary
   partial-commit/A2A rollback assertions. Keep audit, retry and strict roles.
4. Add live HTTP owner/manager registration/replay/validation/A2A stories to the
   standard Singlebox suite; run the unchanged coverage entrypoint and verifier.
5. Record actual results and limitations; do not relax any CI gates.

## Migration/deployment

The new migration changes unique-key identity semantics, not display names or
stored identifiers. Apply before enabling multi-user registration, with writers
stopped while all ALTERs run (DDL auto-commits; no cross-table atomicity is
claimed). Existing folded uniqueness is stricter, so retained rows remain unique.
Widths, defaults, nullability, comments and indexes are retained. The approved-owner generated key was already
binary in migration 036. No SQLite migration is needed.

Do not roll back collation after admitting case/accent-distinct actors: a reverse
ALTER can collide or merge identities. Stop new multi-user clients before an
application rollback, retain migration 043 and current authority data, and resolve
any identity data explicitly rather than attempting a destructive schema revert.


## Related identity references (review expansion)

Changing only the Bot registry would enable distinct physical Bot IDs to alias
one default runtime permission profile, or Human IDs to collapse into a single
group/session membership. Native `ensure_default_profile(ProfileBot/profilebot)`
reproduced the same profile ID before expanding the migration. Therefore 043
aligns all verified active actor-reference keys/predicates, not just registration:

- Registry/creator: `bcs_bots.bot_uuid/created_by`, `bcs_user_identities.user_id`.
- Grants/profiles/requests/relations: `edge_grants.from_id/to_id`,
  `permission_profiles.bot_id`, `permission_requests.from_id/to_id`,
  `bcs_actor_relations.from_id/to_id`, `bcs_friendships.left_bot/right_bot`,
  `bcs_friend_requests.from_bot/to_bot`.
- Membership/routing: `bcs_provider_bot_bindings.bot_uuid`,
  `bcs_organization_members.bot_uuid`, `bcs_group_participants.bot_uuid`,
  `bcs_session_participants.bot_uuid`, `bcs_groups.dm_pair_key`,
  `bcs_session_speaking_leases.holder_actor_id`.
- Actor-scoped content/operations: `bcs_messages.sender_id/owner_bot_id`,
  `bcs_collaboration_drafts.owner_bot_id`, `bcs_state_machine_node_runs.assignee_bot_id`,
  `bcs_state_machine_runs.created_by`, `bcs_invite_codes.bound_user_id/created_by`.
- Channel identity predicates: `bcs_channel_bindings.target_json`,
  `bcs_channel_im_participants.im_user_id`, `bcs_channel_conversations.im_user_id`,
  `bcs_human_input_requests.reply_scope_key/active_slot_key`. These encode actor
  identity and are compared directly; they are not display/search fields.

This is a coordinated identity-schema repair, not a blanket database-default
collation change. Display names, provider credentials/refs, opaque config and
read-back-only audit metadata are unchanged. DDL may rebuild indexes/tables;
plan maintenance and capacity from actual table sizes. Native tests cover full
chain, retained-data upgrade, runtime-profile separation and membership writes;
they are not a production-scale DDL duration or lock-budget measurement.


## Follow-up validation (2026-11-06)

- Red/green: ordinary Core multi-owner direct call returned success before the
  fix; it now returns InvalidOperation before writing the Bot. Public application
  registration and A2A retain the approved multi-user behavior.
- Red/green native MySQL: `Carol`/`carol` previously failed initialization;
  `ensure_default_profile(ProfileBot/profilebot)` additionally reproduced shared
  permission-profile IDs before reference-column repair.
- Five affected crate test suites: **1,150 passed, 0 failed, 7 ignored**.
- `bcs-admin` normal tests: **84 passed, 0 failed, 5 ignored**.
- Native MySQL 9.7.2 focused full-chain test: **passed**. Covers fresh 001–043,
  retained-data upgrade, 100 managers, case/accent distinctions, existing lowercase
  Human versus new uppercase manager, ordinary partial-commit/authority rollback,
  A2A whole-registration rollback, retry, profile isolation and group/session
  membership isolation. The migration inventory tests were updated to 43 for
  MySQL only; SQLite stays at 42. No historical migration was edited.
- New live HTTP Provider manager story: **32 assertions passed** through the
  instrumented server, including validation, deduplication, 100 managers,
  case/accent identities, revocation/replay, ownership transfer and inline A2A.
- Unmodified full Singlebox runner executed with an isolated root under
  `singlebox/.dependencies/coverage/provider-managers-review`. Initial attempts
  exposed local path-case/profile ownership and a fixture's prohibition on config
  roots outside `.dependencies`; the final run used canonical paths inside that
  directory without relaxing the checks.
- Backend acceptance: **66 passed**. BCS changed-line coverage: **91.72% (133/145)**,
  above the unchanged **>65%** gate (original PR: 52.45%, 75/143). BCS runtime line
  coverage **54.08%**, method coverage **49.26%**, HTTP endpoints **177/177**, CLI
  leaf commands **66/66**.
- **Full Singlebox is still failed**: existing
  `story_cli_operator_chats_and_shares_files_in_terminal` failed at its direct-chat
  command (submitted a run, then exited 1). The new Provider story passed. The
  artifact verifier correctly fails on `summary.status` and BCS `e2e_status`;
  no green overall result is claimed and no gate was changed.
- Native `bcs-admin` full-chain suite: **1 passed**, including fresh migration
  application, history preservation and its existing store contracts. The first
  attempt failed because this local MySQL 9.7.2 server defaults EXPLAIN output to
  TREE, whereas the existing test reads the traditional `key` column. The same
  TREE output was reproduced before/after reverting the affected message-table
  collation in a disposable database. Setting only this isolated server's
  `explain_format=TRADITIONAL` made the unchanged full-chain test pass; no production
  config, query, assertion or coverage threshold was weakened.
- `git diff --check`, Python compilation, shell syntax and all 14 modified/new
  source-file line counts passed (largest 827 lines).


Local Singlebox artifacts: `singlebox/.dependencies/coverage/provider-managers-review/reports/`.
The report directory is ignored runtime output, not part of this commit. No
follow-up commit or push has been made while the remaining gate failures are
being reported. The remote PR status still reflects the prior pushed commit.
