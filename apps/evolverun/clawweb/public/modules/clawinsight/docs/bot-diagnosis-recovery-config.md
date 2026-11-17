# Bot diagnosis and recovery configuration

This change owns ClawWeb configuration, recovery admission/notification, and the
existing Insight page. The upstream diagnosis scanner/scheduler is unchanged;
its owner can consume `diagnosis_enabled` independently.

## Storage and identity

`ce_bot_diagnosis_recovery_config` has one row per exact
`bot_id + entity_id + env`. `entity_id` is the existing Bot directory entity;
`env` is the target Bot environment. ClawWeb instances share these records and
must not partition/filter them by their own control-plane environment.

Columns: auto-increment `id`, the three identity columns, independent numeric
`diagnosis_enabled` and `recovery_enabled` (default 0), `config_json` TEXT NOT
NULL, `version` (starts at 1), `updated_by`, `gmt_create`, `gmt_modified`.
The identity index uses the monitoring schema's case-sensitive collations and
fits the 767-byte index limit. Inserts explicitly supply `{}` for JSON.
Switch updates use compare-and-swap on `version` and never overwrite JSON.

Recovery requests identify the Owner. Current `ac_bots` ownership resolves the
directory entity before reading configuration; service entities are not assumed
to equal their Owner, and leading zeros/environment boundaries are preserved.

```json
{
  "notifications": {
    "owner": { "enabled": true },
    "group": { "enabled": true, "route_id": "group_a" }
  }
}
```

Only explicit boolean `true` enables a channel. Missing settings, malformed
JSON, invalid notification fields, ambiguous ownership or failed configuration
reads skip notifications. Unknown top-level properties remain available for
future features. No per-Bot webhook is stored and no global notification default
is inherited for a Bot with a new-table record.

Group destinations stay in **`ce_app_config`**, under
`session_recovery_notification_routes`:

```json
{"routes":{"group_a":{"name":"A 群","webhook":"https://example.invalid/robot","timeout_seconds":10}}}
```

The registry row must have `enabled = 1`. A missing/invalid referenced route or
failed send never selects another route/channel. Existing claims and delivery
audit remain in use. The existing `session_recovery_owner_notifications.card`
configuration still supplies Owner card templates/callback routes by **ClawWeb
control-plane** environment. Its `enabled`/`bot_overrides` govern only Bots that
have no new-table record. Ordinary paused Owner notices remain paused.

## Behavior and HTTP contracts

- All production recovery admission resolves policy in the recovery service,
  before persistence or execution. Automatic diagnosis-triggered requests use
  that same service. A new row is authoritative, including explicit false;
  only a missing row consults the enabled legacy allowlist. Missing both denies
  admission; unavailable storage is retryable. Existing idempotent requests
  return their original Task without requiring admission again.
- `bot-configuration.ts` owns new/legacy source selection for admission, visible
  switches, notifications, team ownership and model credential selection. `legacy-configuration.ts` contains read-only
  parsers for the old keys. Only an unambiguous current Owner/directory lookup
  permits recovery admission or notifications. Read failures are never absence.
- `createMonitoringRecoveryTrigger` still accepts the old host's `config`
  argument (`findByKey`), as well as the intermediate `find` argument. They are
  deprecated and are not read: the supplied recovery service must enforce
  admission. `recovery_disabled` is a successful skip; unavailable service or
  configuration returns a retryable error. OCB's existing production assembly
  therefore remains compatible with this Avernet version.
- Closing the switches stops new admission. Existing tasks, feedback, results,
  observation/verification and termination keep their established lifecycle.
  Debug/simulation retain their existing explicit paths.
- `POST /api/insight/v1/monitoring/configurations` accepts either
  `{ "botRef": "opaque reference", "enabled": true, "version": 0 }` or
  `{ "all": true, "enabled": false }`. The one visible switch writes both
  flags; 0 is the expected version for a missing row. The current owner may
  configure a non-service Bot. The directory's `bot_type = service` marks
  platform-managed Bots. A single-Bot write to a service Bot returns 403.
- An authenticated ClawInsight or global administrator can read or replace a single Bot's
  full configuration within the existing tenant and target-environment scope via `GET` or `PUT`
  `/api/insight/v1/monitoring/configurations/:botRef`. Both return
  `{ configuration }`; a GET with no stored row returns `{ configuration: null }`.
  The PUT body contains independent booleans `diagnosisEnabled` and
  `recoveryEnabled`, the expected `version` (0 for insert), and `configJson`
  (a JSON object encoded as a string, at most 8 KiB). PUT replaces both switches
  and the JSON in one write. To omit notification settings, send `"{}"`.
  Notification channels accept route references and booleans as above.
  Stale versions return 409. Group URLs still come from the shared registry.
- Bulk operations use the existing owner directory and traverse all keyset
  pages, irrespective of UI search/page. Bulk writes skip service Bots and
  cover the current owner's eligible Bots. `{updated, failed}` reports partial
  success; retries reload versions and apply compare-and-swap to switch writes.
- Bot options/status return the effective recovery switch and mark service Bots
  with `isServiceBot: true`. Legacy-only
  Bots have `diagnosisEnabled: false`, their legacy recovery state, and version 0.
  The first UI write creates a new row, with `{}` notification JSON: it takes
  complete ownership and notifications remain off until explicitly configured.
  This does not copy or modify legacy notification preferences. Historical
  check/diagnosis data remains separate and readable after disabling.
- `GET /api/insight/v1/monitoring/session-recoveries/tasks` lists the authenticated
  user's tasks using `page`, `pageSize` (1–50), optional exact `botId`, `env`, and
  task `status`. It reads only page-sized detail artifacts, not execution audit
  histories. Task state and recovery effect are independent columns.

The **诊断自愈** sidebar entry contains **诊断** and **自愈**. The recovery
list shows task state and recovery result in separate columns and preserves
filters and pagination when opening an existing detail.

**诊断监控设置** shows the current owner's searchable, paginated Bot list.
The switch appears on when both flags are true. Service Bot switches are
read-only and show the platform contact. Platform staff use the administrator
configuration endpoint to configure those Bots.

Use `/insight?module=monitoring&openMonitoringSettings=1` as the invitation
path. Opening it after ClawWeb sign-in shows the settings dialog. The user
chooses which switches to save.

## Migration and deployment

1. Provision `apps/evolverun/docs/clawweb/bot-diagnosis-recovery.mysql.sql` through
   the existing managed DDL process. SQLite development databases use migration
   154. No production DDL/data changes are performed by this feature's UI.
2. In the existing authenticated maintenance host, call exported
   `migrateBotDiagnosisRecoveryConfigs(db, directory, scope, {updatedBy})` from
   `@avernet/clawinsight/server/services/monitoring/bot-config-migration`.
   Supply the existing shared DB, monitoring directory and trusted scope.
   **The default is dry-run**; inspect `planned`, `warnings`, `skipped`, and
   `routeCount`. Reports contain route references, never webhook addresses.
3. **Only enabled legacy recovery allowlist identities are candidates.** No
   diagnosis checks, notification-only overrides or historical Task targets are
   scanned. Resolve current directory ownership; deleted/unresolvable targets
   are reported and skipped. New rows start with diagnosis disabled and recovery
   enabled. This migration does not configure upstream diagnosis.
4. For those candidates only, materialize exact Bot/Owner/environment overrides
   before the old Owner default, including a true override under a disabled
   default. Group overrides precede the old default. Identical webhook/timeout
   pairs share a deterministic route ID. Invalid settings stay off and are
   reported. Existing new-table rows, including explicit disabled rows, are skipped.
5. Apply the reviewed plan with `{updatedBy, apply: true}`. Source versions are
   rechecked/locked in the transaction. Insertion conflicts roll back; rerun
   dry-run instead of overwriting. **Migration is explicit, never startup work.**
   Legacy policy keys are not rewritten by migration, runtime or the UI.
6. Deploy the compatible Avernet implementation first. Its unchanged OCB
   production caller can still use legacy configuration while the new table is
   empty. Provision the table before deployment: missing table/read errors stop
   admission rather than falling back. Do not mix switch writes with older,
   non-compatible consumers that cannot honor new-row precedence.
7. Pilot one current-owner Bot by writing its complete new row and referenced
   group route. Initially retain its legacy entry. Verify admission, notification
   destination, disabling new admission and existing Task lifecycle. Only then
   remove that Bot's legacy allowlist entry through the reviewed data-change
   process. Other Bots continue to use their existing configuration.

## Removing compatibility later

After migration, check remaining legacy-only current Bots and in-flight Task
notification targets before removing old policies. Remove the legacy branches
from `bot-configuration.ts`, their parser exports and the deprecated trigger
argument once all hosts have dropped it. Admission, Task execution, notification
claims, audit and UI writes remain the same pipeline; none needs a second
implementation or distributed new/old conditionals. Retain the Owner card
transport configuration while it still supplies templates/callback routes.

Rollback to a pre-compatible implementation can ignore new-table disables and
send using old destinations. Preserved legacy keys are **not** a synchronized
rollback policy: reconcile post-migration switches/routes and retire old
consumers before allowing writes. Preserve the new table and Task/Step/audit
history. No deployment, live Bot operation, upstream diagnosis change or
production migration is performed merely by installing this code.

## Team resources and gradual ownership migration

The existing Bot JSON supports an explicit `team_key` (a string, including
numeric names such as `"97"`). Only the per-Bot administrator PUT can replace
this JSON. Owner single/bulk switch writes preserve it. The global role is
resolved from trusted host membership; it grants this configuration API only,
not other administrators' monitoring views or owner-only bulk operations.

```json
{
  "team_key": "97",
  "notifications": {
    "owner": { "enabled": true },
    "group": { "enabled": true }
  }
}
```

Team resources live in the existing **`ce_app_config`** under
`session_recovery_teams` with `enabled = 1`:

```json
{
  "teams": {
    "default": {},
    "97": {
      "name": "97",
      "notification_route_id": "team_97",
      "llm": { "api_key_mist_key": "team-97-model-key" }
    }
  }
}
```

`default` is built in and has no optional resources unless configured. Other
team references require an enabled, valid definition. Invalid or unavailable
team configuration rejects new execution; it is not treated as legacy absence.
The administrator PUT validates the team before writing any switches or JSON.

- **Group:** an explicit Bot `notifications.group.route_id` wins over the team
  `notification_route_id`. Both reference the existing notification-route
  registry. A missing team group destination skips group delivery while a valid
  Owner setting can still send. A broken explicit route does not fall back to
  the team/default/legacy group. Missing or invalid notification settings still
  disable notifications; introducing teams does not implicitly enable old JSON.
- **Handbooks:** `session_recovery_handbooks.team_routes` remains the catalog of
  chapters. Explicit Bot ownership replaces only the old `bot_teams` lookup.
  A team without a handbook route (including `default`) uses common chapters
  with no custom handbook scope. A team with routes retains its custom scope
  even when the current fault category has no preferred custom chapter.
- **Model:** no configured MIST reference means AIS Base deployment defaults.
  A configured reference is read by the OCB host using its existing MIST
  provider/cache. CW sends the retrieved value only as
  `input.credentials.modelApiKey`. Base overlays `llm.api_key` for this invocation;
  model, endpoint and other defaults stay in the deployment configuration.
  Empty/invalid values or a failed lookup stop dispatch and use the existing
  retryable execution-failure path, never another team's/default key.

`bot-configuration.ts` is the single compatibility boundary. A missing new row
retains the existing legacy switch/notification behavior. A new row without
`team_key` retains historical handbook membership and the Base default key;
its notification behavior remains the existing new-row behavior. An explicit
`team_key` resolves new team resources and does not consult the obsolete Bot
roster. Repositories, task execution, notification transports and AIS Base do
not select between legacy and new ownership.

Task creation freezes handbook references and the optional `modelApiKeyRef`
in existing task state; no new table/column is required. Actual keys are never
stored in Task/Step artifacts. Each dispatch resolves the frozen reference,
so secret rotation remains possible without reassigning a running task to a
new team. Historical tasks without this field keep Base defaults. Base removes
credentials before task persistence and uses an invocation-private temporary
config outside archives; cleanup also runs after failure.

### Pilot and cutover

1. Deploy the compatible CW public/internal changes and the updated AIS Base
   before enabling any team MIST reference. Existing records need no rewrite.
   An old Base cannot consume the new per-invocation model credential.
2. First verify the current Owner on the user's selected production Bot.
   Establish an actual team `97` legacy baseline, then use the existing admin
   PUT to set that same Bot's `team_key = "97"` and explicit notifications.
   Reuse the existing 97 handbook routes. An explicit personal test-group route
   can isolate notifications without changing shared 97 settings.
3. Verify frozen 97 chapters, end-to-end recovery, configured personal/group
   notification destinations, and (when a MIST reference is configured) actual
   model consumption. Check real tasks independently; unit tests do not prove
   production MIST permission or runtime package deployment.
4. Only after the own-Bot pilot succeeds, migrate remaining Bots explicitly
   through the administrator API. Do not rewrite legacy keys at startup or
   synchronize two writable sources. Switch changes preserve the rest of JSON.
5. During this pilot the page retains its existing `{}` initialization. The
   later enrollment cutover will initialize `team_key: "default"` and explicit
   Owner/group `enabled: true`; it must not retrofit that default onto old `{}`
   or malformed JSON. A default team with no route sends no group notice.
6. After all relevant Bots have explicit team ownership, remove the legacy
   roster-selection/parser branch at the single boundary. Keep shared chapter
   routes and Owner card transport settings while consumers still use them.

No new configuration page, migration marker, dual-write mechanism or upstream
diagnosis integration is introduced. Deployment and production migration remain
separate operations from installing this change.
