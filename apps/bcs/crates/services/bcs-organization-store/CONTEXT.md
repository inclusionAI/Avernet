# bcs-organization-store Context

## Provides

- Memory and DbPlugin-backed implementations of OrganizationRepoPort.
- Disabling an organization disables all its memberships (one SQL transaction
  for the database implementation). Re-enabling the organization does not
  reactivate them; members must join again explicitly.
- `has_active_membership_by_bot` provides the bounded membership check used before
  Provider Bot retirement (one `LIMIT 1` SQL query, or a memory scan).

## Consumes

- bcs-service-api OrganizationRepoPort.
- bcs-db-api DbPlugin.

## Runtime ownership

This crate owns Organization SQL, row mapping, and memory persistence. It does not own HTTP, Provider authorization policy, discovery, or A2A behavior.
