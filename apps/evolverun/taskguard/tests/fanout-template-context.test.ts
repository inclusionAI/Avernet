import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildTemplateContext } from '../src/runner.js';
import type { FlowState } from '../src/types.js';

test('fanout runtime identity and storage context cannot be supplied through business parameters', () => {
 const state={params:{initiator:{id:'spoof'},runtime:{stateDir:'/spoof'}},workflowData:{},actionOutputs:{},flowHooks:{},nodeStates:{},
  runInitiator:{id:'trusted',name:'Trusted'},fanoutRuntimeContext:{stateDir:'isolated-child',launchIdempotencyKey:'launch-key',sessionKey:'child-session'}} as unknown as FlowState;
 const context=buildTemplateContext(state,'.');
 assert.deepEqual(context.initiator,{id:'trusted',name:'Trusted'});
 assert.equal((context.runtime as {stateDir:string}).stateDir,'isolated-child');
 (context.initiator as {id:string}).id='mutated';
 assert.equal(state.runInitiator?.id,'trusted');
});

test('fanout child dispatch drops a parent tool context when session identity changes', async () => {
 const { scopeToolContextForSession } = await import('../src/executors/embedded-context.js');
 const parent={sessionKey:'parent-session',sessionId:'parent-id',sessionFile:'/parent.jsonl',workspaceDir:'/parent'};
 assert.equal(scopeToolContextForSession(parent,'child-session'),undefined);
 assert.equal(scopeToolContextForSession(parent,'parent-session'),parent);
});
