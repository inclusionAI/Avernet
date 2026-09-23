# Cron Guard internal Service API (draft v1)

This is a private, Bearer-authenticated Service API for a ClawWeb Cron Guard
adapter. It does not decide whether a governance improvement is eligible, send
notifications, or approve a stop. Those policies belong to the governance and
ClawWeb layers. The credential is resolved from the configured secret registry;
an absent credential denies every request. Only the `online` runtime stage is
addressed. No endpoint edits OpenClaw's `jobs.json` directly.

## `GET /api/internal/cron-guard`

Required query parameters: `target_user_id`, `target_bot_id`. Returns each
task's normalized `task_id` (`task_id` or legacy `id`), `name`, `enabled`,
`runtime_stage`, and `configuration_fingerprint`. The fingerprint is SHA-256 of
canonical JSON over `name`, `schedule`, `payload`, and `command`. It permits a
compare-before-disable check without returning the potentially sensitive cron
prompt. An incomplete target list fails with HTTP 503.

## `GET /api/internal/cron-guard/{task_id}/runs`

Required query parameters: `target_user_id`, `target_bot_id`; optional `limit`
between 1 and 5000 (default 3000). The task must exist in the complete online
list. Returns per-instance `device_uuid`, `truncated`, and run records containing
only `job_id`, `started_at_ms`, and `status`. The caller must reject truncated or
incomplete histories when checking failure ratios. Prompt, output, and error
text are deliberately omitted.

## `POST /api/internal/cron-guard/disable`

Body: `target_user_id`, `target_bot_id`, `task_id`,
`expected_fingerprint`, `improvement_id`, `operator`, and `reason`. The current
online task must be uniquely present, enabled, and match the expected
configuration fingerprint. The adapter then calls the existing Cron Relay with
`{"enabled": false}`. An already-disabled task is idempotent. Any reported
`failed_targets` makes `success=false` even if other instances changed. The
caller must not close an improvement on partial success.

This API authenticates its machine caller; it does **not** establish Admin
approval or the 72-hour reminder sequence. The ClawWeb caller must enforce
those checks before requesting a disable. Until that caller and its Admin gate
exist, the credential must remain unprovisioned in all environments.
