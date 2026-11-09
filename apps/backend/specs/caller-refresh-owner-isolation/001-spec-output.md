# Isolate optional Caller refresh from owner callback

User requires all newly added Caller refresh failures to log safe metadata only and preserve original owner callback success. Keep original owner install outside isolation; its failures must still propagate. Catch Caller preparation (stable entity/env), tenant scope and provider execution errors after owner install. Return original owner bindings/token summary unchanged; log exception type only, no raw exception/traceback/credential, no false Caller-success event. Tenant context restores on success/failure. No independent timer/retry queue introduced; Caller failures no longer drive owner callback retries.

Implementation: latest GitHub dev in new Avernet worktree, follow-up PR because3513 merged. Update README/protocol commentary to match best-effort caller side effect. Keep OCB gitlink governed by remote dev; update open OCB5920 metadata with dependency/result.

Tests first reproduce provider/timeout/preparation/scope errors breaking owner result; verify real HTTP200 envelope on Caller failure, original owner error still fails, owner result unchanged, safe logs and tenant restoration. Run callback/Passport/handoff/DI/architecture regressions and independent review, then normal topic push and PR.
