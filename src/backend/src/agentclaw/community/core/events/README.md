# `agentclaw.community.core.events`

Event bus + canonical event types — pub/sub primitive for cross-domain notifications.

## Context Boundary

```yaml
purpose: "Event bus + canonical event types — pub/sub primitive for cross-domain notifications."
provides:
  - "EventBus"
  - "Required event delivery for durable hand-off boundaries"
  - "Canonical event type dataclasses"
consumes:
  []
internal_dependencies:
  - agentclaw.community.log
```

### Change impact

Event type changes break subscribers silently, so treat event shapes as a public
contract. Ordinary subscribers remain best-effort and cannot block siblings.
Handlers registered with `required=True` are reserved for durable hand-off
boundaries: all siblings still run, then `publish()` raises
`RequiredEventDeliveryError` so the producer's retry mechanism can redeliver.
