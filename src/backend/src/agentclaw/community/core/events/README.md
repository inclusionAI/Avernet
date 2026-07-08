# `agentclaw.community.core.events`

Event bus + canonical event types — pub/sub primitive for cross-domain notifications.

## Context Boundary

```yaml
purpose: "Event bus + canonical event types — pub/sub primitive for cross-domain notifications."
provides:
  - "EventBus"
  - "Canonical event type dataclasses"
consumes:
  []
internal_dependencies:
  - agentclaw.community.log
```

### Change impact

Event type changes break subscribers silently — the bus does not fail on schema drift. Treat event shapes as a public contract.
