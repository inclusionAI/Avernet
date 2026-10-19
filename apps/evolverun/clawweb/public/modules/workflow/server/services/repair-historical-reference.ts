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
/** All explicitly selected goals are rewritten together against the current files, never old patches applied sequentially. */
export function historicalIntegration(items:RepairItem[],baseline:RepairBaseline):NonNullable<RepairBatchInput['integration']>{
 for(const item of items)if(!itemSnapshot(item))changed('PLAN_SCOPE_INCOMPLETE',[item.itemId]);
 return {schemaVersion:'workflow-proposal-plan/v1',baselineSpecDigest:baseline.specDigest,plans:[{id:digestRepairJson(['historical-reference/v1',baseline,items.map(i=>i.itemId)]),originalItemIds:items.map(i=>i.itemId),
 goals:items.map(i=>`核对原始建议 ${i.itemId} 的完整目标，在当前目标文件中保留适用约束；历史来源仅作参考，不重放旧补丁。`),relation:'complementary'}]};
}
