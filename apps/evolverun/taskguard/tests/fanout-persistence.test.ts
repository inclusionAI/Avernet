import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:http';
import { ApiClient } from '../src/db/api-client.js';
import { FanoutApiPersistence } from '../src/platform/fanout-persistence.js';

async function withServer(handler: (method: string, path: string, body: any) => { status: number; body: unknown }, run: (store: FanoutApiPersistence) => Promise<void>) {
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const output = handler(req.method!, req.url!, raw ? JSON.parse(raw) : undefined);
    res.writeHead(output.status, {'content-type': 'application/json'}); res.end(JSON.stringify(output.body));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address() as {port: number};
  try { await run(new FanoutApiPersistence(new ApiClient({baseUrl: `http://127.0.0.1:${address.port}`, maxRetries: 1}))); }
  finally { await new Promise<void>((resolve, reject) => server.close(e => e ? reject(e) : resolve())); }
}

test('fanout persistence reads an absent group without hiding parent cancellation', async () => {
  await withServer(() => ({status: 200, body: {success: true, data: {group: null, parentStatus: 'cancelled'}}}), async store => {
    assert.deepEqual(await store.readGroup('parent', 'group'), {group: null, parentStatus: 'cancelled'});
  });
});

test('fanout persistence does not convert a failed read into not-found', async () => {
  await withServer(() => ({status: 503, body: {success: false, error: 'unavailable'}}), async store => {
    await assert.rejects(store.readGroup('parent', 'group'), /503|unavailable/);
  });
});

test('fanout persistence rejects a lost CAS and does not report success', async () => {
  await withServer(() => ({status: 409, body: {success: false, error: 'revision conflict'}}), async store => {
    await assert.rejects(store.saveGroup({parentFlowId:'p',groupNodeId:'n',revision:2} as never, 1, {} as never), /409|conflict/);
  });
});

test('fanout persistence distinguishes absent child from an unavailable child read', async () => {
  let unavailable = false;
  await withServer(() => unavailable
    ? {status: 500, body: {success:false,error:'database offline'}}
    : {status: 404, body: {success:false,error:'not found'}}, async store => {
    assert.equal(await store.readChild('p','c'), null);
    unavailable = true;
    await assert.rejects(store.readChild('p','c'), /offline|500/);
  });
});

test('stable child requests carry identity and checkpoint revision in the internal contract', async () => {
  const seen: {method:string;path:string;body:any}[] = [];
  const child = {flowId:'child',workflowId:'wf',status:'running',sessionKey:'fanout:child',stateJson:'{}',revision:1};
  await withServer((method,path,body) => { seen.push({method,path,body}); return {status:200,body:{success:true,data:path.endsWith('/state')?{...child,revision:2}:{created:true,run:child}}}; }, async store => {
    const result=await store.startChild('parent',{childFlowId:'child',parentNodeId:'group',groupExecutionId:'execution',itemId:'a/p',attemptNumber:1,sessionKey:'fanout:child',childWorkflow:{id:'wf'},params:{}} as never, '{}', {originBotId:'bot:owner'});
    assert.equal(result.created,true); assert.equal(result.run.flowId,'child');
    const updated=await store.checkpointChild('parent','child',1,'{"nodeStates":{}}','running','first');
    assert.equal(updated.revision,2);
    assert.equal(seen[0].body.groupExecutionId,'execution');
    assert.equal(seen[0].body.nodeId,'group');
    assert.equal(seen[1].body.expectedRevision,1);
    assert.equal(seen[1].method,'PUT');
    assert.match(seen[1].path,/\/fanout-children\/child\/state$/);
  });
});
