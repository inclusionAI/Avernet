# A2A Bot Card management application

Provides `A2aBotManagement`, implementing `A2aBotManagementService`. Authenticates
Provider management before public Card access, checks active Bot affiliation,
validates local credential readiness, then commits a complete Card through the
core contract. URL and inline JSON sources share validation; Bot names always come from Card.name; omitted summaries default from Card descriptions. Only Card, nullable source URL and revision
are persisted. Refresh scans bounded URL-only pages with a process-local cursor;
failures preserve the last committed snapshot. Routes derive from current policy
and credentials from the trusted Provider binding, rather than saved metadata.

Consumes `ProviderCoreService`, `A2aBotCoreService`, `BotRegistryCoreService`
and `A2aCardPort`. SDK/network
types, persistence SQL and concrete implementations stay outside this crate.
Bootstrap injects the implementations. Configuration time is supplied by the
trusted caller. No environment reads or probe task execution occur here.

Provider-admin creation consumes `A2aProviderRegistrationCoreService` after
public Card validation. It never creates an ordinary webhook Bot first or
mutates persistence directly. Core repeats Provider admission after the fetch
and commits the existing ownership/Card composite atomically, recording the
Provider principal. A2A duplicates conflict. Explicit updates without an expected
revision replace the complete Card atomically; legacy supplied revisions retain
CAS behavior. Refreshes always retain internal CAS protection.
Registration wiring also requires `BotRegistryCoreService`. After the atomic
ownership/Card registration succeeds, URL and inline registrations apply an
explicit avatar Set or Clear through `update_avatar_existing`; Keep issues no
avatar write. This matches ordinary Provider registration: a missing live target
returns BotNotFound and persistence failures propagate, while the ownership/Card
registration remains committed. There is no rollback or automatic retry of
registration when this subsequent write fails. Each explicit transition adds
one short existing-only registry transaction: a one-row existence query and
a guarded one-row update. Keep adds no registry query or write; no network
wait spans the avatar transaction.

Name-based management uses the existing Provider slug as provider_name, with
`[a-z][a-z0-9_-]{0,63}` validation. Bot names are exact, case-sensitive Card.name
values (2–64 Unicode characters, no control characters or surrounding whitespace).
Name resolution does not grant chat permission: the existing asynchronous chat
admission use case authenticates and authorizes the caller after resolution.
Name-only registration currently rejects AgentPass modes without a trusted
external Agent identity mapping; it never invents an AgentPass identity from a UUID.
Legacy identity-aware registration remains available.

Anonymous Agent authentication requires both a permissive Card and explicitly
enabled deployment policy. Bearer requirements require an enabled credential;
unsupported requirements fail before admission. Provider management credentials
remain mandatory and are separate from Agent-call authentication.

Centralized conformance tests exercise authorization, configuration, refresh,
stale writes and unchanged route semantics against real Bot core/SQLite storage.
The injected Card fixture records network-port invocations. Listening HTTP
fetch behavior is tested independently by the outbound client crate.

Public target resolution validates the Provider/ref and active A2A Card/Provider Bot binding
before pinning the Provider slug. Missing or invalid targets never mutate Provider
metadata. The conditional pin still fences a concurrent Provider rename.

## Public management v0.1

`management` derives Provider identity from an authenticated Provider-admin token,
validates the anonymous-only full Card and delegates commits to core contracts.
Its five-field result contains the stable BCN bot_id but never runtime credentials.
Provider-ID and Provider-name registration retain their admission policies and expose
the stable Bot identity. Public get/update/refresh use only bot_id, never Card.name.
Updates fence the original Bot ID and Provider binding across the fetch, retry local
CAS conflicts at most three times without refetching, and cannot retarget a renamed/deleted Bot.
Refresh retains single-CAS protection against newer explicit updates. Unmounted name
management handlers and their Service API methods are removed. Provider chat resolves
provider_bot_ref, not mutable names. Public registration requires that ref and returns
it with bot_id; Card names may repeat. Reference recovery authenticates the Provider,
queries existing env/Provider/ref columns exactly, rechecks binding and returns zero
or one management result. Classification v2 contains optional mutable party, no agentId. It is
not registration upsert or history storage.
The Provider must already have an enabled static-bearer downlink identity mode;
AgentPass identity-aware admission stays on the existing registration entry.

Public Agent registration accepts BCN visibility (protected by default, or public/private). The typed policy is passed into atomic ownership/Card admission; Card updates preserve it unless explicitly supplied; refreshes never modify it. Card and visibility updates share one CAS-fenced transaction. Legacy registration retains protected visibility. This policy does not alter user_visibility or existing discovery, connection and invocation authorization; private is not an owner-only Human access mode.
