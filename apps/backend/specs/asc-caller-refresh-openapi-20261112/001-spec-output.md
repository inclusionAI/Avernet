# Independent stored-authorization Caller refresh

Add POST /openapi/v1/bots/{bot_id}/caller-token/refresh to the existing Caller identity router. Require UserIdDep, OWNER authorization and the existing addressed-Bot application grant. Derive the actor and Bot entity through existing trusted dependencies. No caller, sandbox or environment override is accepted.

Extend the existing ScheduledCallerRefreshProtocol with refresh_for_actor. Implementations authorize the current owner, reject read-only ownership transfer, refresh eligible alive runtimes with enabled scheduled tasks and return credential-free per-binding results and counts. Missing authorization skips, partial failures remain explicit. Community profiles return unsupported instead of reporting an update. No timer is added and existing owner callback refresh semantics remain unchanged.

Tests cover schema, handler delegation, stable safe failure envelopes, partial status, unsupported profile, authorization/admission inventory and registered happy/error HTTP cases. Local checks do not claim deployed runtime acceptance.
