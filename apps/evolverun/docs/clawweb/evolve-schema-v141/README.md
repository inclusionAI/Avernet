# Session recovery storage

The shared `ce_tasks.config_json` expansion is withdrawn. Version 141 stays reserved with no SQL; this change neither widens nor shrinks an existing column.

Session recovery reuses `ce_tasks`, `ce_steps` and `ce_repair_tool_calls`. Phase reports, complete plans, judgments, evidence attachments and result snapshots use the existing object store with digest references. Compact pending result references and retry state live in `ce_tasks.config_json.resultDelivery`; successful delivery is recorded in the phase report.

The unshipped v142 migration for `ce_session_recovery_results` is removed. Neither `ce_session_recovery_records` nor `ce_session_recovery_results` is required. No new table, remote DDL or column expansion is part of this change. Existing widened columns are not automatically shrunk.

Callbacks occur only on effect-wait expiry or task completion. Owner waiting, approval, notification and annotation remain local business history. Legacy inline Task data is converted on subsequent updates; only waiting-effect and terminal result snapshots remain eligible for delivery.
