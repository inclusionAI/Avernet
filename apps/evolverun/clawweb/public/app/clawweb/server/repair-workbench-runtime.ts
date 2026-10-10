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
import { createRepairBatchesRouter } from '@avernet/workflow/server/routes/repair-batches';
import { createRepairAuthorizer, type RepairPrincipalResolver } from '@avernet/workflow/server/routes/repair-authorization';
import { RepairBatchError } from '@avernet/workflow/server/contracts/repair-batch';
import type { RepairExecutionPort } from '@avernet/workflow/server/contracts/repair-workbench';

export function createWorkflowRepairRuntime(db: IDatabase, options: {
  principal: RepairPrincipalResolver;
  execution?: RepairExecutionPort;
}): { service: ReturnType<typeof createRepairWorkbenchService>; router: Router } {
  const sources = createRepairSourcePort({
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
  sources.readVersion = async (tx, workflowId) => {
    // No JSON bodies: independently committed analysis, legacy sources and human
    // dispositions all invalidate the bounded read projection on every instance.
    const tables = [
      ['workflow_evolution_analysis_runs', 'state_version', 'requested_at_ms'],
      ['workflow_healing_diagnoses', 'id', 'gmt_modified'],
      ['workflow_healing_suggestions', 'id', 'gmt_modified'],
      ['workflow_repair_items', 'state_version', 'updated_at_ms'],
    ];
    const versions = [];
    for (const [table, version, updated] of tables) {
      versions.push(await tx.query(`SELECT COUNT(*) AS n, COALESCE(SUM(${version}), 0) AS v, MAX(${updated}) AS t
        FROM ${table} WHERE workflow_id = ?`, [workflowId]));
    }
    return JSON.stringify(versions);
  };
  const service = createRepairWorkbenchService(db, sources, options.execution);
  const authorize = createRepairAuthorizer(options.principal, new BotWorkflowPermissionRepository(db));
  return { service, router: createRepairBatchesRouter({ service, authorize }) };
}
