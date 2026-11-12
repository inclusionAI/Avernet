# Caller cron sessions

Goal: Include runtime-created cron sessions in the expert-chat session list for an authenticated user's independent OpenClaw Caller instance.

Existing flow: ExpertChat authorizes Bot access, resolves the subject's independent Caller binding, and enriches Backend-owned session rows. Cron sessions originate in Engine and have no Backend row.

Allowed changes: ExpertChat discovery mixin and minimal list/runtime wiring, owning README, tests. No shared-runtime discovery, provisioning policy changes, Engine changes, OCB gitlink/worktree, or deployment.

Plan:
1. Write failing list test with empty ownership index and an actual cron key returned by the Caller runtime.
2. For empty ownership indexes use a read-only lookup of an existing ready Caller instance binding; never create, restart or upgrade an instance in this newly enabled path. Existing indexed sessions keep the prior lifecycle. Mark only connections resolved from that binding. Discover actual OpenClaw cron session keys with bounded pages and no user filter; register them in the existing ownership index so connect/history retain their existing authorization path.
3. Merge/deduplicate before sorting and pagination, respect exact key/favorites, preserve placeholders and existing sessions on runtime failures.
4. Declare discovery capability on the existing engine provisioning strategy (base False, Default OpenClaw True) rather than branch on engine names inside ExpertChat; expose the read-only instance lookup on its owning Service API Protocol. Verify Caller binding isolation, non-Caller/shared exclusion, pagination, malformed keys, runtime failures, credentials-free diagnostics and session lifecycle.

Logs: caller_cron_sessions_request/success/failed record Bot/user/owner/binding, method/route, offset/limit, selected keys/count, elapsed time, and error type. Never log connection credentials or upstream exception text.

Review/QA: test service behavior against controlled adapter responses. No claim of preproduction deployment or validation.
