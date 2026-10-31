# A2A Bot Card management application

Provides `A2aBotManagement`, implementing `A2aBotManagementService`. Authenticates
Provider management before public Card access, checks active Bot affiliation,
validates local credential readiness, then commits a complete Card through the
core contract. URL and inline JSON sources share validation; omitted names and
summaries default from Card declarations. Only Card, nullable source URL and revision
are persisted. Refresh scans bounded URL-only pages with a process-local cursor;
failures preserve the last committed snapshot. Routes derive from current policy
and credentials from the trusted Provider binding, rather than saved metadata.

Consumes `ProviderCoreService`, `A2aBotCoreService` and `A2aCardPort`. SDK/network
types, persistence SQL and concrete implementations stay outside this crate.
Bootstrap injects the implementations. Configuration time is supplied by the
trusted caller. No environment reads or probe task execution occur here.

Provider-admin creation consumes `A2aProviderRegistrationCoreService` after
public Card validation. It never creates an ordinary webhook Bot first or
mutates persistence directly. Core repeats Provider admission after the fetch
and commits the existing ownership/Card composite atomically, recording the
Provider principal. A2A duplicates conflict; updates remain revision-guarded.

Centralized conformance tests exercise authorization, configuration, refresh,
stale writes and unchanged route semantics against real Bot core/SQLite storage.
The injected Card fixture records network-port invocations. Listening HTTP
fetch behavior is tested independently by the outbound client crate.
