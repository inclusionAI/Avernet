# Preserve independently managed outbound headers

## Requirement and scope
Generic BaaS outbound updates currently send a PUT without append mode. They can remove independently appended Caller identity headers, causing MCP initialization failures. The user confirms that BaaS append deduplicates and replaces matching rules while preserving unrelated rules.

Change `BaasService.update_device_outbound_rule` to explicitly send `mode=append` for its existing callers. Preserve positional callers, serialization, timeout and exception contract. Add an explicit keyword update mode only to preserve existing Teclaw bot-level replace semantics: Teclaw append requires session_key and is session-scoped. Both Teclaw publish refresh and device header updates select replace internally but omit the mode query parameter, preserving the upstream default replacement behavior; ordinary BaaS updates explicitly send mode=append. Existing dedicated Caller/JIT append paths remain unchanged. No OCB gitlink, deployment, credentials or production state changes.

## Design
Use the existing BaaS HTTP client and structured logger. Emit request/success/failure events containing device, mode, non-sensitive rule descriptors, HTTP/business status and elapsed time; never emit rule values or raw credential-bearing error bodies. No generic logging framework or new configuration.

## Verification
- Generic owner rule update preserves an already appended Caller rule.
- Repeated owner updates replace the matching rule with the latest value without accumulating duplicates, using a test transport modeling the user-confirmed BaaS contract.
- HTTP request carries append mode and unchanged serialized body.
- HTTP rejection, business rejection and transport failure retain error behavior and produce safe diagnostics.
- Existing Caller and Teclaw outbound tests remain passing; run focused lint and diff checks.
- Independent code review and regression reports precede PR creation.

## Delivery
Create a fresh Avernet worktree. GitHub inclusionAI/Avernet dev is the base, feature commits replay above it. Re-fetch and rebase before push; create English PR to dev and inspect real CI/review results. User explicitly authorizes implementation, rebase, push and PR; no deployment/merge.

## Compatibility evidence
Current BaaS `_teclaw_paas_service.update_outbound_operation_rule` rejects append without session_key; this is distinct from ARCA/BaaS matching-rule append. Tests must cover both Teclaw write sites and generic default append. No session key is fabricated.

## User clarification
The replace path must rely on the upstream default: omit the mode query parameter entirely. Only append sends mode=append. Internal mode selection may remain explicit to distinguish Teclaw global updates from BaaS upserts. Contract tests assert the absence of the replace query.
