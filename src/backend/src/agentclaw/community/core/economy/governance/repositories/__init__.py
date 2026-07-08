"""Governance repositories — data-access (IO) layer.

Each repository wraps ORM access to one governance table:

- :class:`TaskRecordRepository` — ``ac_governance_task_record_daily`` (read + upsert)
- :class:`NotifyLogRepository` — ``ac_governance_notify_log``
- :class:`GovernanceAuditRepository` — ``ac_governance_audit``
- :class:`GovernanceWhitelistRepository` — ``ac_bot_whitelist``

Repositories take an injected :class:`DatabasePlugin`; a single ORM body
runs unchanged on prod OceanBase and local SQLite (``orm_session()`` yields
a SQLAlchemy ``Session`` in both runtimes).
"""
