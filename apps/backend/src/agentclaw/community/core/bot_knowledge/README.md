# Bot knowledge

Current application selection only; external platforms own grants. The platform catalog uses
the existing business Bot ID and target user identifier; no new UUID contract.
Service Bots require the current draft editing state. No knowledge versions,
release hooks, audit tables or membership synchronization are introduced.

## Context Boundary

```yaml
purpose: Persist current knowledge selection independently of platform grants.
provides:
  - BotKnowledgeService
  - BotKnowledgeRepository
consumes:
  - DatabasePlugin
  - KnowledgeCatalogPlugin
internal_dependencies:
  - agentclaw.community.kernel
  - agentclaw.community.plugin_api
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_knowledge
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.spaces
  - agentclaw.community.core.service_bot
  - agentclaw.community.utils
```

Only native Owner/explicit Editor may edit. Enabling consults the platform with
the business Bot ID, existing owner/user identifier and current space; disabling does not require a platform call. Revision CAS and
native editor/draft checks run in the same transaction as the current selection.
Re-read on stale revision; no request history is replayed. Deployments must apply
the reviewed current-state schema before enabling integration.

Space-mismatched selections are not inherited. A new-space write uses revision
zero (the absent view); enabling rechecks that space's external catalog. Reusing
an old row keeps its monotonically increasing revision. Only current-space rows
consume the enabled limit. Disabling an absent relation returns disabled/revision
zero without creating a row. Enabled reads/counts and cursor pages have matching
indexes; current service-Bot draft checks retain their transactional recheck.


## Native editability and tenant lineage

Personal Space Owners and explicit Editors do not need Space-member rows.
Team Space edits still require ACTIVE owner and actor membership as well as
ownership or an explicit editor grant. Space existence, editor standing and
current service-Bot draft state are rechecked inside the write transaction.

`ac_bot_publish` has no tenant column. For legacy `default` Bots, publication
candidates are restricted to source Bot rows in the current tenant/environment
before selecting the latest version. A retained historical source PK remains
valid even if it differs from the current Bot PK; it is never silently rebound.
A matching publication whose source was hard-deleted cannot be attributed to a
tenant and fails closed (`BOT_DRAFT_REQUIRED`), including when ignoring it would
otherwise reveal an older draft. No new publication ownership is inferred from
business Bot/owner IDs alone, and no publication schema or DDL is changed.

## Live local acceptance

The unified Singlebox manifest registers all production Core files, both HTTP
routes and both declared `KnowledgeCatalogPlugin` methods. `list_authorized`
is exercised; `list_in_space` currently has no production caller, remains in the
denominator and is not manufactured into coverage through test-only business
calls. The initial Plugin API threshold is the measured 1/2 (50%), not a claim
that both methods were called. The three live HTTP/DB stories cover
selection/CAS/pagination, Personal editor access, Team revocation, draft lifecycle
and tenant-isolated legacy publication selection.

The runner sets `SINGLEBOX_ACCEPTANCE_KNOWLEDGE_FIXTURE_FILE` to the reviewed JSON
catalog. Only the `SINGLEBOX` composition profile reads it; exact Bot/owner/env/
space keys select grants through the existing local Plugin API. Missing catalog
configuration still fails closed; production and unit-test profiles ignore the
fixture variable. This is an external-catalog double, not real remote-platform
integration evidence. Full shared-stack acceptance and artifact verification
must be reported separately from an isolated Backend run.
