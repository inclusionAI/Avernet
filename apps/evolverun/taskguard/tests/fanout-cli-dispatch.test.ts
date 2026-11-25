import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createDefaultExecutorDispatch } from '../src/platform/default-executor.js';
import { createActionRegistry } from '../src/actions/registry.js';
import type { FlowState, WorkflowNode } from '../src/types.js';

const state={params:{scene:'s'},subworkflowMeta:{parentFlowId:'p',parentNodeId:'n',depth:1,fanout:{childFlowId:'c'}},fanoutRuntimeContext:{stateDir:'isolated',launchIdempotencyKey:'key',sessionKey:'child'}} as unknown as FlowState;
const node={id:'launch',executor:{type:'cli-script',command:'python3 launch.py',args:{scene:'{{params.scene}}',state:'{{runtime.stateDir}}'},outputMode:'json'}} as WorkflowNode;
test('fanout MCP cli nodes resolve mapped arguments and explicit JSON outcomes',async()=>{
 let argv: string[]=[];
 const dispatch=createDefaultExecutorDispatch({sessionKey:'child',actionRegistry:createActionRegistry(),commandRunnerFn:async options=>{argv=options.argv;return {code:0,stdout:'{"experimentId":"exp"}',stderr:''};}});
 const result=await dispatch(node,{params:state.params,runtime:state.fanoutRuntimeContext},state,'c');
 assert.deepEqual(argv,['python3','launch.py','--scene','s','--state','isolated']);assert.deepEqual(result.result,{experimentId:'exp'});
});
test('confirmed script JSON failure retains the structured outcome for the collector',async()=>{
 const detail={category:'execution',code:'GATEWAY_CONNECT_FAILED',message:'not submitted',acceptanceKnown:true};
 const dispatch=createDefaultExecutorDispatch({sessionKey:'child',actionRegistry:createActionRegistry(),commandRunnerFn:async()=>({code:0,stdout:JSON.stringify({success:false,fanoutError:detail}),stderr:''})});
 const result=await dispatch(node,{params:state.params,runtime:state.fanoutRuntimeContext},state,'c');
 assert.equal(result.status,'failed');assert.deepEqual(result.result?.fanoutError,detail);
});
