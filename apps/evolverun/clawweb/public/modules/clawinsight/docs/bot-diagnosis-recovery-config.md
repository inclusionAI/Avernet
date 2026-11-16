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
is inherited at runtime.

Group destinations stay in **`ce_app_config`**, under
`session_recovery_notification_routes`:

```json
{"routes":{"group_a":{"name":"A 群","webhook":"https://example.invalid/robot","timeout_seconds":10}}}
```

The registry row must have `enabled = 1`. A missing/invalid referenced route or
failed send never selects another route/channel. Existing claims and delivery
audit remain in use. The existing `session_recovery_owner_notifications.card`
configuration still supplies Owner card templates/callback routes by **ClawWeb
control-plane** environment; its old `enabled`/`bot_overrides` no longer grant
notifications. Ordinary paused Owner notices remain paused.

## Behavior and HTTP contracts

- Automatic diagnosis-triggered recovery reads `recovery_enabled` on every
  event. Production direct recovery creation checks it too, before persistence
  or execution. Missing configuration denies new admission; unavailable storage
  is retryable. Existing idempotent requests return their original Task.
- Closing the switches stops new admission. Existing tasks, feedback, results,
  observation/verification and termination keep their established lifecycle.
  Debug/simulation retain their existing explicit paths.
- `POST /api/insight/v1/monitoring/configurations` accepts either
  `{ "botRef": "opaque reference", "enabled": true, "version": 0 }` or
  `{ "all": true, "enabled": false }`. The one visible switch writes both
  flags; 0 is the expected version for a missing row. Only the current owner may
  write, including callers who have administrator read access.
- Bulk operations use the existing owner directory and traverse all keyset
  pages, irrespective of UI search/page. They affect existing Bots, not future
  additions. `{updated, failed}` reports partial success; stale entries are not
  overwritten. Retry reloads versions. No notification preferences are written
  through this endpoint.
- Existing Bot options/status add `configuration` with both flags and version.
  Historical check/diagnosis data remains separate and readable after disabling.
- `GET /api/insight/v1/monitoring/session-recoveries/tasks` lists the authenticated
  user's tasks using `page`, `pageSize` (1–50), optional exact `botId`, `env`, and
  task `status`. It reads only page-sized detail artifacts, not execution audit
  histories. Task state and recovery effect are independent columns.

The left navigation and page heading are **Agent 诊断**. The existing diagnosis
view and new **自愈任务** are sub-tabs. **诊断监控设置** opens the current owner's
searchable/paginated Bot list with per-Bot and whole-roster controls. Mixed flag
states show “部分开启”. The task list opens existing recovery details and keeps
its filters/page in the URL for Back. Existing diagnosis/detail links continue
to work. No application configuration or notification management page is added.

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
3. The candidate set includes persisted Bot checks, enabled recovery allowlist
   identities, explicit legacy notification targets, and historical task targets.
   Diagnosis starts from persisted checks (review stale roster entries during
   dry-run); recovery starts from the enabled allowlist. Notification-only or
   historical-task-only targets do not acquire admission switches.
4. Owner migration materializes exact Bot/Owner/environment overrides before the
   old default, including a true override under a disabled default. Group
   overrides take precedence over the old default group. Identical webhook and
   timeout pairs share a deterministic route ID; private test groups stay mapped
   to their exact Bots. Invalid settings stay disabled and are reported.
5. Apply the reviewed plan with `{updatedBy, apply: true}`. Source versions are
   rechecked/locked inside the transaction. Existing new-table rows are skipped,
   including operator/user changes. Duplicate insertion conflicts roll back;
   rerun dry-run instead of overwriting. Repeating migration is safe. Legacy
   source keys are preserved for audit, never used as runtime fallback.
6. Deploy the matching Avernet and OCB revisions together after backfill. Avoid
   serving old and new admission/notifier implementations concurrently: route
   traffic only after the new pool is ready and retire old consumers before
   exposing switch writes. Verify owner access, a private notification route,
   pending tasks, and the intended target environments before enabling traffic.

Rollback requires reviewing post-cutover switch/JSON changes; the preserved
legacy keys are a snapshot, not a continuously synchronized rollback policy.
Pause new admission/notification consumers before restoring a reviewed legacy
configuration. Preserve the new table and Task/Step/audit history. No upstream
diagnosis deployment, live Bot operation, or production migration is implicit.
