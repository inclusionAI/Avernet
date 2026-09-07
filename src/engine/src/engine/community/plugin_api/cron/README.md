# `engine.community.plugin_api.cron`

Shared **cron wire types** — `CronJob` and `CronRunRecord`. These are the neutral
shapes both `core/cron` business logic and the leaf notification impls
(`plugins/prod/notification/`, `plugins/community/notification/`) exchange, so a
plugin can format a cron notification without importing `core`.

`CronJob` / `CreateJobRequest` now also carry optional `owner_id` and `bot_id`
fields so adapters can round-trip the creator/bot context without parsing the
legacy command string.

## Context Boundary

```yaml
purpose: "Neutral cron DTOs (CronJob, CronRunRecord) shared between core/cron and the notification plugins, so plugins consume cron data without a plugins->core edge."
provides:
  - "engine.community.plugin_api.cron.CronJob — cron job definition DTO"
  - "engine.community.plugin_api.cron.CronRunRecord — cron execution record DTO"
consumes:
  []
internal_dependencies:
  []
```

### Change impact

Pure DTOs (dataclasses; stdlib/typing only). Changing a field ripples to
`core/cron` producers and every notification impl that renders a run record.
Sinking these here (out of `core/cron/models`) is what keeps
`plugins/prod/notification/dingtalk_impl.py` off a `plugins ↛ core` violation.

### Compatibility

The `owner_id` / `bot_id` fields are additive and optional (`None` by default),
so existing callers remain compatible while updated adapters can populate them.


### Engine properties (`engine_properties`)

`CronJob` / `CreateJobRequest` / `UpdateJobRequest` carry an optional
`engine_properties` bag for engine-specific cron switches. Each switch must be
declared as an explicit, named field on the HTTP schema
(`api/cron/schemas.EngineProperties`) — there is **no** open/passthrough bag, so
every new switch is versioned, validated, and reviewed with the change.

- `reuse_session` (aicoding `agentTurn`): reuse the existing conversation on
  each trigger (`True`, default) vs. start a fresh one (`False`).

**Consumer scope (propagation).** `engine_properties` is consumed only by the
**corp aicoding** engine (`corp/engines/aicoding`, present in the internal full
checkout, excluded from the GitHub community export). Community engines
(`openclaw`, `claude_code`) do **not** read it — this is an explicit
no-op-by-design for those engines, not a silent discard. Conformance tests for
the aicoding consumer live in the internal checkout alongside the corp adapter.
Requests omitting `engine_properties` are unaffected on every engine.
