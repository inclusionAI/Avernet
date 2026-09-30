# `agentclaw.community.core.approval`

Neutral approval-callback helper for the bot-publish approval flow.

## Context Boundary

```yaml
purpose: "Neutral approval-callback handler for bot-publish approvals (the approval workflow itself is a Plugin: ApprovalWorkflowPlugin)."
provides:
  - "handle_approval_callback (dispatches an approval outcome into bot-public state)"
consumes:
  - "BotPublicService (callback writes back into bot-public state)"
internal_dependencies:
  - agentclaw.community.core.bot_public
  - agentclaw.community.log
```

### Change impact

The callback path is exposed via the approval HTTP router; signature changes
break the external workflow engine that posts back. The capability itself
(start/query/cancel) is the `ApprovalWorkflowPlugin` Protocol; its corp impl and
the vendor facade types live in `plugins/prod` (outside this neutral package).
