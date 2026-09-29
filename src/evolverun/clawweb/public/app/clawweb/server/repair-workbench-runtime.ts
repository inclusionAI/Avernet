/** Shared composition root for the public and internal ClawWeb Hosts.
 * Workflow owns control-plane rules; old repositories supply read-only analysis sources.
 * The Host supplies verified identity and, independently, its AIS execution adapter.
 */
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { Router } from 'express';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { EvolveRepository } from '@avernet/clawevolve/server/repositories/evolve-repository';
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
    groups: (tx, workflowId) => new IssueAggregationRepository(tx).listSources(workflowId),
    suggestions: async (tx, workflowId) => {
      const repo = new EvolveRepository(tx);
      const rows: RepairSuggestionSource[] = [];
      for (;;) {
        const page = await repo.listSuggestions({ workflowId, limit: 200, offset: rows.length });
        rows.push(...page.rows);
        if (rows.length >= page.total) return rows;
        // An explicit operational limit/error, never an apparently complete first page.
        if (!page.rows.length || rows.length >= 10_000) throw new RepairBatchError('PAYLOAD_TOO_LARGE', 'Too many repair sources; narrow the workflow history before retrying');
      }
    },
    evidence: (tx, _workflowId, eventIds) => new WorkflowEvolutionRepository(tx).listEvidenceByEventIds(eventIds),
  });
  const service = createRepairWorkbenchService(db, sources, options.execution);
  const authorize = createRepairAuthorizer(options.principal, new BotWorkflowPermissionRepository(db));
  return { service, router: createRepairBatchesRouter({ service, authorize }) };
}
