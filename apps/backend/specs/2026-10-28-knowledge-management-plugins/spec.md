# Public knowledge management and deployment boundary

## Goal
Expose existing knowledge browser and management consumers without a private deployment package. The remote platform still owns canonical assets, native ACLs, grants, content, ingestion, connectors and scheduling. This does not create a second knowledge engine.

## Scope
- Public resource Router／Schema, management Service API, orchestration and public-model display repository.
- Deployment-neutral HTTP catalog, content, space and membership adapters using configured origins and shared transport.
- One request-scoped KnowledgeSessionPlugin for deployment-specific session interpretation; unavailable defaults reject rather than impersonate a user.
- Existing Bot selection and durable space snapshot contracts remain intact.

## Acceptance
1. Preserve management/content URLs, pagination aliases, HTTP codes and bodies, CAS, uploads and raw streaming.
2. Public code contains no private import, internal hostname or enterprise credential names.
3. Preserve native KB permissions, workspace membership, composite Bot identity and environment/tenant guards. Bot candidates require KB MANAGE before enumeration.
4. No duplicate routes, ACL store, asset store, scheduler, schema migration or publication behavior.
5. Public tests run without private providers; default-closed admission has a real composition/HTTP flow.
6. Shared asynchronous transport preserves cancellation and releases individual responses without closing the pool; credentials and response cookies do not leak across callers.

## Non-goals
Publication-stage isolation, chat/MCP consumption, changing platform ACL policy, frontend or live deployment. Existing catalog fan-out and slow-shard aggregate deadline limitations are not fixed by relocating consumers.
