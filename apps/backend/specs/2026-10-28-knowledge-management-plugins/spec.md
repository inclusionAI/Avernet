# Public knowledge management and deployment boundary

## Goal
Expose existing knowledge browser and management consumers without a private deployment package. The remote platform still owns canonical assets, native ACLs, grants, content, ingestion, connectors and scheduling. This does not create a second knowledge engine.

## Scope
- Public resource Router／Schema, management Service API, orchestration and public-model display repository.
- Deployment-neutral HTTP catalog, content, space and membership adapters using configured origins and shared transport.
- Deployment-specific session interpretation stays an ordinary content-adapter policy; unconfigured access rejects rather than impersonating a user. No new Plugin API.
- Existing Bot selection and durable space snapshot contracts remain intact.

## Acceptance
1. Preserve management/content URLs, pagination aliases, HTTP codes and bodies, CAS, uploads and raw streaming.
2. Public code contains no private import, internal hostname or enterprise credential names.
3. Preserve native KB permissions, workspace membership, composite Bot identity and environment/tenant guards. Bot candidates require KB MANAGE before enumeration.
4. No duplicate routes, ACL store, asset store, scheduler, schema migration or publication behavior.
5. Public tests run without private providers; default-closed admission has a real composition/HTTP flow.
6. Shared asynchronous transport preserves cancellation and releases individual responses without closing the pool; credentials and response cookies do not leak across callers.

## Non-goals
Publication-stage isolation, chat/MCP consumption, changing platform ACL policy, frontend or live deployment. The initial relocation preserved catalog fan-out; the follow-up below removes it. The existing slow-shard aggregate deadline limitation remains out of scope.

## Follow-up: bounded catalog detail batches
Use the remote TC catalog-details read endpoint for one page of 1–20 unique knowledge IDs. Preserve requested order, exact space/ID correspondence and all-or-nothing native authorization; an empty page makes no detail request. A missing batch endpoint or malformed/partial batch fails closed, never falls back to per-ID HTTP. Restore the original KNOWLEDGE_SCOPE_MISMATCH error body for invalid sync context. This follow-up does not fix the existing slow-stream total deadline or pre-body disconnect cleanup limitations.

## Approved review follow-up (2026-10-28)
Remove the unnecessary session Plugin API/registry pair without weakening identity matching, credential allowlisting or fail-closed defaults. Register the shared management/content router normally, exactly once; keep existing community versus enterprise Bot-selection gate behavior. Endpoint acceptance must traverse actual composition providers, with only config, data and external transport/auth boundaries seeded. Breaking a real provider must make its positive endpoint acceptance fail. No changes to upstream business authority, batch contracts, rollout toggles, publication isolation or MCP.

## Bounded grant-state and available-KB reads

- Management CAS-state reads request one exact, ordered batch of 1–20 composite Bot/Owner identities. A missing state must be explicitly returned as false/revision zero; revoked entries retain their CAS revision. Never infer absence from a bounded prefix of grant history.
- Available-KB management pages request only the current provider page (`cursor`, `limit<=20`) and one metadata batch for that page. They never prefetch the complete authorized list or compute local offsets. Provider cursors are opaque integers and can decrease; pass them through unchanged.
- Incomplete, mismatched, duplicate or malformed provider replies fail closed. The existing plugin enablement contract stays compatible; this change does not migrate its legacy list call.
- The configured knowledge-platform receiver must support exact state POST and authorized-KB server pagination before deploying this consumer. No fallback to history scans, unbounded lists or per-KB HTTP is permitted. Restart pagination at zero during transition from the old local-offset implementation.
