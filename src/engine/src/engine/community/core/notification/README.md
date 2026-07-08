# `engine.community.core.notification`

The notification **domain facade** — exposes the `NotificationService` port (from
`plugin_api/notification/`) to `core/cron`, which fires notifications on cron run
completion. The concrete channel (DingTalk vs logger) is chosen by DI per profile.

## Context Boundary

```yaml
purpose: "core-layer entry point for notifications: exposes NotificationService to core/cron, delegating the abstraction to plugin_api.notification."
provides:
  - "engine.community.core.notification.NotificationService"
consumes:
  - "engine.community.plugin_api.notification.NotificationService"
internal_dependencies:
  - engine.community.plugin_api.notification
```

### Change impact

Thin layer over `plugin_api.notification`. Primary consumer is `core/cron`
(services trigger `send_cron_notification` after a run); the concrete impl is bound
by `di/modules/infrastructure/{corp,community}/notification.py`.
