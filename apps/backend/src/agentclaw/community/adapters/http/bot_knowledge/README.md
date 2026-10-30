# Knowledge selection HTTP

## Context Boundary

```yaml
purpose: Adapt authenticated current-selection requests and publication-observation reads.
provides:
  - router
consumes:
  - BotKnowledgeServiceProtocol
  - BotKnowledgePublicationSelectionProtocol
internal_dependencies:
  - agentclaw.community.adapters.http.auth
  - agentclaw.community.api
  - agentclaw.community.di
  - agentclaw.community.kernel
  - agentclaw.community.utils
```

### Change impact
Existing current-selection GET/PUT behavior is unchanged. The read-only
`GET /api/bot-knowledge/{bot_id}/publications/{publication_id}/selection`
accepts a native service-Bot publication ID and uses current Owner/Editor
permissions to read that version's last successful knowledge-selection observation.
Callers select the publication through the native flow; this endpoint does not
choose a live version or mutate publication state.

`last_recorded` includes the captured IDs (including `[]` for explicit all-off)
and capture time. Missing or invalid observations return `unrecorded`; optional
storage failures return `unavailable`. Neither unknown result falls back to the
current draft selection or means all-off. Access/resource errors remain errors.
The observation may be stale and is not proof of deployment, file synchronization
or runtime authorization. No grant writes, history backfill or draft restoration
are introduced.
