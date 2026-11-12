# Caller cron sessions implementation

Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-cron-sessions-20261112`
Branch: `fix/caller-cron-sessions-20261112`, initially based on GitHub `origin/dev` d0cdd023453055304418c6170897936f321746b3.

Existing flow: ExpertChat authorizes Bot and indexed sessions, resolves the authenticated subject's Caller instance binding, then enriches metadata. Allowed changes are confined to this domain; no Engine, shared-runtime policy, OCB gitlink or deployment changes.

Implementation: a new read-only InstanceService lookup retrieves the exact subject Caller row without lifecycle operations. For the newly enabled empty-index path, the discovery helper resolves only an existing ready instance binding and sets a request-local marker; missing instances return empty and unready instances remain pending. Existing indexed sessions retain the prior connection lifecycle and trusted instance marker. The new cron discovery mixin requires service/caller, matching nonempty instance binding and the registered strategy discovery capability (OpenClaw only). Actual cron keys returned by that instance are registered through the existing ownership repository, enabling unchanged connect/messages/favorite/mutation authorization. No historical run key is synthesized. Discovery handles empty indexes, exact keys, malformed data, repeated/short filtered pages, deduplication and failures; merge/sort precede client pagination. Cron runtime timestamps survive repeated reads.

Necessary strategy/API changes: base provisioning strategy declares discovery unsupported by default; Default supports only OpenClaw. ExpertChat uses the registered strategy hook. The existing-instance method is declared by the owning InstanceServiceProtocol.

Files: new `expert_chat_cron_sessions.py` owns discovery; service integrates it and retains existing session enrichment; InstanceService exposes a read-only row lookup; owning README describes contract; dedicated tests exercise behavior.

Validation:
- RED: empty-index cron test failed `assert 0 == 1` against original code.
- GREEN: `uv run pytest tests/community/core/expert_chat/services/ -q`: 296 passed with engine provisioning strategy tests and ExpertChat architecture guard (28 dedicated cron tests) (17 existing Pydantic warnings).
- `uv run ruff check <new helper and tests> --select F,E203,E211,E265`: passed (legacy E selections require preview).
- `git diff --check`: passed.
- Modified main source is 969 lines; helper is 116 lines, both below 1,000-line limit.
- Full backend gate is running independently under orchestration; not claimed passed here.

Diagnostics: `caller_cron_sessions_request`, `caller_cron_sessions_success`, `caller_cron_sessions_failed`; include system/operation, route/method, Bot/user/owner/binding, page parameters, counts/selected actual keys, status and elapsed milliseconds; failure includes type only. Connection headers/token and raw upstream error text are never logged. Tests assert request/success/failure events and secret absence.

Risks: discovery depends on the independent OpenClaw runtime returning its actual session index. A real SQLite repository test verifies two subjects resolve distinct Caller bindings, persist separate ownership and successfully connect/read their own cron history while cross-user connect is denied. Runtime fetch failures retain existing indexed behavior. Discovery adds missing Backend ownership rows like legacy-session migration; DB write errors propagate. Read-only discovery RED verified 3 failures before the no-provisioning fix; GREEN verifies missing/failed/init instances never enter get_caller_connection. No preproduction deployment or authenticated online validation was performed.
