# Edge permission context

## Provides

The existing Connect and Admission application services. AgentInvoke is an
additional runtime grant, not a new management application. Admission checks
target presence/status and consumes the direction/environment's valid edge;
AuthzContext retains kind/ref rather than translating AgentInvoke to a profile.
Connect reads and decisions reject non-Connect requests, and unfriend does not
remove AgentInvoke edges.

## Consumes

Service API repo ports for edges, bot configuration, profiles and requests;
existing registry/authority/notification hooks. No concrete DB plugin or
transport is used by this service. Edge lookups propagate dependency/corrupt
row errors via the checked repo contract. Profile/rules/public-default and
owner/manager behavior remain independent.

## Consumers and tests

Existing admission delivery adapters, direct A2A and bootstrap. Unit regressions
exercise real SQLite stores and the existing application services, including
direction/nontransitivity, status, malformed/error reads, legacy profile/rules,
Connect lifecycle isolation and unfriend coexistence. No new HTTP or SDK surface
is provided.
