/**
 * Schema DDL for ClawWeb.
 *
 * Includes ClawFlow's tables (v1–v4) so ClawWeb can read them,
 * plus ClawWeb's own workflow_specs table (v5).
 * All DDL uses CREATE TABLE IF NOT EXISTS for safe co-existence.
 *
 * Uses SQLite-compatible SQL as the canonical dialect. Dialect-specific
 * rendering is handled by each Dialect implementation via `renderDdl`.
 *
 * Compliance (ZDAS/MySQL):
 * - id columns use BIGINT (MySQL) / INTEGER (SQLite)
 * - gmt_create / gmt_modified required on every table
 * - No FLOAT/DOUBLE columns (metric_value uses DECIMAL/INTEGER)
 * - MySQL: TIMESTAMP DEFAULT CURRENT_TIMESTAMP / ON UPDATE CURRENT_TIMESTAMP
 * - MySQL: All columns have COMMENT
 * - MySQL: Indexes defined inline within CREATE TABLE (no separate CREATE INDEX
 *          to avoid ZDAS parser class cast errors)
 * - MySQL: Indexed columns use VARCHAR(255) not TEXT (OceanBase can't index TEXT)
 * - SQLite: Uses AFTER UPDATE triggers for gmt_modified auto-update
 */

import type { Migration } from './schema-types.js';
export type { DbType, Migration } from './schema-types.js';
import { migrationsV1To36 } from './schema-v1-36.js';
import { migrationsV37To74 } from './schema-v37-74.js';
import { migrationsV75To108 } from './schema-v75-108.js';
import { migrationsV109To143 } from './schema-v109-143.js';
import { repairDeployTagMigrations } from './schema-repair-deploy-tag.js';
import { workflowAiDraftMigrations } from './schema-workflow-ai-draft.js';
import { flowRunMarkersMigrations } from './schema-flow-run-markers.js';

/**
 * Trigger DDL for auto-updating gmt_modified on SQLite.
 * MySQL handles this via ON UPDATE CURRENT_TIMESTAMP, so these
 * are only needed for SQLite.
 */
export const sqliteTriggers: string[] = [
  `CREATE TRIGGER IF NOT EXISTS trg_ce_stage_extension_runs_update AFTER UPDATE ON ce_stage_extension_runs FOR EACH ROW BEGIN UPDATE ce_stage_extension_runs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_ce_skill_versions_update AFTER UPDATE ON ce_skill_versions FOR EACH ROW BEGIN UPDATE ce_skill_versions SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_ce_skill_audit_events_update AFTER UPDATE ON ce_skill_audit_events FOR EACH ROW BEGIN UPDATE ce_skill_audit_events SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_flow_events_update AFTER UPDATE ON flow_events FOR EACH ROW BEGIN UPDATE flow_events SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_flow_metrics_update AFTER UPDATE ON flow_metrics FOR EACH ROW BEGIN UPDATE flow_metrics SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_triggered_alerts_update AFTER UPDATE ON triggered_alerts FOR EACH ROW BEGIN UPDATE triggered_alerts SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_node_executions_update AFTER UPDATE ON node_executions FOR EACH ROW BEGIN UPDATE node_executions SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_flow_runs_update AFTER UPDATE ON flow_runs FOR EACH ROW BEGIN UPDATE flow_runs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_scheduled_triggers_update AFTER UPDATE ON scheduled_triggers FOR EACH ROW BEGIN UPDATE scheduled_triggers SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_webhook_triggers_update AFTER UPDATE ON webhook_triggers FOR EACH ROW BEGIN UPDATE webhook_triggers SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_webhook_events_update AFTER UPDATE ON webhook_events FOR EACH ROW BEGIN UPDATE webhook_events SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_workflow_specs_update AFTER UPDATE ON workflow_specs FOR EACH ROW BEGIN UPDATE workflow_specs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_schema_version_update AFTER UPDATE ON schema_version FOR EACH ROW BEGIN UPDATE schema_version SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_knowledge_bases_update AFTER UPDATE ON knowledge_bases FOR EACH ROW BEGIN UPDATE knowledge_bases SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_validation_templates_update AFTER UPDATE ON validation_templates FOR EACH ROW BEGIN UPDATE validation_templates SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_facade_bindings_update AFTER UPDATE ON facade_bindings FOR EACH ROW BEGIN UPDATE facade_bindings SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_approval_cards_update AFTER UPDATE ON approval_cards FOR EACH ROW BEGIN UPDATE approval_cards SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_node_step_traces_update AFTER UPDATE ON node_step_traces FOR EACH ROW BEGIN UPDATE node_step_traces SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_bot_workflow_permissions_update AFTER UPDATE ON bot_workflow_permissions FOR EACH ROW BEGIN UPDATE bot_workflow_permissions SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_flow_control_slots_update AFTER UPDATE ON flow_control_slots FOR EACH ROW BEGIN UPDATE flow_control_slots SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_flow_control_queue_update AFTER UPDATE ON flow_control_queue FOR EACH ROW BEGIN UPDATE flow_control_queue SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_cm_bench_templates_update AFTER UPDATE ON cm_bench_templates FOR EACH ROW BEGIN UPDATE cm_bench_templates SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_cm_bench_runs_update AFTER UPDATE ON cm_bench_runs FOR EACH ROW BEGIN UPDATE cm_bench_runs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_cm_bench_task_results_update AFTER UPDATE ON cm_bench_task_results FOR EACH ROW BEGIN UPDATE cm_bench_task_results SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_cm_bench_domains_update AFTER UPDATE ON cm_bench_domains FOR EACH ROW BEGIN UPDATE cm_bench_domains SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_cm_bench_template_versions_update AFTER UPDATE ON cm_bench_template_versions FOR EACH ROW BEGIN UPDATE cm_bench_template_versions SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_cm_bench_artifacts_update AFTER UPDATE ON cm_bench_artifacts FOR EACH ROW BEGIN UPDATE cm_bench_artifacts SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_workflow_notification_configs_update AFTER UPDATE ON workflow_notification_configs FOR EACH ROW BEGIN UPDATE workflow_notification_configs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dev_workflow_templates_update AFTER UPDATE ON dev_workflow_templates FOR EACH ROW BEGIN UPDATE dev_workflow_templates SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dev_workflows_update AFTER UPDATE ON dev_workflows FOR EACH ROW BEGIN UPDATE dev_workflows SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dev_workflow_phases_update AFTER UPDATE ON dev_workflow_phases FOR EACH ROW BEGIN UPDATE dev_workflow_phases SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dima_work_items_update AFTER UPDATE ON dima_work_items FOR EACH ROW BEGIN UPDATE dima_work_items SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_conversation_logs_update AFTER UPDATE ON conversation_logs FOR EACH ROW BEGIN UPDATE conversation_logs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_log_analysis_results_update AFTER UPDATE ON log_analysis_results FOR EACH ROW BEGIN UPDATE log_analysis_results SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_pending_operations_update AFTER UPDATE ON pending_operations FOR EACH ROW BEGIN UPDATE pending_operations SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dev_git_ops_update AFTER UPDATE ON dev_git_ops FOR EACH ROW BEGIN UPDATE dev_git_ops SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dev_artifacts_update AFTER UPDATE ON dev_artifacts FOR EACH ROW BEGIN UPDATE dev_artifacts SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  // dev_code_changes and dev_code_change_files triggers removed (tables dropped in v58)
  `CREATE TRIGGER IF NOT EXISTS trg_dev_phase_conversations_update AFTER UPDATE ON dev_phase_conversations FOR EACH ROW BEGIN UPDATE dev_phase_conversations SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dev_approvals_update AFTER UPDATE ON dev_approvals FOR EACH ROW BEGIN UPDATE dev_approvals SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dev_approval_reviewers_update AFTER UPDATE ON dev_approval_reviewers FOR EACH ROW BEGIN UPDATE dev_approval_reviewers SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  // dev_approval_decisions trigger removed (table dropped in v57)
  `CREATE TRIGGER IF NOT EXISTS trg_dev_discussions_update AFTER UPDATE ON dev_discussions FOR EACH ROW BEGIN UPDATE dev_discussions SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dev_discussion_replies_update AFTER UPDATE ON dev_discussion_replies FOR EACH ROW BEGIN UPDATE dev_discussion_replies SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_dev_project_constraints_update AFTER UPDATE ON dev_project_constraints FOR EACH ROW BEGIN UPDATE dev_project_constraints SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_insight_failure_task_update AFTER UPDATE ON insight_failure_task FOR EACH ROW BEGIN UPDATE insight_failure_task SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_insight_metric_daily_update AFTER UPDATE ON insight_metric_daily FOR EACH ROW BEGIN UPDATE insight_metric_daily SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_insight_improvement_evidence_update AFTER UPDATE ON insight_improvement_evidence FOR EACH ROW BEGIN UPDATE insight_improvement_evidence SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_insight_improvement_evolve_link_update AFTER UPDATE ON insight_improvement_evolve_link FOR EACH ROW BEGIN UPDATE insight_improvement_evolve_link SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_ce_task_sources_update AFTER UPDATE ON ce_task_sources FOR EACH ROW BEGIN UPDATE ce_task_sources SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_insight_governance_rule_trust_update AFTER UPDATE ON insight_governance_rule_trust FOR EACH ROW BEGIN UPDATE insight_governance_rule_trust SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_insight_auto_repair_grant_update AFTER UPDATE ON insight_auto_repair_grant FOR EACH ROW BEGIN UPDATE insight_auto_repair_grant SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_ce_repair_tool_calls_update AFTER UPDATE ON ce_repair_tool_calls FOR EACH ROW BEGIN UPDATE ce_repair_tool_calls SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_flow_retry_requests_update AFTER UPDATE ON flow_retry_requests FOR EACH ROW BEGIN UPDATE flow_retry_requests SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  `CREATE TRIGGER IF NOT EXISTS trg_workflow_deploy_history_update AFTER UPDATE ON workflow_deploy_history FOR EACH ROW BEGIN UPDATE workflow_deploy_history SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
];

/**
 * All migration definitions, in order.
 * Each migration uses SQLite-compatible SQL as the canonical form.
 * For MySQL/ZDAS: each Dialect's `renderDdl` converts types.
 * For SQLite: triggers for gmt_modified auto-update are in sqliteTriggers.
 *
 * Note: Indexed columns use VARCHAR(255) instead of TEXT because
 * OceanBase/MySQL cannot index TEXT columns. SQLite treats VARCHAR
 * identically to TEXT so this is fully compatible.
 */
export const migrations: readonly Migration[] = [
  ...migrationsV1To36,
  ...migrationsV37To74,
  ...migrationsV75To108,
  ...migrationsV109To143,
  ...repairDeployTagMigrations,
  ...flowRunMarkersMigrations,
  ...workflowAiDraftMigrations,
];
