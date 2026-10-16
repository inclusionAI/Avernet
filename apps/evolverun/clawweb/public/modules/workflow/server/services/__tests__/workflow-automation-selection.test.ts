// @vitest-environment node
import { expect, it } from 'vitest';
import { selectAutomaticScope, previousShanghaiDay } from '../workflow-automation-selection.js';
import { repairFixture } from './repair-batch-fixtures.js';
import { RepairBatchError } from '../../contracts/repair-batch.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { IssueAggregationRepository } from '@avernet/clawevolve/server/repositories/issue-aggregation-repository';

it('freezes previous Shanghai natural day independent of dispatch hour', () => {
  expect(previousShanghaiDay(Date.parse('2026-10-16T10:00:00Z'))).toEqual({ fromMs: Date.parse('2026-10-14T16:00:00Z'), untilMs: Date.parse('2026-10-15T16:00:00Z') });
});
it('enumerates over 20 issues and 100 candidates, retains all >5 eligible and bounded batches', async () => {
  const f = await repairFixture(225);
  try {
    const cutoff = Date.parse('2026-10-16T02:00:00Z');
    f.raw.exec('CREATE TABLE flow_runs (workflow_id TEXT, flow_id TEXT, started_at INTEGER, completed_at INTEGER)');
    const base = await f.executionPort.resolveBaseline('wf-1');
    for (const [i, item] of f.items.entries()) {
      item.proposal = { schemaVersion: 'workflow-patch/v1', workflowId: 'wf-1', baseSpecDigest: base.specDigest, summary: 'fix', operations: [{ op: 'replace', nodeId: `n-${i}`, path: '/executor/timeoutSeconds', value: 60 }] };
      item.context = { signature: `issue-${i}`, diagnoses: [{ flowId: `flow-${i}`, severity: 'medium', completedAtMs: cutoff - 1 }] };
      f.raw.prepare('INSERT INTO flow_runs VALUES (?, ?, ?, ?)').run('wf-1', `flow-${i}`, Date.parse('2026-10-15T00:00:00Z') / 1000, Date.parse('2026-10-15T01:00:00Z') / 1000);
    }
    const workbench = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
    const read = workbench.candidates.bind(workbench);
    workbench.candidates = async (id, query) => { if ((query?.pageSize ?? 20) > 50) throw new RepairBatchError('PAYLOAD_TOO_LARGE', 'controlled response budget'); return read(id, query); };
    const scope = await selectAutomaticScope(workbench, new IssueAggregationRepository(f.db), 'wf-1', base, { db: f.db, minimumImpact: 'medium', evidenceCutoffMs: cutoff });
    expect(scope!.items).toHaveLength(225);
    expect(scope!.batches!.flat()).toHaveLength(225);
    expect(scope!.batches!.every(batch => batch.length <= 100)).toBe(true);
    // Yesterday low severity must not borrow a global issue severity; future evidence excluded.
    f.items[0].context!.diagnoses = [{ flowId: 'flow-0', severity: 'low', completedAtMs: cutoff - 1 }];
    f.items[1].context!.diagnoses = [{ flowId: 'flow-1', severity: 'high', completedAtMs: cutoff + 1 }];
    f.items[2].context!.diagnoses = [{ flowId: 'flow-2', severity: null, completedAtMs: cutoff - 1 }];
    f.raw.prepare('UPDATE flow_runs SET started_at = ? WHERE flow_id = ?').run(Date.parse('2026-10-16T00:00:00Z') / 1000, 'flow-3');
    const medium = await selectAutomaticScope(workbench, new IssueAggregationRepository(f.db), 'wf-1', base, { db: f.db, minimumImpact: 'medium', evidenceCutoffMs: cutoff });
    expect(medium!.items).toHaveLength(221);
    expect(medium!.deferred!['item-3']).toBe('RUN_TIME_NOT_IN_SCOPE');
    const high = await selectAutomaticScope(workbench, new IssueAggregationRepository(f.db), 'wf-1', base, { db: f.db, minimumImpact: 'high', evidenceCutoffMs: cutoff });
    expect(high!.items).toHaveLength(0);
    expect(Object.keys(high!.deferred!)).toHaveLength(225);
  } finally { await f.db.close(); }
});

it('retains an explainable empty scope when the pending inbox is empty', async () => {
  const f = await repairFixture(0);
  try {
    const workbench = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
    const result = await selectAutomaticScope(workbench, new IssueAggregationRepository(f.db), 'wf-1', await f.executionPort.resolveBaseline('wf-1'),
      { db: f.db, minimumImpact: 'medium', evidenceCutoffMs: Date.parse('2026-10-16T02:00:00Z') });
    expect(result).toMatchObject({ items: [], batches: [], deferred: {} });
  } finally { await f.db.close(); }
});

async function legacyPlanFixture(oversized = false) {
  const f = await repairFixture(7);
  const baseline = await f.executionPort.resolveBaseline('wf-1');
  for (const [i,item] of f.items.entries()) {
    item.proposal = { schemaVersion:'workflow-patch/v1', workflowId:'wf-1', baseSpecDigest:baseline.specDigest,
      summary:`fix-${i}`, operations:[{op:'replace',nodeId:i<6?'query':'other',path:'/executor/prompt',value:`goal-${i}`}] };
    item.context = {signature:'issue',diagnoses:[{flowId:`run-${i}`,severity:'high'}], ...(oversized && i<6 ? {details:'x'.repeat(19000)} : {})};
  }
  const workbench = createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort);
  const page = await workbench.candidates('wf-1',{state:'pending',pageSize:100});
  const originals = page.items.slice(0,6);
  const card = {...originals[0],aggregation:{id:'six-member-plan',fingerprint:'plan-version',itemIds:originals.map(item=>item.itemId),
    title:'Integrate six goals',changes:['preserve six goals'],relation:'complementary' as const,status:'ready' as const,
    originals,flowIds:originals.map((_item,i)=>`run-${i}`),conflicts:[]}};
  workbench.candidates = async()=>({...page,items:[card,page.items[6]]});
  const issues = {listPage:async()=>({groups:[{signature:'issue',priority:{severity:'high'}}]})} as unknown as IssueAggregationRepository;
  return {f,workbench,issues,baseline};
}

it('keeps a six-original ready plan whole in legacy automatic selection',async()=>{
  const {f,workbench,issues,baseline}=await legacyPlanFixture();
  try {
    const selected=await selectAutomaticScope(workbench,issues,'wf-1',baseline);
    expect(selected?.items.filter(item=>item.aggregation?.id==='six-member-plan').map(item=>item.itemId))
      .toEqual(['item-0','item-1','item-2','item-3','item-4','item-5']);
    expect(selected?.items.map(item=>item.itemId)).toContain('item-6');
  } finally {await f.db.close();}
});

it('defers an over-budget plan as a whole and still selects another fitting legacy unit',async()=>{
  const {f,workbench,issues,baseline}=await legacyPlanFixture(true);
  try {
    const selected=await selectAutomaticScope(workbench,issues,'wf-1',baseline);
    expect(selected?.items.map(item=>item.itemId)).toEqual(['item-6']);
  } finally {await f.db.close();}
});

it('keeps the legacy five-choice limit for independent original suggestions',async()=>{
  const {f,workbench,issues,baseline}=await legacyPlanFixture();
  try {
    const page=await workbench.candidates('wf-1');
    const originals=[...page.items[0].aggregation!.originals,page.items[1]].map((item,i)=>({...item,
      proposal:{...item.proposal,operations:[{op:'replace',nodeId:`separate-${i}`,path:'/executor/prompt',value:`goal-${i}`}]}}));
    workbench.candidates=async()=>({...page,items:originals});
    const selected=await selectAutomaticScope(workbench,issues,'wf-1',baseline);
    expect(selected?.items.map(item=>item.itemId)).toEqual(['item-0','item-1','item-2','item-3','item-4']);
  } finally {await f.db.close();}
});

it('does not select the remaining members of a partly unavailable legacy plan',async()=>{
  const {f,workbench,issues,baseline}=await legacyPlanFixture();
  try {
    const page=await workbench.candidates('wf-1');
    page.items[0].aggregation!.originals[5].sourceAvailable=false;
    const selected=await selectAutomaticScope(workbench,issues,'wf-1',baseline);
    expect(selected?.items.map(item=>item.itemId)).toEqual(['item-6']);
  } finally {await f.db.close();}
});
