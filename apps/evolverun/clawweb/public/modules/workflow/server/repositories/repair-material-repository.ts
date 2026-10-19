import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { boundedRepairJson,canonicalRepairJson,digestRepairJson,MAX_FROZEN_BYTES,MAX_MATERIAL_BYTES,validateRepairBatchInput,RepairBatchError,type RepairBatchInput,type RepairItem } from '../contracts/repair-batch.js';
import { projectRepairEvidence } from '../contracts/repair-evidence-projection.js';
type Snapshot={workflowId:string;taskId:string;revision:number;items:RepairItem[]};
function mismatch():never{throw new RepairBatchError('CONTENT_MISMATCH','冻结材料缺失或完整性校验失败，未派发任务',{reasonCode:'FROZEN_MATERIAL_INVALID'});}
/** Immutable snapshots use the existing record store; no external/live source is consulted. */
export class RepairMaterialRepository{
 constructor(private readonly db:IDatabase){}
 async freeze(input:RepairBatchInput):Promise<RepairBatchInput>{
  if(input.materials){await this.expand(input);return input;}
  if(Buffer.byteLength(canonicalRepairJson(input))<=MAX_FROZEN_BYTES)return input;
  const snapshot:Snapshot={workflowId:input.baseline.workflowId,taskId:input.taskId,revision:input.revision,items:input.items};
  let json:string;
  try{json=boundedRepairJson(snapshot,MAX_MATERIAL_BYTES);}catch(error){if(error instanceof RepairBatchError)throw new RepairBatchError(error.code,error.message,{...error.details,stage:'frozen_materials',reasonCode:'REPAIR_MATERIALS_TOO_LARGE'});throw error;}
  const sha256=digestRepairJson(snapshot),recordId=digestRepairJson(['repair-material',snapshot.workflowId,snapshot.taskId,snapshot.revision,sha256]);
  const compact={...input,items:input.items.map(item=>projectRepairEvidence(item)),materials:{schemaVersion:'repair-frozen-materials/v1' as const,recordId,sha256,bytes:Buffer.byteLength(json)}};
  // Reject an oversized goal/operation set before any write; original constraints are never clipped.
  validateRepairBatchInput(compact);
  const old=await this.db.query<{body_json:string}>('SELECT body_json FROM workflow_repair_release_records WHERE record_id = ?',[recordId]);
  if(old.length){if(digestRepairJson(JSON.parse(old[0].body_json))!==sha256)mismatch();return compact;}
  const now=Date.now();
  await this.db.exec(`INSERT INTO workflow_repair_release_records
   (record_id,workflow_id,task_id,revision,record_kind,state_version,request_key,request_digest,body_json,created_at_ms,updated_at_ms)
   VALUES (?,?,?,?,?,0,?,?,?,?,?)`,[recordId,snapshot.workflowId,snapshot.taskId,snapshot.revision,'repair_material',recordId,sha256,json,now,now]);
  return compact;
 }
 async expand(input:RepairBatchInput):Promise<RepairBatchInput>{
  if(!input.materials)return input;
  const ref=input.materials;
  const rows=await this.db.query<{body_json:string}>('SELECT body_json FROM workflow_repair_release_records WHERE record_id = ? AND workflow_id = ? AND task_id = ? AND record_kind = ?',[ref.recordId,input.baseline.workflowId,input.taskId,'repair_material']);
  if(rows.length!==1)mismatch();
  const body=rows[0].body_json;
  if(Buffer.byteLength(body)!==ref.bytes||Buffer.byteLength(body)>MAX_MATERIAL_BYTES)mismatch();
  let snapshot:Snapshot;try{snapshot=JSON.parse(body);}catch{mismatch();}
  if(digestRepairJson(snapshot)!==ref.sha256||snapshot.workflowId!==input.baseline.workflowId||snapshot.taskId!==input.taskId||snapshot.revision>input.revision||!Array.isArray(snapshot.items)
   ||canonicalRepairJson(snapshot.items.map(item=>projectRepairEvidence(item)))!==canonicalRepairJson(input.items))mismatch();
  const {materials:_materials,...expanded}=input;
  return {...expanded,items:snapshot.items};
 }
}
