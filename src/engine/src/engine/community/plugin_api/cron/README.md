# `engine.community.plugin_api.cron`

Shared **cron wire types** — `CronJob` and `CronRunRecord`. These are the neutral
shapes both `core/cron` business logic and the leaf notification impls
(`plugins/prod/notification/`, `plugins/community/notification/`) exchange, so a
plugin can format a cron notification without importing `core`.

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
