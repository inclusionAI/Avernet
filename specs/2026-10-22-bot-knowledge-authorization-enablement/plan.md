# Plan

One current selection table with revision CAS, native editor checks and current
service-Bot draft checks. Catalog receives the existing business Bot and target
user identifiers. Keep source Bot primary keys internal to application storage.
No changes to MCP producers, their Protocols or transport. External reserved UUID
support requires a separately agreed contract and migration, not an active path.
Remove feature-owned membership capture/outbox and history additions.

Review fixes keep the single current-state table. Reclaim a space-mismatched
row under the Bot lock using the absent-view CAS, preserving the stored counter;
never carry enablement into the new space without catalog validation. Add
current-space enabled and cursor indexes, plus indexes for both existing
service-Bot draft-query shapes (source and legacy default). Publish records,
status semantics and runtime version selection remain unchanged. DDL is review
only: one new relation table and supporting indexes, no deployment execution.
