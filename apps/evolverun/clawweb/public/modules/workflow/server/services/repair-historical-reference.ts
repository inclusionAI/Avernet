import {workflowSnapshotKey, type WorkflowSnapshotBinding, type WorkflowSnapshotRef} from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import {itemSnapshot, itemSnapshotKey} from '../contracts/repair-version-scope.js';
import {RepairBatchError,digestRepairJson,type RepairItem,type RepairBaseline,type RepairBatchInput} from '../contracts/repair-batch.js';
import type {RepairSelectionRequest,RepairInboxItem} from '../contracts/repair-workbench.js';
export {historicalReference} from '../contracts/repair-version-scope.js';
function changed(reasonCode:string,itemIds:string[]=[]):never{
 throw new RepairBatchError('SOURCE_CHANGED',reasonCode==='TARGET_VERSION_CHANGED'?'当前生效版本已变化，选择与要求已保留，请核对新目标后提交':'所选建议的来源版本需要核对，其他选择与要求已保留',{reasonCode,itemIds});
}
export function reviewVersionSelection(request:RepairSelectionRequest,items:RepairInboxItem[],active:WorkflowSnapshotBinding|null|undefined):WorkflowSnapshotRef|undefined{
 if(active===undefined){if(request.historicalItemIds?.length||request.targetSnapshotKey)changed('HISTORICAL_REFERENCE_UNSUPPORTED');return;}
 if(active?.status!=='resolved'||active.snapshot.kind!=='deployment')changed('NO_ACTIVE_VERSION');
 const target=workflowSnapshotKey(active.snapshot);
 if(request.targetSnapshotKey&&request.targetSnapshotKey!==target)changed('TARGET_VERSION_CHANGED');
 const selected=items.filter(item=>request.itemIds.includes(item.itemId));
 const missing=selected.filter(item=>!itemSnapshotKey(item));if(missing.length)changed('BASELINE_IDENTITY_UNKNOWN',missing.map(i=>i.itemId));
 const historical=selected.filter(item=>itemSnapshotKey(item)!==target).map(item=>item.itemId).sort();
 const declared=[...(request.historicalItemIds??[])].sort();
 if(JSON.stringify(historical)!==JSON.stringify(declared)||historical.length&&!request.targetSnapshotKey)changed('HISTORICAL_REFERENCE_CONFIRMATION_REQUIRED',historical);
 return active.snapshot;
}
export function freezeReference(item:RepairItem,target:WorkflowSnapshotRef|undefined):RepairItem{
 if(!target)return item;
 return {...item,context:{...item.context,targetSnapshot:target,referenceOnly:itemSnapshotKey(item)!==workflowSnapshotKey(target)}};
}
/** Review overlapping node goals together; an atomic suggestion is never split. */
export function manualIntegration(items:RepairItem[],baseline:RepairBaseline):NonNullable<RepairBatchInput['integration']>{
 const nodes=(item:RepairItem)=>new Set([
  ...(Array.isArray(item.proposal?.operations)?item.proposal.operations:[]).map((op:unknown)=>op&&typeof op==='object'&&'nodeId' in op&&typeof op.nodeId==='string'&&op.nodeId?op.nodeId:'*'),
  ...(item.context?.nodeId?[String(item.context.nodeId)]:[]),
  ...(Array.isArray(item.proposal?.patches)&&item.proposal.patches.length?['*']:[]),
 ]);
 const groups:Array<{items:RepairItem[];nodes:Set<string>}>=[];
 for(const item of items){
  const current={items:[item],nodes:nodes(item)};if(!current.nodes.size)current.nodes.add('*');
  for(let index=0;index<groups.length;){
   const group=groups[index];
   if(current.nodes.has('*')||group.nodes.has('*')||[...current.nodes].some(node=>group.nodes.has(node))){
    current.items.push(...group.items);for(const node of group.nodes)current.nodes.add(node);groups.splice(index,1);index=0;
   }else index++;
  }
  groups.push(current);
 }
 return {schemaVersion:'workflow-proposal-plan/v1',baselineSpecDigest:baseline.specDigest,plans:groups.map(group=>{
  const originals=items.filter(item=>group.items.includes(item));
  return {id:digestRepairJson(['selected-goals/v1',baseline,originals.map(item=>item.itemId)]),originalItemIds:originals.map(item=>item.itemId),
   goals:originals.map(item=>`综合核对原始建议 ${item.itemId} 的完整目标；合并重复目标，保留互补约束，无法兼容的目标报告未解决，不扩大用户选择。`),relation:'complementary' as const};
 })};
}
/** All explicitly selected goals are rewritten together against the current files, never old patches applied sequentially. */
export function historicalIntegration(items:RepairItem[],baseline:RepairBaseline):NonNullable<RepairBatchInput['integration']>{
 for(const item of items)if(!itemSnapshot(item))changed('PLAN_SCOPE_INCOMPLETE',[item.itemId]);
 return {schemaVersion:'workflow-proposal-plan/v1',baselineSpecDigest:baseline.specDigest,plans:[{id:digestRepairJson(['historical-reference/v1',baseline,items.map(i=>i.itemId)]),originalItemIds:items.map(i=>i.itemId),
 goals:items.map(i=>`核对原始建议 ${i.itemId} 的完整目标，在当前目标文件中保留适用约束；历史来源仅作参考，不重放旧补丁。`),relation:'complementary'}]};
}
