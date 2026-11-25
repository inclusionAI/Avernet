import assert from 'node:assert/strict';
import { test } from 'node:test';
import { executeSubworkflow, buildChildFlowState } from '../src/executors/subworkflow.js';
import { digestWorkflowSpec } from '../src/workflow-dependencies.js';
import type { FlowState, WorkflowNode, WorkflowSpec } from '../src/types.js';

const child: WorkflowSpec={id:'child',version:1,title:'Child',nodes:[{id:'finalize',phase:'P1',dependsOn:[],executor:{type:'done'}}],outputs:{result:{from:'nodeOutput.finalize'}}};
const node: WorkflowNode={id:'fanout',phase:'P5',dependsOn:[],executor:{type:'subworkflow',workflowId:'child',version:'pinned',fanout:{schemaVersion:'fanout.v1',items:[],itemIdFrom:['id'],concurrency:2,successPolicy:'all_done',finishMode:'wait_all',resultPolicy:'collect_all',groupTimeoutSeconds:10,cancelGraceSeconds:1}}};
function state(): FlowState{return {workflowId:'parent',workflowVersion:7,params:{},executionMode:'private',businessStatus:'RUNNING',currentPhase:'P5',activeNodes:[],nodeStates:{},workflowData:{},actionOutputs:{},flowHooks:{},auditLog:[]} as FlowState;}

test('fanout dispatch refuses the legacy single-child path when no durable host is available',async()=>{
 let calls=0;const result=await executeSubworkflow(node,{skillRoot:''},state(),'p',{resolvedWorkflows:[],executeChildWorkflow:async()=>{calls++;return {status:'succeeded',workflowData:{}};}});
 assert.equal(result.status,'failed');assert.match(result.error ?? '',/fanout|durable|host/i);assert.equal(calls,0);
});

test('fanout empty plan uses its published dependency without consulting mutable saved specs',async()=>{
 const parent=state();
 parent.subworkflowDependencies={child:{workflowId:'child',publishedVersion:1,releaseId:'release-v1',spec:child,digest:digestWorkflowSpec(child)}};
 let saved:any=null;
 const result=await executeSubworkflow(node,{skillRoot:''},parent,'p',{
  workflowSpecRepo:{findByWorkflowId:async()=>{throw new Error('mutable spec must not be read');}} as never,
  executeChildWorkflow:async()=>{throw new Error('legacy path must not be used');},
  fanoutRuntime:{load:async()=>saved,save:async s=>{saved=structuredClone(s);},parentStatus:async()=> 'running',startOrGetChild:async()=>{throw new Error('empty');},cancelChild:async()=>{}},
 } as never);
 assert.equal(result.status,'succeeded');assert.equal(result.result?.total,0);assert.equal(result.result?.childPublishedVersion,1);
});

test('child state inherits trusted initiator without taking it from mapped params',()=>{
 const parent=state();parent.runInitiator={id:'trusted'};
 const next=buildChildFlowState(child,{initiator:'spoof'},parent,'fanout','p',1);
 assert.deepEqual(next.runInitiator,{id:'trusted'});assert.notEqual(next.runInitiator,parent.runInitiator);
});

test('locked child closure resolves legacy unpinned subworkflow calls from its published dependency', async()=>{
 const grandchild: WorkflowSpec={id:'grandchild',version:1,title:'Grandchild',nodes:[{id:'done',phase:'P1',dependsOn:[],executor:{type:'done'}}],outputs:{result:{from:'nodeOutput.done'}}};
 const lockedChild: WorkflowSpec={id:'child',version:1,title:'Child',nodes:[],subworkflowDependencies:{grandchild:{workflowId:'grandchild',publishedVersion:1,releaseId:'release-v1',spec:grandchild,digest:digestWorkflowSpec(grandchild)}}};
 const legacyNode: WorkflowNode={id:'legacy-call',phase:'P1',dependsOn:[],executor:{type:'subworkflow',workflowId:'grandchild'}};
 const flow=state();flow.workflowId='child';flow.workflowSnapshot=structuredClone(lockedChild);flow.subworkflowDependencies=structuredClone(lockedChild.subworkflowDependencies);
 let selected: WorkflowSpec|undefined;
 const result=await executeSubworkflow(legacyNode,{skillRoot:''},flow,'child-flow',{
  workflowSpecRepo:{findByWorkflowId:async()=>{throw new Error('mutable resolver must not be called');}} as never,
  executeChildWorkflow:async({childWorkflow})=>{selected=childWorkflow;return {status:'succeeded',workflowData:{},outputs:{result:{ok:true}}};},
 });
 assert.equal(result.status,'succeeded');assert.equal(selected?.id,'grandchild');assert.equal(selected?.version,1);
});
