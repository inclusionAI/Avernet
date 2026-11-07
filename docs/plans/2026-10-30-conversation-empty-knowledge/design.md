# Conversation knowledge without enabled selections

## Approved contract

After validating pagination, a live composite Bot identity, native conversation
access and the supported personal/current Bot type, a Bot with no enabled local
knowledge selections returns `{"items": [], "next_cursor": null}`. This includes
first-time use, all-disabled selections and legacy NULL-space personal Bots.
The request must not contact the knowledge provider or initialize a remote tenant.

When any enabled selection exists, preserve the existing bounded provider grant
page, grant/selection intersection and ready-metadata projection. Provider errors
remain errors; do not convert arbitrary 404/403/503 responses into empty results.
Management APIs, original-file ACLs, grants and enablement writes are unchanged.

## Plan

1. Extend `KnowledgeDisplaySource` with a read-only enabled-selection existence
   query, scoped by tenant, environment, exact Bot PK and resolved space.
2. Implement it with a single bounded query using the existing `idx_bk_enabled`
   index. No migration, enumeration, pagination loop or new dependency.
3. Call it only after conversation admission and Bot-type validation, before the
   provider. Short-circuit only the proven absence of enabled selections.
4. Regress the uninitialized-provider case with real SQLite and HTTP transport;
   retain rejection, enabled-provider-error, cursor and composite-scope coverage.

## Alternatives rejected

- Converting all provider 404s into empty results would mask missing routes and
  inconsistent existing assets.
- Creating/syncing remote tenants during a read would add unnecessary state and
  alter the first-use lifecycle.

## Delivery and validation boundaries

Implementation belongs in the public backend. The host only needs an updated
public gitlink after publication and mirror reachability have been verified.
Local tests are not live deployment evidence. No frontend or provider change,
DB write, runtime retry, schema update or automatic deployment is required.
