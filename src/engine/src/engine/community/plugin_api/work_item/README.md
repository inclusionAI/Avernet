# `engine.community.plugin_api.work_item`

The neutral **work-item port** — a vendor-agnostic view of an external work-item /
requirement system, plus its DTOs. Consumers (`api/work_item/`, the aicoding
auto-initiate composition root) depend on this abstraction; concrete impls live in
`plugins/prod/dima/` (corp) and `plugins/community/work_item/` (no-op).

## Context Boundary

```yaml
purpose: "Vendor-neutral WorkItemService port + DTOs (WorkItem/WorkItemCreate/WorkItemRef) so upper layers query work items without coupling to a specific system (e.g. DIMA)."
provides:
  - "engine.community.plugin_api.work_item.WorkItemService — neutral port Protocol"
  - "engine.community.plugin_api.work_item.WorkItem / WorkItemCreate / WorkItemRef — neutral DTOs"
consumes:
  []
internal_dependencies:
  []
```

### Change impact

A pure port: imports only its own `models` (+ stdlib/typing), never `core`,
`plugins`, or `api`. Changing a method signature ripples to exactly the impls that
satisfy it (`plugins/prod/dima`, `plugins/community/work_item`) and the callers
(`api/work_item/router.py`, `engines/aicoding` auto-initiate). Keeps the DIMA
vendor name out of the OSS-facing surface.
