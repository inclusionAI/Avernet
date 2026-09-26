# `engine.community.plugin_api.notification`

The **notification port** — the `NotificationService` Protocol plus shared
recipient-resolution helpers. The corp DingTalk impl
(`plugins/prod/notification/`) and the community logger impl
(`plugins/community/notification/`) satisfy it; `core/cron` triggers it.

## Context Boundary

```yaml
purpose: "Notification port (NotificationService.send_cron_notification) + recipient-resolution helpers, so cron can notify without coupling to a concrete channel (DingTalk vs logger)."
provides:
  - "engine.community.plugin_api.notification.NotificationService — notification port Protocol"
  - "engine.community.plugin_api.notification.resolve_user_ids / get_default_user_ids — recipient helpers"
consumes:
  - "engine.community.plugin_api.cron.CronJob / CronRunRecord — the payload it renders"
internal_dependencies:
  - engine.community.plugin_api.cron
```

### Change impact

Imports only `plugin_api.cron` (for the DTOs it renders) — never `core`,
`plugins`, or `api`. A signature change ripples to both profile impls and the
`di/modules/infrastructure/{corp,community}/notification.py` wiring. `recipients`
lives beside the port so leaf impls reuse resolution without a `plugins ↛ core`
edge.
