/** Synthetic local records using the production task repository, service and routes. No AIS or notification transport. */
import type { Request } from 'express';
import { runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { EvolveRepository } from '@avernet/clawevolve/server/repositories/evolve-repository';
import { SessionRecoveryTaskRepository } from '../server/repositories/repair/session-recovery-task-repository';
import { SessionRecoveryTaskService } from '../server/services/repair/session-recovery/task-service';
import { createSessionRecoveryRouter } from '../server/routes/session-recovery';
import type { RecoveryStoredTask } from '../server/services/repair/session-recovery/task-contracts';

export async function previewRecoveries(db: IDatabase, authenticated: (req: Request) => boolean) {
  await runMigrations(db, 'sqlite');
  const objects = new Map<string, Buffer>();
  const tasks = new SessionRecoveryTaskRepository(db, new EvolveRepository(db), {
    putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
    getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: 'application/json' }),
  });
  const sampleStatuses = ['canceled', 'waiting_effect', 'waiting_owner', 'running', 'failed', 'completed'] as const;
  for (let i = 0; i < 23; i++) {
    const status = sampleStatuses[i - 17] ?? 'completed';
    const effect = status === 'failed' ? 'not_recovered' : status === 'canceled' ? 'not_intervened'
      : status === 'completed' && (i === 22 || i % 2 === 1) ? 'recovered' : 'unknown';
    const task: RecoveryStoredTask = { schemaVersion: 'session-recovery-task/v1', taskId: `SR-preview-${i}`, version: 1,
      level: 'approval', controlPlaneEnvironment: 'pre', status, phase: status === 'running' ? 'observe' : status === 'waiting_owner' ? 'assess' : 'verify', observationDeadlineAt: null, ownerDeadlineAt: null,
      request: { event_id: `preview-${i}`, delivery_key: `preview-${i}`, diagnosis: '原任务执行遇到工具调用异常。', tc_fault_label: 'TC.MCP.PERMISSION',
        target: { bot_id: 'default', entity_id: '001234', env: 'prod', engine: 'OC' }, session_key: `agent:main:session:preview-${i}` },
      plan: null, judgment: { effect, summary: ({ recovered: '已核验原任务继续执行。', not_recovered: '已核验原任务未恢复。', not_intervened: '本次未执行干预。', unknown: '恢复效果尚未确认。' })[effect], delivery: 'confirmed', response: 'acted', evidence_ids: [] },
      evidence: [], timeline: [] };
    await tasks.create(task, `${task.taskId}-1`);
  }
  const unavailable = async (): Promise<never> => { throw new Error('Local preview does not execute recovery operations'); };
  const service = new SessionRecoveryTaskService({ controlPlaneEnvironment: 'pre', tasks, observationMs: 3600000,
    authorizeCreation: unavailable, agent: { start: unavailable, release: unavailable, terminate: unavailable, listExecutions: async () => [] },
    notifier: { notify: async () => false }, reporter: { report: unavailable } });
  return createSessionRecoveryRouter({ service, tasks: service, resolveActor: async req => authenticated(req) ? { userId: '001234', isAdmin: false } : null });
}
