import { randomBytes } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { canonicalRepairJson,digestRepairJson,RepairBatchError,type RepairBatchInput } from '../contracts/repair-batch.js';
import type { RepairMaterialAccess,RepairMaterialChunk,RepairExecutionIdentity } from '../contracts/repair-workbench.js';
import { RepairMaterialRepository } from './repair-material-repository.js';
import { RepairBatchRepository } from './repair-batch-repository.js';
import { RepairTaskRepository } from './repair-task-repository.js';
const CHUNK_BYTES=64*1024;
/** One read-only capability per execution. Only its digest is persisted; no user cookie or shared key. */
export class RepairMaterialDownload {
 constructor(private readonly db:IDatabase){}
 async issue(input:RepairBatchInput,identity:RepairExecutionIdentity):Promise<RepairMaterialAccess>{
  const full=await new RepairMaterialRepository(this.db).expand(input);
  const token = (
    randomBytes(32).toString('base64url')
  );
  const id=digestRepairJson(['repair-material-access',token]),now=Date.now();
  const files=full.items.map(item=>({itemId:item.itemId,bytes:Buffer.byteLength(canonicalRepairJson(item)),sha256:digestRepairJson(item)}));
  const grant={executionId:identity.executionId,expiresAtMs:now+2*60*60*1000,files};
  await this.db.exec(`INSERT INTO workflow_repair_release_records
   (record_id,workflow_id,task_id,revision,record_kind,state_version,request_key,request_digest,body_json,created_at_ms,updated_at_ms)
   VALUES (?,?,?,?,?,0,?,?,?,?,?)`,[id,input.baseline.workflowId,input.taskId,input.revision,'repair_material_access',id,id,canonicalRepairJson(grant),now,now]);
  return {schemaVersion:'repair-material-download/v1',token,files};
 }
 async read(taskId:string,revision:number,itemId:string,token:string,offset:number):Promise<RepairMaterialChunk|null>{
  if(!/^[A-Za-z0-9_-]{43}$/.test(token))return null;
  const rows=await this.db.query<{body_json:string;workflow_id:string}>(`SELECT body_json,workflow_id FROM workflow_repair_release_records
   WHERE record_id=? AND task_id=? AND revision=? AND record_kind='repair_material_access'`,[digestRepairJson(['repair-material-access',token]),taskId,revision]);
  if(rows.length!==1)return null;
  const grant=JSON.parse(rows[0].body_json) as {executionId:string;expiresAtMs:number;files:RepairMaterialAccess['files']};
  const execution=await new RepairTaskRepository(this.db).execution(taskId,revision);
  if(grant.expiresAtMs<Date.now()||execution?.executionId!==grant.executionId||!['dispatching','dispatch_uncertain','dispatched','running'].includes(execution.status))return null;
  const file=grant.files.find(file=>file.itemId===itemId);
  if(!file)throw new RepairBatchError('ITEM_NOT_FOUND','Material is not in this frozen selection');
  if(!Number.isSafeInteger(offset)||offset<0||offset>=file.bytes)throw new RepairBatchError('INVALID_INPUT','Invalid material offset');
  const row=await new RepairBatchRepository(this.db).getRevision(rows[0].workflow_id,taskId,revision);
  if(!row)throw new RepairBatchError('REVISION_NOT_FOUND','Frozen revision is unavailable');
  const full=await new RepairMaterialRepository(this.db).expand(row.input);
  const item=full.items.find(item=>item.itemId===itemId);
  if(!item||digestRepairJson(item)!==file.sha256)throw new RepairBatchError('CONTENT_MISMATCH','Frozen material integrity failed');
  const bytes=Buffer.from(canonicalRepairJson(item));
  const end=Math.min(offset+CHUNK_BYTES,bytes.length);
  return {schemaVersion:'repair-material-chunk/v1',itemId,offset,nextOffset:end===bytes.length?null:end,totalBytes:bytes.length,sha256:file.sha256,base64:bytes.subarray(offset,end).toString('base64')};
 }
}
