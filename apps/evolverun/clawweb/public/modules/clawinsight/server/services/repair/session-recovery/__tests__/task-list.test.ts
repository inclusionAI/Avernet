import Database from 'better-sqlite3';
import { afterAll, beforeAll, expect, it, vi } from 'vitest';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { EvolveRepository } from '@avernet/clawevolve/server/repositories/evolve-repository';
import { SessionRecoveryTaskRepository } from '../../../../repositories/repair/session-recovery-task-repository.js';
import { parseRecoveryTaskListQuery } from '../task-list.js';
import type { RecoveryStoredTask } from '../task-contracts.js';
const db = new SqliteDatabase(new Database(':memory:'));
const objects = new Map<string, Buffer>();
const repo = new SessionRecoveryTaskRepository(db, new EvolveRepository(db), {
  putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
  getObject: async key => ({ content: objects.get(key)!, etag: null, contentType: 'application/json' }),
});
beforeAll(async () => {
  await runMigrations(db, 'sqlite');
  for (let i = 0; i < 4; i++) {
    const task: RecoveryStoredTask = { schemaVersion: 'session-recovery-task/v1', taskId: `SR-${i}`, version: 1, level: 'approval', controlPlaneEnvironment: 'pre',
      request: { event_id: `event-${i}`, delivery_key: `key-${i}`, diagnosis: '任务尚未完成', tc_fault_label: null, session_key: 'original',
        target: { bot_id: 'default', entity_id: i === 3 ? 'other' : 'owner', env: i === 2 ? 'pre' : 'prod', engine: 'OC' } },
      status: i === 1 ? 'running' : 'completed', phase: 'verify', plan: null, judgment: { effect: 'unknown', summary: '效果待确认', delivery: 'unconfirmed', response: 'unknown', evidence_ids: [] },
      ...(i === 0 ? { completionReason: 'user_continued' as const } : {}),
      timeline: i === 0 ? [
        { event_id: 'started', at_ms: 10, kind: 'execution', actor: 'service', summary: '启动自愈 Agent', payload: { execution_id: 'execution' } },
        { event_id: 'assessment', at_ms: 15, kind: 'assessment', actor: 'agent', summary: '## 原任务目标\n查询原文\n\n## 当前阻塞\n工具无法调用', payload: {} },
        { event_id: 'continued', at_ms: 20, kind: 'termination', actor: 'agent', summary: '用户继续对话', payload: { completion_reason: 'user_continued' } },
        { event_id: 'notification', at_ms: 30, kind: 'notification', actor: 'service', summary: '发送通知', payload: {} },
      ] : [], evidence: [], observationDeadlineAt: null, ownerDeadlineAt: null };
    await repo.create(task, `${task.taskId}-1`);
  }
}, 30000);
afterAll(async () => db.close());
it('pages only owned tasks, filters Bot environment/status and keeps completion separate from effect', async () => {
  const hydrate = vi.spyOn(repo, 'find');
  expect(await repo.listTasks(parseRecoveryTaskListQuery({ pageSize: 1 }), 'owner')).toMatchObject({ total: 3, pageSize: 1, items: [{ taskId: 'SR-2' }] });
  const page = await repo.listTasks(parseRecoveryTaskListQuery({ botId: 'default', env: 'prod', status: 'completed' }), 'owner');
  expect(page).toMatchObject({ total: 1, items: [{ taskId: 'SR-0', status: 'completed', effect: 'unknown' }] });
  expect((await repo.listTasks(parseRecoveryTaskListQuery({}), 'unrelated')).total).toBe(0);
  expect(hydrate).not.toHaveBeenCalled();
});
it('optionally projects the recorded goal, start and terminal fact without loading task details', async () => {
  const hydrate = vi.spyOn(repo, 'find');
  const base = { botId: 'default', env: 'prod', status: 'completed' };
  const original = await repo.listTasks(parseRecoveryTaskListQuery(base), 'owner');
  expect(original.items[0]).not.toHaveProperty('overview');
  const presented = await repo.listTasks(parseRecoveryTaskListQuery({ ...base, includePresentation: 'true' }), 'owner');
  expect(presented.items[0]).toMatchObject({ overview: '查询原文', startAtMs: 10, endAtMs: 20, completionReason: 'user_continued' });
  expect(hydrate).not.toHaveBeenCalled();
});
it.each([{ pageSize: 500 }, { page: -1 }, { status: 'recovered' }, { ownerId: 'other' }, { env: '*' }])('rejects invalid list queries and owner spoofing: %j', query => {
  expect(() => parseRecoveryTaskListQuery(query)).toThrow();
});
