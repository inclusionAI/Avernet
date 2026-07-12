# core/economy/governance

Negative-governance subsystem: daily scan, notification, user feedback, audit, and emergency brake.

## Context Boundary

**Layer:** `core/` — business logic + data access (no HTTP, no plugin impl).

**Inbound dependencies (who imports us):**
- `adapters/http/economy/router.py` — HTTP endpoints for scan, whitelist, feedback, emergency, card callback
- `di/modules/economy_governance_module.py` — DI wiring (provider methods)
- `plugins/local/database.py` — table registration (`contracts.models`)

**Outbound dependencies (who we import):**
- `plugin_api/database` — `DatabasePlugin` (ORM session factory)
- `plugin_api/cache` — `CachePlugin` (emergency brake distributed state)
- `plugin_api/database_protocol` — TYPE_CHECKING only
- `plugins/prod/dingtalk_sender` — loaded at DI time, not at core import

**Internal communication:** modules within `governance/` may import each other freely.

## Directory Layout

```
governance/
  repositories/               IO layer — ORM access to governance tables
    __init__.py
    task_record_repo.py        TaskRecordRepository  (ac_governance_task_record_daily)
    notify_log_repo.py         NotifyLogRepository   (ac_governance_notify_log + ac_governance_audit)
    whitelist_repo.py           GovernanceWhitelistRepository  (ac_bot_whitelist)
  services/                    Domain logic layer — orchestration + business rules
    __init__.py
    scan_service.py            GovernanceBotService  (cron tick orchestrator)
    record_process_service.py  GovernanceRecordService  (offline-batch ingest)
    feedback_service.py        GovernanceFeedbackService  (user resolve actions)
    admin_service.py           GovernanceAdminService  (pause/resume/bulk/cancel/emergency/deliver)
    workflow_service.py        GovernanceWorkflowService  (review list/detail/action)
    lifecycle_service.py       GovernanceLifecycleService  (ticket state-machine driver)
    notify_lifecycle_service.py NotifyLifecycleService  (notify send state-machine driver)
    notify_render_service.py   NotifyRenderService  (rendering outlet + builder pure-fns)
    whitelist_service.py       GovernanceWhitelistService  (whitelist add/delete/list)
  contracts/                   ORM models + Protocol interfaces
    __init__.py
    models.py                  4 ORM table definitions
    protocols.py               Repository Protocols (NotifySenderPlugin → plugin_api/)
  lifecycle.py                 GovernanceBotLifecycle (cron participant)
  __init__.py
```

## Session Convention

| Layer | Session | Commit |
|-------|---------|--------|
| Repositories (read) | caller-supplied | no |
| Repositories (batch writes) | self-managed `orm_session()` | yes |
| Services | caller-supplied (scan long-lived session) | via `_safe_commit` |
| Services (emergency) | self-managed per-action | yes |

## Key Decisions

- **D2 (dirty-write归属):** UPDATE field logic stays in services; repos only do SELECT/INSERT/audit.
- **D3 (audit repo):** `audit` writes consolidated into `NotifyLogRepository.add_audit` (single method, same transaction).
- **Repository style:** Practical (concrete class in core module, not protocol+plugin split — that's harness mode, out of scope).