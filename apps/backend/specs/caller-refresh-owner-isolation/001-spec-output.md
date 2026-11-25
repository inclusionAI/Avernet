# Isolate optional Caller refresh from owner callback

User requires all newly added Caller refresh failures to log safe metadata only and preserve original owner callback success. Keep original owner install outside isolation; its failures must still propagate. Catch Caller preparation (stable entity/env), tenant scope and provider execution errors after owner install. Return original owner bindings/token summary unchanged; log exception type only, no raw exception/traceback/credential, no false Caller-success event. Tenant context restores on success/failure. No independent timer/retry queue introduced; Caller failures no longer drive owner callback retries.

Implementation: latest GitHub dev in new Avernet worktree, follow-up PR because3513 merged. Update README/protocol commentary to match best-effort caller side effect. Keep OCB gitlink governed by remote dev; update open OCB5920 metadata with dependency/result.

Tests first reproduce provider/timeout/preparation/scope errors breaking owner result; verify real HTTP200 envelope on Caller failure, original owner error still fails, owner result unchanged, safe logs and tenant restoration. Run callback/Passport/handoff/DI/architecture regressions and independent review, then normal topic push and PR.


## Default-off rollout gate

User requested temporarily closing the owner-callback Caller refresh branch until tcauthmng is deployed. Add module-level SCHEDULED_CALLER_REFRESH_ENABLED=False as the first condition, so disabled callbacks avoid Caller dependencies/metadata/probes/RPC/outbound and preserve original owner result. Log fixed reason=disabled with safe Bot/owner identifiers. Existing enabled-path isolation tests explicitly enable the gate; verify shipped default is False and disabled callbacks cannot invoke failing/new dependencies, including absent optional port. This gate applies to owner-triggered runtime refresh, not interactive ASC ensure. Update REL20261110 PR3788 and keep dev PR3786 consistent.


## PR CI failure repair

Real JUnit artifacts from release run42644432021 and dev42644554524 each fail test_no_identity_vendor_in_core because the new rollout-gate Python comment names the credential vendor. Reproduce that architecture gate, replace only the comment and related core README vendor wording with neutral credential-provider terms, preserve default False and original behavior. Include identity-vendor architecture gate in release/dev targeted regression. Separately investigate Singlebox missing embedding CI configuration; do not fabricate credentials or lower gates.

## Release PR scope correction (2026-11-10)

Remove the upstream Singlebox backport from this release PR. Restore both Singlebox files to the release base, retain only Caller isolation/default-off changes, rerun relevant backend tests and inspect the new remote CI. Do not modify the dev PR or weaken CI gates.
