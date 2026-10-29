# Plan

## Ownership
Core owns provider-neutral management orchestration and display enrichment from existing public models. Delivery owns HTTP schemas and scoped exception mapping. adapters/knowledge_platform owns the remote HTTP protocol, configured origin and bounded JSON/raw transport. Deployment packages own identity directories, session interpretation and environment configuration.

KnowledgeCatalog is an ordinary outbound port. KnowledgeContentServiceProtocol is a resource-oriented Service API. Do not add Catalog/Content business plugins for single HTTP implementations. Session authentication is an ordinary constructor policy on the content adapter, not a Plugin. Preserve existing selection/membership Plugin contracts.

## Composition and compatibility
KnowledgeManagementModule installs disabled-by-default config and a fail-closed ordinary request access policy. Enabled deployments provide an explicit space dataset identifier and a trusted origin. The normal community Bot-selection router remains ungated; only the new management/content surface uses the new gate. Management/content routes register in the existing phase-one resource mount alongside other normal resource routers; enterprise composition retains only the existing gated Bot aliases.

Public HTTP clients share configured pool limits and discard response cookies. JSON and raw-file operations use a lazy asynchronous pool with native cancellation, no redirects and no environment proxy inheritance. Closing a response must not close the shared pool. Lifecycle discovery closes the pool once at shutdown, and subsequent use fails.

## Verification and rollout
Migrate generic tests and retain deployment transport and fixed-provider pairing tests downstream. Check native authority, composite identity, Source/Job/run scope, CAS, closed defaults, original wire bodies and stream cleanup. Add a composition HTTP flow and import/credential ownership guards. Keep provider execution, deployment and actual production acceptance distinct from controlled transport tests.

Contribute public changes first, then pin a reachable public revision downstream. Public baseline advancement requires adjacent regression checks, independently of the feature relocation.

## Batch consumer follow-up
Keep KnowledgeCatalog.details as the public port. The outbound adapter sends one POST to the configured TC catalog-details endpoint with actor_staff_no and kb_ids, reusing request identity mapping, shared transport, error mapping and the existing budget. Validate complete ordered space-scoped results before returning them; no partial or per-item fallback. Add 1/20-item wire counts, invalid batch/rejection regressions and a fixed downstream real-provider pairing.

## Review follow-up design
Use a small ordinary access-policy callable; remove the Plugin Protocol, LOCAL/PROD registrations and unavailable implementations. The policy returns request-scoped ContentAccess. The public default raises the existing unavailable error; corp supplies the original allowlist/identity policy through composition. A test policy supplies only neutral test credentials. No authentication mode branches in services and no duplicated resource methods.

```python
ContentAccessPolicy = Callable[[str, Mapping[str, str]], ContentAccess]
RemoteKnowledgeContentService(client, access_policy)
# Application construction, without resolving injector or opening I/O:
app.include_router(management_surface)
# Enterprise OptionalRouters retains ONLY the gated Bot alias surface.
```

Replace the endpoint seed's manually constructed services with bottom-boundary config and transport setup. Obtain clients from the injector, retain the real public repository/catalog/service/content factories, and assert resolved object identity and recorded wire requests. Add a mutation guard demonstrating that a broken provider fails a success case. Add preload/registration uniqueness coverage and corp gate/identity/Cookie regressions. Keep prod/prepub YAML byte-identical to the latest user-approved PR input. Push public first, verify mirror reachability, then advance the existing enterprise PR.

## Release-target conflict resolution (2026-10-28)
The user-selected public target is `REL20261029`, not `dev`. Rebase preserves the target's Bot publication risk module, its Service API boundary declaration and registered risk flows alongside the knowledge module and flows. Both TaskArtifactConfigModule and TcFileUploadIntegrationModule remain in the application container. Normal resource-router registration is reused to avoid growing the 1,000-line application composition root; no optional management router or relaxed gate is introduced.

## Bounded-query follow-up

Replace the five-page grant history reader with one composite-identity POST. Validate the full response envelope, exact mapped Owner and request order before returning CAS state. Introduce a narrow `page_authorized` catalog port and preserve its opaque continuation in `available_for_bot`; keep existing selection and batch-detail projection unchanged. Retain the latest ordinary access-policy and normal resource-route composition, not the removed session Plugin. Test malformed/old receiver replies, one-call budgets and decreasing cursors. Real receiving-provider HTTP/SQLite pairing is maintained downstream; no internal receiver implementation or private endpoint is published here.

## Candidate index follow-up
Add `idx_bot_knowledge_candidates(avernet_tenant, env, space_id, is_delete, id)`
to `BotModel` and ship the matching operator SQL under
`core/knowledge_management/sql/2026_10_28_bot_candidates.sql`. Keep unique keys
and repository logic unchanged. See [candidate-index.md](candidate-index.md)
for alternatives, preflight, rollout/rollback and proof boundaries.
