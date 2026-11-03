import { WorkflowSnapshotRepository } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import { logWorkflowBackground, withWorkflowBackgroundContext } from '@avernet/clawweb-shared/server/services/workflow-background-log';
import { repairSourceSnapshot } from '@avernet/workflow/server/services/repair-source-snapshot';
import type { ProposalModelPort } from '@avernet/workflow/server/services/repair-proposal-integration';
import { createRepairTrialService } from '@avernet/workflow/server/services/repair-trial-service';
import type { RepairTrialPort } from '@avernet/workflow/server/contracts/repair-trial';
/** Shared composition root for the public and internal ClawWeb Hosts.
 * Workflow owns control-plane rules; old repositories supply read-only analysis sources.
 * The Host supplies verified identity and, independently, its AIS execution adapter.
 */
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { Router } from 'express';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { IssueAggregationRepository } from '@avernet/clawevolve/server/repositories/issue-aggregation-repository';
import { WorkflowEvolutionRepository } from '@avernet/clawevolve/server/repositories/workflow-evolution-repository';
import { createRepairSourcePort, type RepairSuggestionSource } from '@avernet/workflow/server/repositories/repair-source-adapter';
import { createRepairWorkbenchService } from '@avernet/workflow/server/services/repair-batch-service';
import { queueRepairCompletion } from '@avernet/workflow/server/services/repair-completion-notification';
import { createWorkflowNotificationDrain } from '@avernet/workflow/server/services/workflow-notification-delivery';
import { createRepairBatchesRouter } from '@avernet/workflow/server/routes/repair-batches';
import { createRepairAuthorizer, type RepairPrincipalResolver } from '@avernet/workflow/server/routes/repair-authorization';
import { RepairBatchError } from '@avernet/workflow/server/contracts/repair-batch';
import type { RepairExecutionPort } from '@avernet/workflow/server/contracts/repair-workbench';
import type { RepairPublicationPort } from '@avernet/workflow/server/contracts/repair-release';
import { createRepairReleaseService } from '@avernet/workflow/server/services/repair-release-service';
import { createRepairVerificationService } from '@avernet/workflow/server/services/repair-verification-service';
import { createWorkflowAutomation } from '@avernet/workflow/server/services/workflow-automation-service';
import { RepairAutomationDeniedError, type WorkflowAutomationPorts, type WorkflowAutomationService } from '@avernet/workflow/server/contracts/workflow-automation';
import type { AutoAnalysisSettings } from '@avernet/workflow/server/services/task-guard/auto-analysis-settings';

export function createWorkflowRepairRuntime(db: IDatabase, options: {
  principal: RepairPrincipalResolver;
  environment?: string;
  /** @deprecated Retained for host compatibility; suggestion reads no longer run an AI coordinator. */
  proposalModel?: ProposalModelPort;
  execution?: RepairExecutionPort;
  publication?: RepairPublicationPort;
  trial?: RepairTrialPort;
  automation?: { settings: AutoAnalysisSettings; ports: WorkflowAutomationPorts; now?: () => number };
  /** Notification-only Hosts do not need to enable automatic analysis or draft scheduling. */
  notifications?: Omit<Parameters<typeof createWorkflowNotificationDrain>[0], 'db' | 'workbench'>;
}): { service: ReturnType<typeof createRepairWorkbenchService>; release: ReturnType<typeof createRepairReleaseService>; trials: ReturnType<typeof createRepairTrialService>; router: Router; automation: WorkflowAutomationService | undefined; monitor: { reconcile(): Promise<number> } } {
  const sources = createRepairSourcePort({
    snapshot: repairSourceSnapshot,
    snapshotRuns: (tx, flowIds) => new WorkflowSnapshotRepository(tx).forRuns(flowIds),
    groups: (tx, workflowId, sourceOptions) => new IssueAggregationRepository(tx).listSources(workflowId, sourceOptions),
    suggestions: async (tx, workflowId, sourceOptions) => {
      const rows: RepairSuggestionSource[] = [];
      let afterId = 0;
      for (;;) {
        const params: unknown[] = [workflowId];
        const recent = sourceOptions?.sinceMs === undefined ? ''
          : " AND (gmt_modified >= ? OR status IN ('applying', 'running', 'dispatching', 'dispatched', 'applied', 'applied_unverified'))";
        if (sourceOptions?.sinceMs !== undefined) params.push(tx.dialect.epochToDb(Math.floor(sourceOptions.sinceMs / 1000)));
        params.push(afterId);
        const page = await tx.query<RepairSuggestionSource>(`SELECT * FROM workflow_healing_suggestions
          WHERE workflow_id = ?${recent} AND id > ? ORDER BY id ASC LIMIT 200`, params);
        if (!page.length) return rows;
        rows.push(...page);
        // An explicit operational limit/error, never an apparently complete first page.
        if (rows.length >= 10_000) throw new RepairBatchError('PAYLOAD_TOO_LARGE', 'Too many repair sources; narrow the workflow history before retrying');
        afterId = Number(page.at(-1)!.id);
      }
    },
    evidence: (tx, _workflowId, eventIds) => new WorkflowEvolutionRepository(tx).listEvidenceByEventIds(eventIds),
  });
  sources.activeSnapshot = (tx, workflowId) => new WorkflowSnapshotRepository(tx).active(workflowId);
  sources.readVersion = async (tx, workflowId) => {
    // No JSON bodies: independently committed analysis, legacy sources and human
    // dispositions all invalidate the bounded read projection on every instance.
    const tables = [
      ['workflow_evolution_analysis_runs', 'state_version', 'requested_at_ms'],
      ['workflow_healing_diagnoses', 'id', 'gmt_modified'],
      ['workflow_healing_suggestions', 'id', 'gmt_modified'],
      ['workflow_repair_items', 'state_version', 'updated_at_ms'],
      ['workflow_deploy_history', 'is_active', 'deploy_number'],
    ];
    const versions = [];
    for (const [table, version, updated] of tables) {
      versions.push(await tx.query(`SELECT COUNT(*) AS n, COALESCE(SUM(${version}), 0) AS v, MAX(${updated}) AS t
        FROM ${table} WHERE workflow_id = ?`, [workflowId]));
    }
    return JSON.stringify(versions);
  };
  let automation: WorkflowAutomationService | undefined;
  const execution = options.execution ? { ...options.execution, dispatch: async (request: Parameters<RepairExecutionPort['dispatch']>[0]) => {
    if (request.input.automation) {
      if (!automation) throw new RepairAutomationDeniedError('AUTOMATION_UNAVAILABLE');
      await automation.authorizeDraft({ workflowId: request.input.baseline.workflowId, actorId: request.actorId, taskId: request.input.taskId,
        origin: request.input.automation, itemIds: request.input.items.map(item => item.itemId) });
    }
    return options.execution!.dispatch(request);
  } } : undefined;
  const service = createRepairWorkbenchService(db, sources, execution, undefined, queueRepairCompletion);
  if (options.automation) automation = createWorkflowAutomation({ db, workbench: service, ...options.automation, environment: options.environment });
  const notificationOptions = options.notifications ?? options.automation;
  const drainNotifications = notificationOptions
    ? createWorkflowNotificationDrain({ db, workbench: service, ...notificationOptions, environment: options.environment }) : undefined;
  console.info('[workflow-notification] ' + JSON.stringify({ event: 'consumer_configured', environment: options.environment ?? 'prod',
    consumer: drainNotifications ? 'notification_only' : 'none', completionObserverConfigured: true }));
  const release = createRepairReleaseService({ db, workbench: service, publisher: options.publication });
  const trials = createRepairTrialService({ db, workbench: service, port: options.trial });
  const authorize = createRepairAuthorizer(options.principal, new BotWorkflowPermissionRepository(db));
  const verification = createRepairVerificationService(db, options.publication);
  logWorkflowBackground('workflow_repair_runtime', 'configured', { enabled: true, environment: options.environment });
  let lastSummaryAt = -Infinity;
  const monitor = { async reconcile() { return withWorkflowBackgroundContext({ environment: options.environment }, async () => {
    try {
      // Fast recovery owns unfinished AIS executions only. Publication/effect
      // checks and new automatic work remain explicit, separate operations.
      const count = await service.reconcile();
      if (count || Date.now() - lastSummaryAt >= 60_000) {
        logWorkflowBackground('workflow_repair_runtime', 'summary', { count });
        lastSummaryAt = Date.now();
      }
      return count;
    } finally {
      // A failed generation must not prevent independent completion notices.
      await drainNotifications?.();
    }
  }); } };
  return { service, release, trials, monitor, automation, router: createRepairBatchesRouter({ service, authorize, release, trials, verification }) };
}
