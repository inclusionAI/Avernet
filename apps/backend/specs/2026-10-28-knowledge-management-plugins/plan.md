# Plan

## Ownership
Core owns provider-neutral management orchestration and display enrichment from existing public models. Delivery owns HTTP schemas and scoped exception mapping. adapters/knowledge_platform owns the remote HTTP protocol, configured origin and bounded JSON/raw transport. Deployment packages own identity directories, session interpretation and environment configuration.

KnowledgeCatalog is an ordinary outbound port. KnowledgeContentServiceProtocol is a resource-oriented Service API. Do not add Catalog/Content business plugins for single HTTP implementations. Only KnowledgeSessionPlugin represents a genuinely deployment-specific capability; preserve the existing selection/membership Plugin contracts.

## Composition and compatibility
KnowledgeManagementModule installs disabled-by-default config and an unavailable session implementation. Enabled deployments provide an explicit space dataset identifier and a trusted origin. The normal community Bot-selection router remains ungated; only the new management/content surface uses the new gate. Enterprise composition can retain its existing combined gate and aliases.

Public HTTP clients share configured pool limits and discard response cookies. JSON and raw-file operations use a lazy asynchronous pool with native cancellation, no redirects and no environment proxy inheritance. Closing a response must not close the shared pool. Lifecycle discovery closes the pool once at shutdown, and subsequent use fails.

## Verification and rollout
Migrate generic tests and retain deployment transport and fixed-provider pairing tests downstream. Check native authority, composite identity, Source/Job/run scope, CAS, closed defaults, original wire bodies and stream cleanup. Add a composition HTTP flow and import/credential ownership guards. Keep provider execution, deployment and actual production acceptance distinct from controlled transport tests.

Contribute public changes first, then pin a reachable public revision downstream. Public baseline advancement requires adjacent regression checks, independently of the feature relocation.
