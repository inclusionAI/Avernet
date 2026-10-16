import { afterEach, expect, it, vi } from 'vitest';
import { createRunAnalysisStarter, RunAnalysisStartError } from '../run-analysis-starter.js';
import { WorkflowEvolutionRepository } from '../../../repositories/workflow-evolution-repository.js';
import type { EvolveRepository } from '../../../repositories/evolve-repository.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';

afterEach(() => vi.restoreAllMocks());

it('rechecks automatic authority after persisting the request and before any external dispatch', async () => {
  const createAnalysis = vi.spyOn(WorkflowEvolutionRepository.prototype, 'createAnalysisRun').mockResolvedValue({} as never);
  const failAnalysis = vi.spyOn(WorkflowEvolutionRepository.prototype, 'failAnalysisRun').mockResolvedValue({} as never);
  const persist = vi.fn().mockResolvedValue(undefined);
  const dispatch = vi.fn();
  const repo = {
    getWorkflowIdByFlowId: vi.fn().mockResolvedValue('wf-1'),
    findRunningRunAnalysisTask: vi.fn().mockResolvedValue(null),
    getFlowRun: vi.fn().mockResolvedValue({ origin_bot_id: 'bot-1:owner' }),
    listEligibleBotsForAnalyze: vi.fn().mockResolvedValue([{ botId: 'bot-1' }]),
    resolveEvolveBotRuntime: vi.fn().mockResolvedValue({ activeEngine: 'openclaw' }),
    startFlowAnalysis: vi.fn().mockResolvedValue(true), createTaskWithStep: persist,
    updateStepStatus: vi.fn().mockResolvedValue(undefined), failFlowAnalysis: vi.fn().mockResolvedValue(undefined),
  };
  const beforeDispatch = vi.fn(async () => {
    expect(createAnalysis).toHaveBeenCalledOnce();
    expect(persist).toHaveBeenCalledOnce();
    throw new RunAnalysisStartError(403, 'AUTOMATION_AUTHORITY_CHANGED', 'Policy disabled');
  });
  const starter = createRunAnalysisStarter({ repo: repo as unknown as EvolveRepository, db: {} as IDatabase,
    dispatch, beforeDispatch, publicBaseUrl: 'https://example.com' });
  await expect(starter.start({ flowId: 'flow-1', userId: 'owner', automatic: {
    workflowId: 'wf-1', policyVersion: 1, requestId: 'request-1', dispatchToken: 'lease-1',
  } })).rejects.toMatchObject({ statusCode: 403, code: 'AUTOMATION_AUTHORITY_CHANGED' });
  expect(dispatch).not.toHaveBeenCalled();
  expect(repo.updateStepStatus).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ errorCode: 'AUTOMATION_AUTHORITY_CHANGED' }));
  expect(failAnalysis).toHaveBeenCalledWith(expect.any(String), 'AUTOMATION_AUTHORITY_CHANGED', expect.any(Number));
});
