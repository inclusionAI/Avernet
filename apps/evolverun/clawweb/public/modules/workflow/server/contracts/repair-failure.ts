/** Execution-bound diagnostic metadata; never a candidate or publication receipt. */
export type RepairFailureIdentity = {taskId:string;revision:number;stepId:string;attempt:number;executionId:string;parentCandidateCommit:string};
export const REPAIR_FAILURE_HEADER='ClawMind workflow-repair-failure/v1\n';
export function repairFailureRef(identity:Pick<RepairFailureIdentity,'taskId'|'revision'|'executionId'>):string{
 if(![identity.taskId,identity.executionId].every(value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,190}$/.test(value))||!Number.isSafeInteger(identity.revision)||identity.revision<1)throw new Error('Invalid failure result identity');
 return `refs/heads/repair-failure/${identity.taskId}/${identity.revision}-${identity.executionId}`;
}
export function readRepairFailure(message:string,expected:RepairFailureIdentity):{status:'failed';error:Record<string,unknown>}{
 if(new TextEncoder().encode(message).length>32*1024||!message.startsWith(REPAIR_FAILURE_HEADER))throw new Error('Invalid repair failure metadata');
 const value=JSON.parse(message.slice(REPAIR_FAILURE_HEADER.length));
 if(value?.schemaVersion!=='workflow-repair-result/v1'||value.status!=='failed'||value.draft!=null||value.checks!=null
  ||(['taskId','revision','stepId','attempt','executionId','parentCandidateCommit'] as const).some(key=>value[key]!==expected[key])
  ||!value.error||typeof value.error!=='object'||Array.isArray(value.error)
  ||typeof value.error.code!=='string'||!value.error.code||value.error.code.length>128
  ||typeof value.error.message!=='string'||!value.error.message||value.error.message.length>4000)throw new Error('Repair failure does not match the execution');
 return {status:'failed',error:value.error};
}
