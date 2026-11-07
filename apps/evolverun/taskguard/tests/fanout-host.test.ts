import assert from 'node:assert/strict';
import { test } from 'node:test';
import { authorizeFanoutExecution } from '../src/platform/fanout-host.js';
import type { IApiClient } from '../src/db/api-client/types.js';

test('child execution authorization requires an explicit grant and preserves bot ownership', async()=>{
 const calls: unknown[]=[];
 const api={post:async(path: string,body:unknown)=>{calls.push([path,body]);return {ok:true,data:{allowed:true},status:200};}} as unknown as IApiClient;
 await authorizeFanoutExecution(api,'bot','owner','child');
 assert.deepEqual(calls, [['/api/internal/bot-workflow-permissions/check',{botId:'bot',botOwnerId:'owner',workflowId:'child',permission:'execute'}]]);
});
for(const response of [{ok:true,data:{allowed:false,hasRecords:false}},{ok:false,data:null,status:503},{ok:true,data:{}},{ok:true,data:{allowed:'true'}}]){
 test(`child execution authorization fails closed: ${JSON.stringify(response)}`,async()=>{
  const api={post:async()=>response} as unknown as IApiClient;
  await assert.rejects(authorizeFanoutExecution(api,'bot','owner','child'),e=>(e as {detail:{category:string}}).detail.category==='authorization');
 });
}
test('missing execution principal does not ask the service to infer one',async()=>{
 let called=false;const api={post:async()=>{called=true;throw Error('unexpected');}} as unknown as IApiClient;
 await assert.rejects(authorizeFanoutExecution(api,undefined,'owner','child'));
 assert.equal(called,false);
});
