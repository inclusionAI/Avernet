# bcs-organization-store Context

## Provides

- Memory and DbPlugin-backed implementations of OrganizationRepoPort.

## Consumes

- bcs-service-api OrganizationRepoPort.
- bcs-db-api DbPlugin.

## Runtime ownership

This crate owns Organization SQL, row mapping, and memory persistence. It does not own HTTP, Provider authorization policy, discovery, or A2A behavior.
