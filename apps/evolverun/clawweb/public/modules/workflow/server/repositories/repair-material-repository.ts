import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { boundedRepairJson,canonicalRepairJson,digestRepairJson,MAX_MODEL_INPUT_BYTES,MAX_FROZEN_BYTES,MAX_MATERIAL_BYTES,repairEvidenceFlows,validateRepairBatchInput,RepairBatchError,type RepairBatchInput,type RepairItem } from '../contracts/repair-batch.js';
import { projectRepairEvidence } from '../contracts/repair-evidence-projection.js';
type Snapshot={workflowId:string;taskId:string;revision:number;items:RepairItem[]};
function mismatch():never{throw new RepairBatchError('CONTENT_MISMATCH','冻结材料缺失或完整性校验失败，未派发任务',{reasonCode:'FROZEN_MATERIAL_INVALID'});}
const bytes=(value:unknown)=>Buffer.byteLength(canonicalRepairJson(value));
const flowCount=(items:RepairItem[])=>new Set(items.flatMap(repairEvidenceFlows)).size;
/** Immutable snapshots use the existing record store; no external/live source is consulted. */
export class RepairMaterialRepository{
 constructor(private readonly db:IDatabase){}
 async freeze(input:RepairBatchInput):Promise<RepairBatchInput>{
  if(input.materials){await this.expand(input);return input;}
  const originalBytes=bytes(input);
  if(originalBytes<=MAX_MODEL_INPUT_BYTES)return input;
  const snapshot:Snapshot={workflowId:input.baseline.workflowId,taskId:input.taskId,revision:input.revision,items:input.items};
  let json:string;
  try{json=boundedRepairJson(snapshot,MAX_MATERIAL_BYTES);}catch(error){if(error instanceof RepairBatchError)throw new RepairBatchError(error.code,error.message,{...error.details,stage:'frozen_materials',reasonCode:'REPAIR_MATERIALS_TOO_LARGE'});throw error;}
  const sha256=digestRepairJson(snapshot),recordId=digestRepairJson(['repair-material',snapshot.workflowId,snapshot.taskId,snapshot.revision,sha256]);
  const compact={...input,items:input.items.map(item=>projectRepairEvidence(item)),materials:{schemaVersion:'repair-frozen-materials/v1' as const,recordId,sha256,bytes:Buffer.byteLength(json),flowCount:flowCount(input.items)}};
  if(originalBytes<=MAX_FROZEN_BYTES&&bytes(compact)>=originalBytes)return input;
  const sizes={originalBytes,coreBytes:bytes(compact),itemCount:input.items.length,
   sourceCount:input.items.reduce((sum,item)=>sum+item.sources.length,0),
   sourceRefsBytes:bytes(input.items.map(item=>item.sources)),proposalBytes:bytes(input.items.map(item=>item.proposal)),
   instructionBytes:bytes(input.items.map(item=>item.instruction)),coreContextBytes:bytes(compact.items.map(item=>item.context))};
  // Reject an oversized goal/operation set before any write; original constraints are never clipped.
  try{validateRepairBatchInput(compact);}catch(error){
   if(error instanceof RepairBatchError&&error.code==='PAYLOAD_TOO_LARGE'){
    console.warn('[workflow-repair] frozen core exceeds budget',{taskId:input.taskId,revision:input.revision,...sizes});
    throw new RepairBatchError(error.code,error.message,{...error.details,sizeBreakdown:sizes});
   }
   throw error;
  }
  console.info('[workflow-repair] frozen input compacted',{taskId:input.taskId,revision:input.revision,...sizes});
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
   ||canonicalRepairJson(snapshot.items.map(item=>projectRepairEvidence(item)))!==canonicalRepairJson(input.items)
   ||ref.flowCount!==undefined&&ref.flowCount!==flowCount(snapshot.items))mismatch();
  const {materials:_materials,...expanded}=input;
  return {...expanded,items:snapshot.items};
 }
}
