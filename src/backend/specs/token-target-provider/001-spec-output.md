# Provider-aware outbound token targets

## Problem
Caller and token plugins treat every runtime binding device_id as a logical BaaS Bot UUID. Direct ARCA bindings instead store their concrete sandbox target in device_props.sandbox_id.

## Design
Keep authorized, entity-scoped runtime binding selection unchanged. Share target selection through BaasService.resolve_token_outbound_device_id(binding), avoiding a dependency injection cycle. BaaS bindings retain the unique-device lookup; ARCA bindings use their stored sandbox target without a logical Bot lookup. Preserve the stored template suffix, reject missing/invalid targets and unsupported providers, and never invent a tenant/template. Existing append operations, domain policy, Caller-first ordering, failure isolation and Bot-type/allowlist gates remain unchanged.

Return a validated PaaS target string. Target resolution errors expose a stable non-sensitive reason; Caller maps ambiguity/not-found into its existing credential errors. Diagnostic logs contain provider, binding ID, reason and timing, never credentials or complete device_props. The existing fixed-host HTTP client and append validation remain the outbound security boundary.

## Tests
Reproduce ARCA lookup regression before changing production code. Cover ARCA direct resolution, BaaS unique/empty/multiple targets, unsupported provider, inactive/missing binding, missing/malformed sandbox ID, template suffix retention and path/control characters. Exercise real Caller and corp plugin adapter paths, preserve scoped default-Bot selection, assert zero logical-Bot lookup for ARCA, and verify sanitized diagnostics. Run focused, affected-module and architecture suites separately from corp tests.

## Delivery
Commit scoped changes, create a new rebase branch, fetch and rebase onto remote REL20260910 (release is the base). Avernet source goes only to inclusionAI/Avernet on GitHub. OCB consumes a mirror-verified source SHA; if synchronization is unavailable, report the dependency as pending rather than publishing an unverified gitlink. Do not auto-merge or deploy.

## Verified append boundary
PaaS outbound append resolves the stored template and connects directly to the raw container; no logical Bot/device registration lookup is required. The template must exist and be ONLINE. ARCA suffixes must be numeric BaaS template IDs, not backend tenant indices. Missing suffixes, connection ports and malformed identifiers are rejected rather than silently routed to a default template. Existing BaaS target syntax remains compatible. Runtime template availability and downstream header receipt require post-deployment validation; unit tests do not establish either.
