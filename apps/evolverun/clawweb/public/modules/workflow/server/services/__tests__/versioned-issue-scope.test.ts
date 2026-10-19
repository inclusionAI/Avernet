// @vitest-environment node
import {afterEach,describe,expect,it} from 'vitest';
import {RepairBatchRepository} from '../../repositories/repair-batch-repository.js';
import {repairFixture} from './repair-batch-fixtures.js';
import {createRepairWorkbenchService} from '../repair-batch-service.js';
import type {WorkflowSnapshotBinding} from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
const fixtures: Awaited<ReturnType<typeof repairFixture>>[]=[];
afterEach(async()=>{for(const f of fixtures.splice(0))await f.db.close();});
const binding=(version:number,deploy:number):WorkflowSnapshotBinding=>({status:'resolved',snapshot:{kind:'deployment',workflowId:'wf-1',workflowVersion:version,deployNumber:deploy,configDigest:'a'.repeat(64)}});
async function setup(){
  const f=await repairFixture(3);fixtures.push(f);
  let active=binding(39,34);
  f.sourcePort.activeSnapshot=async()=>active;
  f.sourcePort.readVersion=async()=> 'unchanged-source';
  f.items[0].context={signature:'timeout',sourceSnapshot:binding(39,34)};
  f.items[1].context={signature:'timeout',sourceSnapshot:binding(40,35)};
  f.items[2].context={signature:'timeout',sourceSnapshot:{status:'unresolved',reason:'LEGACY_MISSING'}};
  const service=createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort);
  return {...f,service,setActive:(value:WorkflowSnapshotBinding)=>{active=value;}};
}
describe('versioned repair issue scope',()=>{
  it('defaults to the active deployment and filters before counts and pagination',async()=>{
    const f=await setup();
    const current=await f.service.candidates('wf-1',{pageSize:1});
    expect(current.items.map(i=>i.itemId)).toEqual(['item-0']);
    expect(current.page.total).toBe(1);expect(current.counts.pending).toBe(1);
    expect(current.activeSnapshot).toEqual(binding(39,34));
    const history=await f.service.candidates('wf-1',{versionScope:'historical',includeHistorical:true});
    expect(history.items.map(i=>i.itemId).sort()).toEqual(['item-1','item-2']);
  });
  it('keeps version identity in a bounded suggestion preview',async()=>{
    const f=await setup();f.items[0].instruction='x'.repeat(9000);
    const response=await f.service.candidates('wf-1');
    expect(response.items[0].readSummary).toBeDefined();
    expect(response.items[0].context?.sourceSnapshot).toEqual(binding(39,34));
  });
  it('changes the default scope on deployment without modifying any suggestion lifecycle',async()=>{
    const f=await setup();await f.service.candidates('wf-1');
    f.setActive(binding(40,35));
    expect((await f.service.candidates('wf-1')).items.map(i=>i.itemId)).toEqual(['item-1']);
    expect(await f.db.query('SELECT * FROM workflow_repair_items')).toEqual([]);
    expect((await f.service.candidates('wf-1',{versionScope:'all',includeHistorical:true})).items).toHaveLength(3);
  });
  it('preserves a proven legacy item identity and verified state when attaching its snapshot',async()=>{
    const f=await setup();
    f.items[0].proposal={baseSpecDigest:'a'.repeat(64),summary:'Raise timeout',operations:[{op:'replace',nodeId:'fetch',path:'/timeoutMs',value:50}]};
    const legacy={...f.items[0],context:{signature:'timeout',diagnoses:[{flowId:'run-old'}]}};
    await f.db.transaction(tx=>new RepairBatchRepository(tx).materializeItem(tx,{workflowId:'wf-1',episodeKey:'initial',item:legacy}));
    await f.db.exec("UPDATE workflow_repair_items SET state='verified' WHERE item_id='item-0'");
    f.items[0]={...legacy,itemId:'scoped-new-id',context:{...legacy.context,sourceSnapshot:binding(39,34)}};
    const response=await f.service.candidates('wf-1');
    expect(response.items.map(i=>[i.itemId,i.state])).toEqual([['item-0','verified']]);
  });
  it('does not invalidate current selection input when unrelated historical content changes',async()=>{
    const f=await setup();const before=await f.service.candidates('wf-1');
    f.items[1].instruction='Updated historical wording';
    const fresh=await createRepairWorkbenchService(f.db,f.sourcePort,f.executionPort).candidates('wf-1');
    expect(fresh.inputDigest).toBe(before.inputDigest);
  });
  it('filters historical suggestions by the explicitly expanded source version',async()=>{
    const f=await setup();
    const key=JSON.stringify(['deployment','wf-1',40,35]);
    const response=await f.service.candidates('wf-1',{versionScope:'historical',snapshotKey:key,includeHistorical:true});
    expect(response.items.map(i=>i.itemId)).toEqual(['item-1']);
    expect((await f.service.candidates('wf-1',{versionScope:'historical',snapshotKey:'unresolved',includeHistorical:true})).items.map(i=>i.itemId)).toEqual(['item-2']);
  });

});
