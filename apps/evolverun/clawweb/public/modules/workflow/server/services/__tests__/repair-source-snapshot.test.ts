// @vitest-environment node
import { expect, it } from 'vitest';
import { repairSourceSnapshot } from '../repair-source-snapshot.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { WorkflowSnapshotBinding } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
const binding: WorkflowSnapshotBinding = {status:'resolved',snapshot:{kind:'deployment',workflowId:'wf',workflowVersion:34,deployNumber:34,configDigest:'a'.repeat(64)}};
const runs = new Map([['run',binding]]);
const db = {} as IDatabase;
it.each([null,{summary:'Text goal'},{baseSpecDigest:'b'.repeat(64),summary:'Semantic digest predates snapshot binding'}])('derives suggestion version from its proven original run, regardless of patch shape/digest: %j', async proposal => {
  expect(await repairSourceSnapshot(db,'wf',['run'],proposal,undefined,runs)).toEqual(binding);
});
it('keeps a frozen analysis version independent of the patch base digest',async()=>{
  expect(await repairSourceSnapshot(db,'wf',[],{baseSpecDigest:'b'.repeat(64)},binding,runs)).toEqual(binding);
});
it('rejects absent, mixed-version and cross-workflow provenance',async()=>{
  expect((await repairSourceSnapshot(db,'wf',['missing'],null,undefined,runs)).status).toBe('unresolved');
  const other:WorkflowSnapshotBinding={status:'resolved',snapshot:{...binding.snapshot!,kind:'deployment',workflowId:'wf',workflowVersion:35,deployNumber:35}};
  expect((await repairSourceSnapshot(db,'wf',['run','other'],null,undefined,new Map([...runs,['other',other]]))).status).toBe('unresolved');
  expect((await repairSourceSnapshot(db,'foreign',[],null,binding,runs)).status).toBe('unresolved');
  expect(await repairSourceSnapshot(db,'wf',['run'],null,{status:'unresolved',reason:'IDENTITY_MISMATCH'},runs)).toEqual({status:'unresolved',reason:'IDENTITY_MISMATCH'});
});
