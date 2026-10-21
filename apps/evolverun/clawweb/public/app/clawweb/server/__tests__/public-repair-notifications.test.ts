import { afterEach, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { WorkflowAutomationRepository } from '@avernet/workflow/server/repositories/workflow-automation-repository';
import type { ClawWebLifecycle } from '../bootstrap.js';
const state = vi.hoisted(() => ({ db: null as IDatabase | null,
  send: vi.fn(async (_input: {recipientUserId: string; title: string; text: string}) => ({ processQueryKey: 'controlled-receipt' })) }));
vi.mock('@avernet/clawweb-shared/server/db', async importOriginal => ({
  ...await importOriginal<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase: async () => ({db: state.db}), closeDatabase: vi.fn(),
}));
vi.mock('@avernet/clawevolve', async () => {
  const {Router} = await import('express');
  return {createClawevolveModule: () => ({start: async()=>{}, stop: async()=>{}, publicRouter: Router(), benchRouter: Router()})};
});
vi.mock('@avernet/clawinsight', async () => {
  const {Router} = await import('express');
  return {createInsightRuntime: () => ({service: {}, notificationSender: {sendNotification: state.send}}),
    createInsightRouter: () => Router(), createSessionRecoveryRouter: () => Router()};
});
// No AIS, automatic-analysis starter or real notification transport is available.
vi.mock('../repair-ais-execution.js', () => ({resolveRepairAisConfiguration: async()=>null}));
import {createClawWebBootstrap} from '../public-bootstrap.js';
let lifecycle: ClawWebLifecycle | void;
afterEach(async()=>{
  await lifecycle?.stop?.(); lifecycle=undefined;
  if(state.db)await state.db.close();state.db=null;
  vi.restoreAllMocks();vi.unstubAllEnvs();state.send.mockReset();
});
it('drains a manual completion through the public bootstrap timer without AIS or automatic-analysis configuration', async()=>{
  const raw=new Database(':memory:');const db=new SqliteDatabase(raw);state.db=db;
  await runMigrations(db,'sqlite');
  raw.exec("INSERT INTO workflow_specs (workflow_id,spec_json,owner_id) VALUES ('wf','{}','334018'); INSERT INTO bot_workflow_permissions (bot_owner_id,workflow_id,can_edit) VALUES ('334018','wf',1)");
  const repo=new WorkflowAutomationRepository(db);
  const job=await repo.ensure('wf','auto_notice','manual-completion',{kind:'completion',title:'优化草稿已可审阅',text:'请审阅草稿',path:'/workflows/workspace?workflowId=wf&repairTaskId=task'},0);
  const polls: Array<()=>unknown>=[];const setIntervalOriginal=globalThis.setInterval;
  vi.spyOn(globalThis,'setInterval').mockImplementation(((callback:()=>unknown,delay:number)=>{
    if(delay===15000)polls.push(callback);return setIntervalOriginal(callback,delay);
  }) as typeof setInterval);
  vi.stubEnv('PORT','0');vi.stubEnv('CLAWWEB_PUBLIC_BASE_URL','http://127.0.0.1:5189');
  lifecycle=await createClawWebBootstrap().start({environment:'prod',profile:'public',config:{},configSources:[]});
  expect(polls).toHaveLength(1);
  await polls[0]();
  await vi.waitFor(async()=>expect((await repo.get(job.id))?.status).toBe('done'));
  expect(state.send).toHaveBeenCalledOnce();
  expect(state.send).toHaveBeenCalledWith({recipientUserId:'334018',title:'优化草稿已可审阅',text:'请审阅草稿\n\n[查看工作流并处理](http://127.0.0.1:5189/workflows/workspace?workflowId=wf&repairTaskId=task)'});
  await polls[0]();await vi.waitFor(()=>expect(state.send).toHaveBeenCalledOnce());
  const clearing=vi.spyOn(globalThis,'clearInterval');
  await lifecycle?.stop?.();lifecycle=undefined;
  expect(clearing).toHaveBeenCalledOnce();
});
