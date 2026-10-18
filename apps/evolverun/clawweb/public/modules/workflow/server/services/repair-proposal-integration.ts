import { prepareProposalEvidence } from './repair-proposal-evidence.js';
import { needsReevaluation } from './repair-selection-review.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { canonicalRepairJson, digestRepairJson, repairEvidenceFlows, RepairBatchError, type RepairBaseline, type RepairItem } from '../contracts/repair-batch.js';
import type { RepairInboxItem } from '../contracts/repair-workbench.js';
import type { ProposalGrouping, ProposalPlan, ProposalAggregation, ProposalConflict } from '../contracts/repair-proposal.js';
import { selectionConflicts } from '../contracts/repair-selection.js';
import { WorkflowAutomationRepository, type AutomationJob } from '../repositories/workflow-automation-repository.js';

export interface ProposalModelPort {
  key: string;
  classify(input: { baseline: RepairBaseline; items: RepairInboxItem[]; signal: AbortSignal }): Promise<unknown>;
}
export type ProposalSnapshot = { items: RepairInboxItem[]; baseline: RepairBaseline };
export type ProposalCoordinator = ReturnType<typeof createProposalCoordinator>;
const norm = (path: string) => path.startsWith('/') ? path : '/' + path.replaceAll('.', '/');
function operations(item: RepairItem) {
  return (Array.isArray(item.proposal?.operations) ? item.proposal.operations : []) as Array<Record<string, unknown>>;
}
function current(item: RepairItem, baseline: RepairBaseline) {
  return !needsReevaluation(item, baseline);
}
function eligible(items: RepairInboxItem[]) { return items.filter(item => item.sourceAvailable && item.state === 'pending'); }
function scope(item: RepairInboxItem) { return String(item.context?.signature ?? item.groupKey); }
function fingerprint(items: RepairInboxItem[], baseline: RepairBaseline) {
  return digestRepairJson({ version: 'proposal-integration/v1', baseline,
    items: [...items].sort((a,b)=>a.itemId.localeCompare(b.itemId)).map(item => ({ id:item.itemId, revision:item.contentRevision,
      proposal:item.proposal, instruction:item.instruction, sources:item.sources, context:item.context??null })) });
}
function groups(items: RepairInboxItem[], baseline: RepairBaseline) {
  // Model work units, not UI groups. Include different issues so shared goals can
  // retain a many-to-many source map; only the model's validated plans group cards.
  const buckets = new Map<string, RepairInboxItem[]>();
  for (const item of eligible(items)) {
    const base=String(item.proposal?.baseSpecDigest??'unknown');
    const list=buckets.get(base)??[];list.push(item);buckets.set(base,list);
  }
  const out: Array<{key:string;items:RepairInboxItem[];fingerprint:string}>=[];
  for(const [base,list] of [...buckets].sort(([a],[b])=>a.localeCompare(b))) {
    list.sort((a,b)=>a.itemId.localeCompare(b.itemId));
    for(let offset=0;offset<list.length;offset+=100) {
      const batch=list.slice(offset,offset+100);
      out.push({key:canonicalRepairJson([base,batch[0].itemId]),items:batch,fingerprint:fingerprint(batch,baseline)});
    }
  }
  return out.sort((a,b)=>a.key.localeCompare(b.key));
}
function title(item: RepairItem) { return String(item.proposal?.summary || item.instruction || '修复建议').split('\n')[0].slice(0,96); }
function exact(items: RepairInboxItem[]): ProposalGrouping {
  const same = new Map<string, RepairInboxItem[]>();
  for(const item of items) {
    // Constraint-bearing instructions are part of exact identity; titles alone never establish equivalence.
    const key=operations(item).length ? canonicalRepairJson([item.proposal?.baseSpecDigest??null,operations(item),item.instruction]) : item.itemId;
    const list=same.get(key)??[];list.push(item);same.set(key,list);
  }
  return {plans:[...same.values()].map(list=>({itemIds:list.map(i=>i.itemId),title:title(list[0]),changes:[],relation:'exact'})),conflicts:scalarConflicts(items)};
}
function scalarConflicts(items: RepairInboxItem[]): ProposalConflict[] {
  return selectionConflicts(items).flatMap(conflict=>{
    const relevant=items.filter(i=>conflict.itemIds.includes(i.itemId));
    const matched=relevant.map(i=>operations(i).filter(op=>String(op.nodeId??'工作流')===conflict.nodeId&&typeof op.path==='string'&&norm(op.path)===conflict.path&&op.op!=='test'));
    // Prompt/body strings require semantic assessment; path overlap by itself is not business incompatibility.
    if(matched.some(ops=>!ops.length)|| /prompt|instruction|content|body/i.test(conflict.path))return [];
    if(!matched.every(ops=>ops.every(op=>op.op==='remove'||['number','boolean','string'].includes(typeof op.value))))return [];
    return [{...conflict,reason:'同一设置要求不同取值，请保留一种方案'}];
  });
}
function validate(raw:unknown, items:RepairInboxItem[]): ProposalGrouping {
  if(!raw||typeof raw!=='object')throw new Error('MODEL_RESULT_INVALID');
  const data=raw as ProposalGrouping, allowed=new Set(items.map(i=>i.itemId)),covered=new Set<string>();
  if(!Array.isArray(data.plans)||!Array.isArray(data.conflicts)||data.plans.length>items.length||data.conflicts.length>200)throw new Error('MODEL_RESULT_INVALID');
  const conflicts=[...scalarConflicts(items),...data.conflicts];
  for(const c of conflicts)if(!Array.isArray(c.itemIds)||c.itemIds.length!==2||new Set(c.itemIds).size!==2||c.itemIds.some(id=>!allowed.has(id))
    ||![c.nodeId,c.path,c.reason].every(v=>typeof v==='string'&&v.length>0&&v.length<2000))throw new Error('MODEL_CONFLICT_INVALID');
  for(const plan of data.plans){
    if(!Array.isArray(plan.itemIds)||!plan.itemIds.length||plan.itemIds.some(id=>!allowed.has(id)||covered.has(id))
      ||!['equivalent','complementary','uncertain'].includes(plan.relation)||typeof plan.title!=='string'||!plan.title.trim()||plan.title.length>120
      ||/已验证|已生效|verified|推荐/i.test(plan.title)||!Array.isArray(plan.changes)||plan.changes.length>8||plan.changes.some(t=>typeof t!=='string'||!t.trim()||t.length>400))throw new Error('MODEL_PLAN_INVALID');
    if(plan.relation==='uncertain'&&plan.itemIds.length>1||conflicts.some(c=>c.itemIds.every(id=>plan.itemIds.includes(id))))throw new Error('MODEL_HID_CONFLICT');
    for(const id of plan.itemIds)covered.add(id);
  }
  if(covered.size!==allowed.size)throw new Error('MODEL_OMITTED_SOURCE');
  const available = new Map(items.map(item => [item.itemId,new Set(((item.context?.diagnoses??[]) as Array<Record<string,any>>).flatMap(d => (Array.isArray(d.evidence)?d.evidence:[]).filter((e:any)=>!e.missing&&e.payload!=null).map((e:any)=>e.eventId)))]));
  const causes = data.causes ?? [];
  if(items.some(i=>Array.isArray(i.context?.diagnoses)&&i.context.diagnoses.length)&&(!Array.isArray(causes)||!causes.length))throw new Error('MODEL_OMITTED_CAUSES');
  if(!Array.isArray(causes)||causes.length>100||causes.some(c=>!c||typeof c.title!=='string'||!c.title.trim()||c.title.length>500||!['supported','hypothesis','unknown'].includes(c.certainty)
    ||!Array.isArray(c.itemIds)||!c.itemIds.length||c.itemIds.some(id=>!allowed.has(id))||!Array.isArray(c.evidenceIds)||c.evidenceIds.some(id=>!c.itemIds.some(itemId=>available.get(itemId)?.has(id)))))throw new Error('MODEL_CAUSE_REFERENCE_INVALID');
  if(data.unknowns!==undefined&&(!Array.isArray(data.unknowns)||data.unknowns.length>50||data.unknowns.some(t=>typeof t!=='string'||t.length>1000)))throw new Error('MODEL_UNKNOWNS_INVALID');

  return {plans:data.plans.map(p=>({...p,itemIds:[...p.itemIds].sort()})),conflicts,causes:causes.map(c=>({...c,certainty:c.certainty==='supported'&&!c.evidenceIds.length?'hypothesis':c.certainty})),unknowns:data.unknowns??[]};
}
/** Durable background projection. GETs never submit models; all inputs are read again before a result or candidate is used. */
export function createProposalCoordinator(input:{db:IDatabase;read:(workflowId:string)=>Promise<ProposalSnapshot>;model?:ProposalModelPort;now?:()=>number;random?:()=>number; scopeKey?:string; hydrate?:(workflowId:string,items:RepairInboxItem[])=>Promise<RepairInboxItem[]>}) {
  const repo=new WorkflowAutomationRepository(input.db), now=input.now??Date.now, random=input.random??Math.random;
  let ticking=false;
  const key=(fp:string)=>digestRepairJson(['proposal',fp,input.model?.key??'unconfigured',input.scopeKey??'database']);
  const get=(wf:string,fp:string)=>repo.get(digestRepairJson([wf,'proposal_group',key(fp)]));
  async function jobGroup(job:AutomationJob){
    const fresh=await input.read(job.workflowId);
    return {fresh,group:groups(fresh.items,fresh.baseline).find(g=>g.fingerprint===job.data.fingerprint)};
  }
  async function scan(root:string){
    const scan=await repo.ensure(root,'proposal_scan','backfill-v1:'+(input.scopeKey??'database'),{},now());const lease=await repo.claim(scan.id,now());if(!lease)return;
    let scannedWorkflow = '';
    try {
      const wf=(await input.db.query<{workflow_id:string}>('SELECT workflow_id FROM workflow_specs WHERE workflow_id > ? ORDER BY workflow_id LIMIT 1',[lease.data.workflowCursor??'']))[0];
      if(!wf){await repo.patch(lease,{data:{workflowCursor:'',groupCursor:''},nextAtMs:now()+15000},now(),true);return;}
      scannedWorkflow=wf.workflow_id;
      const data=await input.read(wf.workflow_id);const all=groups(data.items,data.baseline).filter(g=>g.key>(lease.data.groupCursor??''));
      for(const g of all.slice(0,20))if(g.items.length>1&&exact(g.items).plans.length>1){
        const j=await repo.ensure(wf.workflow_id,'proposal_group',key(g.fingerprint),{fingerprint:g.fingerprint,scope:g.key,scopeKey:input.scopeKey??'database'},now());
        if(j.stateVersion===0){const claim=await repo.claim(j.id,now());if(claim)await repo.patch(claim,{nextAtMs:now()+2000},now(),true);}
      }
      await repo.patch(lease,{data:all.length>20?{workflowCursor:lease.data.workflowCursor??'',groupCursor:all[19].key}:{workflowCursor:wf.workflow_id,groupCursor:''},nextAtMs:now()},now(),true);
    }catch(error){await repo.patch(lease,{status:'retryable',reason:'SOURCE_READ_UNAVAILABLE',data:{workflowCursor:scannedWorkflow||lease.data.workflowCursor||'',groupCursor:''},nextAtMs:now()+1000},now(),true);}
  }
  async function work(record:AutomationJob,root:string){
    let job=await repo.claim(record.id,now());if(!job)return;
    if(job.id!==digestRepairJson([job.workflowId,'proposal_group',key(job.data.fingerprint)])){await repo.patch(job,{status:'done',reason:'PROVIDER_CHANGED'},now(),true);return;}
    let budget:AutomationJob|null=null;
    try{
      const {fresh,group}=await jobGroup(job);
      if(!group){await repo.patch(job,{status:'done',reason:'SUPERSEDED'},now(),true);return;}
      if(!input.model){await repo.patch(job,{status:'blocked',reason:'MODEL_UNAVAILABLE'},now(),true);return;}
      const hydrated=input.hydrate?await input.hydrate(job.workflowId,group.items):group.items;
      const modelItems=prepareProposalEvidence(hydrated);
      const bytes=Buffer.byteLength(canonicalRepairJson(modelItems));
      if(group.items.length>100||bytes>180000){await repo.patch(job,{status:'blocked',reason:'SCOPE_TOO_LARGE'},now(),true);return;}
      const global=await repo.ensure(root,'proposal_budget',input.model.key,{window:now(),tokens:0,requests:0},now());
      if(global.status==='blocked'){await repo.patch(job,{status:'blocked',reason:'PROVIDER_REQUIRES_RECOVERY'},now(),true);return;}
      budget=await repo.claim(global.id,now());
      if(!budget){await repo.patch(job,{status:'waiting',nextAtMs:Math.max(now()+1000,global.nextAtMs)},now(),true);return;}
      const state=now()-Number(budget.data.window)>=60000?{window:now(),tokens:0,requests:0}:budget.data;
      const tokens=bytes+10000; // Conservative UTF-8 byte upper bound plus system/output allowance.
      if(state.requests>=20||state.tokens+tokens>200000){await repo.patch(budget,{nextAtMs:Number(state.window)+60000},now(),true);budget=null;await repo.patch(job,{status:'waiting',nextAtMs:Number(state.window)+60000},now(),true);return;}
      budget=await repo.patch(budget,{data:{...state,tokens:state.tokens+tokens,requests:state.requests+1}},now());
      job=await repo.patch(job,{status:'dispatching',attempts:job.attempts+1},now());
      const abort=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
      let result:unknown;
      try{result=await Promise.race([input.model.classify({baseline:fresh.baseline,items:modelItems,signal:abort.signal}),new Promise((_,reject)=>{timer=setTimeout(()=>{abort.abort();reject(Object.assign(new Error('MODEL_TIMEOUT'),{status:504}));},25000);})]);}
      finally{if(timer)clearTimeout(timer);}
      const validated=validate(result,modelItems);
      const latest=await jobGroup(job);
      if(!latest.group){await repo.patch(job,{status:'done',reason:'SUPERSEDED'},now(),true);return;}
      await repo.patch(job,{status:'done',reason:null,data:{...job.data,result:validated}},now(),true);
    }catch(error){
      const e=error as {status?:number;retryAfterMs?:number;permanent?:boolean};
      const transient=!e.permanent&&(e.status===429||e.status===408||e.status===504||!!e.status&&e.status>=500);
      const delay=Math.max(Number(e.retryAfterMs)||0,Math.min(300000,2000*2**job.attempts)*(1+random()));
      await repo.patch(job,{status:transient&&job.attempts<4?'retryable':'blocked',reason:transient?'MODEL_RETRY_EXHAUSTED_OR_DELAYED':'MODEL_UNAVAILABLE_OR_INVALID',nextAtMs:now()+delay},now(),true);
      if(budget&&(e.permanent||[400,401,402,403].includes(e.status??0))){await repo.patch(budget,{status:'blocked',reason:'PROVIDER_REQUIRES_RECOVERY'},now(),true);budget=null;}
      if(budget&&e.status===429){await repo.patch(budget,{nextAtMs:now()+delay},now(),true);budget=null;}
    }finally{if(budget)await repo.patch(budget,{nextAtMs:now()+1000},now(),true);}
  }
  async function resolution(wf:string,items:RepairInboxItem[],baseline:RepairBaseline){
    const fp=fingerprint(items,baseline),job=await get(wf,fp);
    const fallback=exact(items);
    let result=job?.status==='done'&&job.data.result?job.data.result as ProposalGrouping:fallback;
    const status:ProposalAggregation['status']=job?.status==='done'&&job.data.result?'ready':job?.status==='dispatching'?'organizing':job?.status==='retryable'?'retryable':job?.status==='blocked'?'unavailable':'queued';
    return {fp,result,status};
  }
  return {
    async tick(){
      if(ticking)return 0;ticking=true;
      try{const persisted=(await input.db.query<{workflow_id:string}>("SELECT workflow_id FROM workflow_repair_release_records WHERE record_kind = 'proposal_scan' ORDER BY created_at_ms LIMIT 1"))[0];
        const root=persisted?.workflow_id??(await input.db.query<{workflow_id:string}>('SELECT workflow_id FROM workflow_specs ORDER BY workflow_id LIMIT 1'))[0]?.workflow_id;if(!root)return 0;
        await scan(root);
        // Fair global order; one bounded request per monitor tick, not one per page/workflow.
        const rows=await repo.readyAcross('proposal_group',now(),20,input.scopeKey??'database');
        for(const job of rows){if(job.leaseUntilMs<=now()){await work(job,root);return 1;}}
        return 0;
      }finally{ticking=false;}
    },
    async retry(workflowId:string){
      const limits=await input.db.query<{body_json:string}>("SELECT body_json FROM workflow_repair_release_records WHERE record_kind = 'proposal_budget'");
      for(const row of limits){const job:AutomationJob=JSON.parse(row.body_json);if(job.status==='blocked')await repo.recoverDraft(job,job.data,now());}
      const rows=await input.db.query<{body_json:string}>("SELECT body_json FROM workflow_repair_release_records WHERE workflow_id = ? AND record_kind = 'proposal_group'",[workflowId]);
      for(const row of rows){const job:AutomationJob=JSON.parse(row.body_json);if(job.status==='blocked')await repo.recoverDraft(job,job.data,now());}
    },
    async present(wf:string,items:RepairInboxItem[],baseline:RepairBaseline){
      const presented:RepairInboxItem[]=items.filter(i=>!i.sourceAvailable||i.state!=='pending');
      for(const g of groups(items,baseline)){
        const {fp,result,status}=await resolution(wf,g.items,baseline);
        for(const plan of result.plans){
          const originals=plan.itemIds.map(id=>g.items.find(i=>i.itemId===id)!);
          const reevaluation=originals.some(item=>!current(item,baseline));
          const aggregation:ProposalAggregation={...plan,id:digestRepairJson([fp,plan.itemIds]),fingerprint:fp,reevaluation,status:reevaluation&&g.items.length===1?'stale':plan.relation==='exact'&&(originals.length>1||g.items.length===1)?'ready':status,
            originals,...(result.causes?{causes:result.causes.filter(c=>c.itemIds.some(id=>plan.itemIds.includes(id)))}:{}),...(result.unknowns?{unknowns:result.unknowns}:{}),flowIds:[...new Set(originals.flatMap(repairEvidenceFlows))].sort(),conflicts:result.conflicts.filter(c=>c.itemIds.some(id=>plan.itemIds.includes(id)))};
          presented.push({...originals[0],aggregation});
        }
      }
      return presented.sort((a,b)=>a.itemId.localeCompare(b.itemId));
    },
    async prepare(wf:string,selected:RepairItem[],baseline:RepairBaseline){
      const snapshot=await input.read(wf);
      if(canonicalRepairJson(snapshot.baseline)!==canonicalRepairJson(baseline))throw new RepairBatchError('STATE_CONFLICT','生效版本在准备期间再次变化，请确认新版本后提交；选择与要求保留',{reasonCode:'BASELINE_STALE'});
      const ids=new Set(selected.map(i=>i.itemId));
      if(selected.length>1 && selected.some(item=>!operations(item).length && !Array.isArray(item.proposal?.patches)))throw new RepairBatchError('INVALID_INPUT','多条文字建议缺少明确修改范围，可单独处理或补充方案后再整合',{reasonCode:'PLAN_SCOPE_INCOMPLETE'});
      const reevaluate=selected.some(i=>!current(i,baseline));
      const plans: NonNullable<import('../contracts/repair-batch.js').RepairBatchInput['integration']>['plans'] = [];
      const applicable = snapshot.items.map(item => ids.has(item.itemId) && item.state === 'processing' ? { ...item, state: 'pending' as const, sourceAvailable: true } : item);
      for(const g of groups(applicable,baseline)){
        const chosen=g.items.filter(i=>ids.has(i.itemId));if(!chosen.length)continue;
        const {result,status,fp}=await resolution(wf,g.items,baseline);
        if(result.conflicts.some(c=>c.itemIds.every(id=>ids.has(id))))throw new RepairBatchError('INVALID_INPUT','方案目标需要取舍，请保留一种选择',{reasonCode:'PLAN_CONFLICT'});
        const overlaps=selectionConflicts(chosen);
        for(const overlap of overlaps){const plan=result.plans.find(p=>overlap.itemIds.every(id=>p.itemIds.includes(id)));
          if(!plan||plan.relation==='uncertain'||plan.relation!=='exact'&&status!=='ready')throw new RepairBatchError('INVALID_INPUT','重叠修改尚待整合，请等待或只选择一项原建议',{reasonCode:'PLAN_NOT_READY'});}
        for(const plan of result.plans){const members=plan.itemIds.filter(id=>ids.has(id));if(!members.length)continue;
          if(plan.relation!=='exact'&&members.length!==plan.itemIds.length)throw new RepairBatchError('SOURCE_CHANGED','方案成员已变化，请确认受影响范围；原始选择保留',{reasonCode:'PLAN_SELECTION_INCOMPLETE'});
          plans.push({id:digestRepairJson([fp,plan.itemIds]),originalItemIds:members,goals:reevaluate?['基于当前 Pack 重新评估原目标；旧 operations/patches 仅作证据，禁止直接套用；已满足或不适用须逐项说明。',...plan.changes]:plan.changes,relation:plan.relation,...(result.causes?{causes:result.causes.filter(c=>c.itemIds.some(id=>members.includes(id)))}:{}),...(result.unknowns?{unknowns:result.unknowns}:{})});
          }
      }
      // Check across issue groups too: group-local model suggestions cannot authorize cross-group overwrites.
      const all=selected as RepairInboxItem[];
      for(const conflict of selectionConflicts(all))if(!groups(applicable,baseline).some(g=>conflict.itemIds.every(id=>g.items.some(i=>i.itemId===id))))throw new RepairBatchError('INVALID_INPUT','跨问题重叠修改尚待整合，请分别取舍',{reasonCode:'PLAN_NOT_READY'});
      return { integration: plans.length && (selected.length>1||reevaluate) ? {schemaVersion:'workflow-proposal-plan/v1' as const,baselineSpecDigest:baseline.specDigest,plans}:undefined, instructions:''};
    },
  };
}
