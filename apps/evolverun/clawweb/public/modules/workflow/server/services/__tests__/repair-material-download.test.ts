// @vitest-environment node
import { afterEach, expect, it } from 'vitest';
import express from 'express';
import type {Server} from 'node:http';
import { repairFixture } from './repair-batch-fixtures.js';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createRepairBatchesRouter } from '../../routes/repair-batches.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
let server:Server;
async function request(url:string,auth='',offset?:number){
 const response=await fetch(`http://127.0.0.1:${(server.address() as {port:number}).port}${url}${offset===undefined?'':`?offset=${offset}`}`,{headers:{Authorization:auth}});
 return {status:response.status,body:await response.json()};
}
let f:Awaited<ReturnType<typeof repairFixture>>;
afterEach(async()=>{if(server)await new Promise<void>(r=>server.close(()=>r()));await f?.db.close()});
async function setup(delivery=true){
 f=await repairFixture(2);
 for(const m of repairReleaseMigrations)for(const sql of m.sql)await f.db.exec(f.db.dialect.renderDdl(sql));
 for(const item of f.items)item.context={nodeId:'report',diagnoses:[{flowId:'flow-1',analysisContext:{facts:['report returned object instead of string']},evidence:[{eventId:'event-1',payload:{error:'expected string, actual object',report:'long report\n'.repeat(60000)}}]}]};
 const port={...f.executionPort,materialDeliveryVersion:delivery?'repair-material-download/v1' as const:undefined};
 const service=createRepairWorkbenchService(f.db,f.sourcePort,port);
 const rev=await service.create('human',{workflowId:'wf-1',itemIds:f.items.map(x=>x.itemId),inputDigest:(await service.candidates('wf-1')).inputDigest,instructions:'keep output goals',requestId:'download-case'});
 const sent=f.calls[0] as any;
 return {service,rev,sent,port};
}
it('dispatches core evidence and a scoped index without any full report',async()=>{
 const {sent}=await setup();
 expect(Buffer.byteLength(JSON.stringify(sent))).toBeLessThan(1024*1024);
 expect(JSON.stringify(sent)).not.toContain('long report\\n'.repeat(100));
 expect(JSON.stringify(sent.input)).toContain('report returned object instead of string');
 expect(JSON.stringify(sent.input)).toContain('expected string, actual object');
 expect(sent.materialAccess.files.map((x:any)=>x.itemId)).toEqual(['item-0','item-1']);
 expect(sent.materialAccess.files[0]).toMatchObject({bytes:expect.any(Number),sha256:expect.stringMatching(/^[a-f0-9]{64}$/)});
});
it('downloads bounded frozen bytes only with the current execution credential, and revokes on cancellation',async()=>{
 const {service,rev,sent}=await setup();
 const app=express().use(createRepairBatchesRouter({service,authorize:async()=>null}));
 server=await new Promise<Server>(r=>{const s=app.listen(0,'127.0.0.1',()=>r(s))});
 const url=`/material-download/${rev.taskId}/1/item-0`;
 expect((await request(url)).status).toBe(401);
 const auth=`Bearer ${sent.materialAccess.token}`;
 const first=await request(url,auth);
 expect(first.status).toBe(200);expect(Buffer.byteLength(JSON.stringify(first.body))).toBeLessThan(100000);
 expect(first.body).toMatchObject({offset:0,totalBytes:sent.materialAccess.files[0].bytes,sha256:sent.materialAccess.files[0].sha256});
 expect(first.body.nextOffset).toBeGreaterThan(0);
 expect((await request(url.replace('item-0','outside'),auth)).status).toBe(404);
 expect((await request(url.replace('/1/','/2/'),auth)).status).toBe(401);
 f.items[0].context={changed:'live source'};
 const chunks:Buffer[]=[];let offset:number|null=0;
 do {const part=await request(url,auth,offset);expect(part.status).toBe(200);chunks.push(Buffer.from(part.body.base64,'base64'));offset=part.body.nextOffset;}while(offset!==null);
 const original=JSON.parse(Buffer.concat(chunks).toString());
 expect(original.context.diagnoses[0].evidence[0].payload.report).toBe('long report\n'.repeat(60000));
 await service.cancel('human',rev.taskId,1);
 expect((await request(url,auth)).status).toBe(401);
});

it('refuses an execution provider that cannot download materials without sending full evidence',async()=>{
 await expect(setup(false)).rejects.toMatchObject({code:'CAPABILITY_UNAVAILABLE'});
 expect(f.calls).toHaveLength(0);
 expect(await f.db.query('SELECT * FROM workflow_repair_revisions')).toHaveLength(0);
 expect(await f.db.query('SELECT * FROM ce_tasks')).toHaveLength(0);
 expect(await f.db.query("SELECT * FROM workflow_repair_release_records WHERE record_kind='repair_material'")).toHaveLength(0);
});
it('stores no raw credential and expires the read capability',async()=>{
 const {service,rev,sent}=await setup();
 const rows=await f.db.query<{body_json:string;record_id:string}>("SELECT body_json,record_id FROM workflow_repair_release_records WHERE record_kind='repair_material_access'");
 expect(JSON.stringify(rows)).not.toContain(sent.materialAccess.token);
 const grant=JSON.parse(rows[0].body_json);grant.expiresAtMs=1;
 await f.db.exec('UPDATE workflow_repair_release_records SET body_json=? WHERE record_id=?',[JSON.stringify(grant),rows[0].record_id]);
 expect(await service.materialChunk!(rev.taskId,1,'item-0',sent.materialAccess.token,0)).toBeNull();
 expect(JSON.stringify(await service.task(rev.taskId))).not.toContain(sent.materialAccess.token);
});
it('keeps structured error excerpts bounded in the core projection',async()=>{
 const {projectRepairEvidence}=await import('../../contracts/repair-evidence-projection.js');
 const projected=projectRepairEvidence({itemId:'item',groupKey:'group',proposalKey:'key',contentRevision:1,previousItemId:null,proposal:null,instruction:'keep goal',sources:[],context:{diagnoses:[{evidence:[{eventId:'event',payload:{error:{stack:'trace'.repeat(300000)}}}]}]}});
 expect(Buffer.byteLength(JSON.stringify(projected))).toBeLessThan(10000);
});

it('rolls back an unsupported generation retry without a new revision or execution claim',async()=>{
 const {service,rev,port}=await setup();
 await service.report({workflowId:'wf-1',taskId:rev.taskId,revision:1,expectedStateVersion:0,status:'failed',error:{code:'AIS_FAILED'}});
 f.setRemoteStatus({status:'failed',rawStatus:'failed',errorMessage:'controlled failure'});
 port.materialDeliveryVersion=undefined;
 await expect(service.retryGeneration('human',rev.taskId,{expectedRevision:1,requestId:'unsupported-retry'})).rejects.toMatchObject({code:'CAPABILITY_UNAVAILABLE'});
 expect(f.calls).toHaveLength(1);
 expect((await service.task(rev.taskId)).revisions).toHaveLength(1);
 expect(await f.db.query('SELECT * FROM ce_steps')).toHaveLength(1);
});
