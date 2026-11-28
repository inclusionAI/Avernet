import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createHmac } from 'node:crypto';
import { fixture, type Fixture, NOW } from './fixture.js';
import { callbackHeaders } from '../transport.js';
let f: Fixture;
beforeEach(async()=>{f=fixture();f.run();f.config();await f.authorize();});
afterEach(async()=>{await f.close();});
const flush = () => new Promise<void>(resolve=>setImmediate(resolve));

describe('single attempt, durable worker lifecycle',()=>{
  it('multiple instances + concurrent ticks CAS only one POST and one audit',async()=>{
    const {batch,service:s}=await f.queued();const other=f.service();
    await Promise.all([s.tick(),other.tick(),s.tick(),other.tick()]);expect(f.calls).toHaveLength(1);
    expect(await s.get('wf','admin',batch.batchId)).toMatchObject({status:'completed',counts:{received:1,auditPending:0}});
    const logs=f.raw.prepare('SELECT * FROM http_callback_logs').all();expect(logs).toHaveLength(1);
    expect(logs[0]).toMatchObject({status:'delivered',callback_source:'workflow-level',request_body:null,request_headers:null,response_body:null});
    expect(JSON.stringify(logs)).not.toMatch(/SENSITIVE|TEST-TOKEN/);await s.tick();await other.tick();expect(f.calls).toHaveLength(1);
  });
  it('frozen protocol preserves JSON strings, null phase, lineage; never sends credentials',async()=>{
    f.raw.exec("UPDATE flow_runs SET parent_flow_id='real-parent',root_flow_id='real-root'");await f.authorize();
    const {service:s}=await f.queued();await s.tick();const call=f.calls[0],body=JSON.parse(call.rawBody);
    expect(body).toMatchObject({workflow_id:'wf',flow_id:'flow-1',status:'succeeded',ext_info:{flow_runs:{
      status:'succeeded',current_phase:null,parent_flow_id:'real-parent',root_flow_id:'real-root',credentials_json:null,
      result_json:'{"result":"SENSITIVE-RESULT"}',params_json:'{"secret_param":"SENSITIVE-PARAM"}'}}});
    expect(body.ext_info.node_executions[0]).not.toHaveProperty('output_json');expect(call.rawBody).not.toContain('fake');
    expect(call).toMatchObject({url:'https://cortex.example.test/exact',redirect:'error',timeoutMs:1000});
    expect(call.headers['X-Callback-Config-Id']).toBe('callback-string-id');expect(call.headers['X-Callback-Timestamp']).toBe(String(NOW));
    expect(call.headers['X-Callback-Signature-256']).toBe('sha256='+createHmac('sha256','TEST-TOKEN').update(`${NOW}.${call.rawBody}`).digest('hex'));
    expect(f.raw.prepare('SELECT status,current_phase FROM flow_runs').get()).toEqual({status:'succeeded',current_phase:null});
  });
  it('honors includeNodeOutput and caps timeout at 30s',async()=>{
    f.raw.exec('UPDATE http_callback_configs SET include_node_output=1,timeout_ms=90000');const {service:s}=await f.queued();await s.tick();
    expect(JSON.parse(f.calls[0].rawBody).ext_info.node_executions[0].output_json).toBe('{"output":"SENSITIVE-OUTPUT"}');expect(f.calls[0].timeoutMs).toBe(30000);
  });
  it.each([400,408,429,500,503,301])('non-2xx %s stays unknown, no retry',async status=>{
    f.outcome(status);const {batch,service:s}=await f.queued();await s.tick();await s.tick();
    expect((await s.get('wf','admin',batch.batchId)).items[0]).toMatchObject({state:'unknown',httpStatus:status,auditPending:false});expect(f.calls).toHaveLength(1);
  });
  it('network error is unknown without leaking transport error',async()=>{
    let calls=0;const s=f.service({transport:async()=>{calls++;throw new Error('SENSITIVE-network-credentials');}});
    const {batch}=await f.queued(s);await s.tick();await s.tick();const result=await s.get('wf','admin',batch.batchId);
    expect(result.items[0].state).toBe('unknown');expect(JSON.stringify(result)).not.toContain('SENSITIVE');expect(calls).toBe(1);
  });
  it('timeout aborts transport and never retries',async()=>{
    f.raw.exec('UPDATE http_callback_configs SET timeout_ms=5');let calls=0;let signal:AbortSignal;
    const s=f.service({transport:async request=>{calls++;signal=request.signal;return new Promise(()=>{});}});
    const {batch}=await f.queued(s);await s.tick();await s.tick();
    expect(signal!.aborted).toBe(true);expect((await s.get('wf','admin',batch.batchId)).items[0].state).toBe('unknown');expect(calls).toBe(1);
  });
  it('restart resumes queued original bytes after preview expiration',async()=>{
    const {batch,service:s}=await f.queued();await s.close();f.advance(16*60_000);const restarted=f.service();await restarted.tick();
    expect((await restarted.get('wf','admin',batch.batchId)).items[0].state).toBe('received');expect(f.calls).toHaveLength(1);
    expect(JSON.parse(f.calls[0].rawBody).ext_info.flow_runs.result_json).toBe('{"result":"SENSITIVE-RESULT"}');
  });
  it('expired sending recovers unknown, never reclaimed',async()=>{
    const {batch,service:s}=await f.queued();f.raw.prepare("UPDATE workflow_callback_compensation_items SET state='sending',sending_until_ms=?").run(NOW-1);
    await s.close();const restarted=f.service();await restarted.tick();await restarted.tick();
    expect((await restarted.get('wf','admin',batch.batchId)).items[0]).toMatchObject({state:'unknown',reason:'SENDING_LEASE_EXPIRED',auditPending:false});expect(f.calls).toHaveLength(0);
  });
  it('outcome persistence failure after POST leaves sending then unknown, never resend',async()=>{
    const {batch,service:s}=await f.queued();f.raw.exec(`CREATE TRIGGER fail_outcome BEFORE UPDATE ON workflow_callback_compensation_items
      WHEN NEW.state = 'received' BEGIN SELECT RAISE(ABORT, 'outcome storage failure'); END`);
    await expect(s.tick()).rejects.toThrow('outcome storage failure');expect(f.calls).toHaveLength(1);
    f.raw.exec('DROP TRIGGER fail_outcome');await s.tick();expect(f.calls).toHaveLength(1);f.advance(61000);await s.tick();
    expect((await s.get('wf','admin',batch.batchId)).items[0].state).toBe('unknown');expect(f.calls).toHaveLength(1);
  });
  it('received + audit failure is audit-only repaired concurrently after restart',async()=>{
    const {batch,service:s}=await f.queued();f.raw.exec("CREATE TRIGGER fail_audit BEFORE INSERT ON http_callback_logs BEGIN SELECT RAISE(ABORT, 'audit failure'); END");
    await expect(s.tick()).rejects.toThrow('audit failure');expect((await s.get('wf','admin',batch.batchId)).items[0]).toMatchObject({state:'received',auditPending:true});
    expect(f.calls).toHaveLength(1);f.raw.exec('DROP TRIGGER fail_audit');await s.close();const a=f.service(),b=f.service();await Promise.all([a.tick(),b.tick()]);
    expect(f.calls).toHaveLength(1);expect(f.raw.prepare('SELECT COUNT(*) n FROM http_callback_logs').get()).toEqual({n:1});
    expect((await a.get('wf','admin',batch.batchId)).items[0].auditPending).toBe(false);
  });
  it.each([
    ["UPDATE flow_runs SET status='running'",'RUN_SNAPSHOT_DRIFT'],["UPDATE flow_runs SET retry_epoch=1",'RUN_SNAPSHOT_DRIFT'],
    ["UPDATE node_executions SET input_json='\"changed\"'",'RUN_SNAPSHOT_DRIFT'],["UPDATE http_callback_configs SET secret='rotated'",'CONFIG_DRIFT'],
    ["UPDATE http_callback_configs SET enabled=0",'CONFIG_DRIFT'],
  ])('worker rechecks %s; skips instead of rebuilding body',async(sql,reason)=>{
    const {batch,service:s}=await f.queued();f.raw.exec(sql);await s.tick();
    expect((await s.get('wf','admin',batch.batchId)).items[0]).toMatchObject({state:'skipped',reason});expect(f.calls).toHaveLength(0);
  });
  it('detects new legacy delivered audit before first POST',async()=>{
    const {batch,service:s}=await f.queued();f.log();await s.tick();
    expect((await s.get('wf','admin',batch.batchId)).items[0]).toMatchObject({state:'skipped',reason:'RECEIVER_ALREADY_DELIVERED'});expect(f.calls).toHaveLength(0);
  });
  it('stop skips only queued, not sending; repeated stop idempotent',async()=>{
    f.run('flow-2');await f.authorize('flow-2');let release:(v:{status:number})=>void;let calls=0;
    const s=f.service({transport:async()=>{calls++;return new Promise(resolve=>{release=resolve;});}});
    const {batch}=await f.queued(s,['flow-1','flow-2']);const ticking=s.tick();while(!release!)await flush();
    expect(await s.stop('wf','admin',batch.batchId)).toMatchObject({status:'stopped',counts:{sending:1,skipped:1}});
    release!({status:204});await ticking;
    expect(await s.stop('wf','admin',batch.batchId)).toMatchObject({status:'stopped',counts:{received:1,skipped:1}});expect(calls).toBe(1);
  });
  it('close waits for active POST/outcome, then prevents further work',async()=>{
    let release:(v:{status:number})=>void;let calls=0;const s=f.service({transport:async()=>{calls++;return new Promise(resolve=>{release=resolve;});}});
    const {batch}=await f.queued(s);const ticking=s.tick();while(!release!)await flush();let closed=false;
    const closing=s.close().then(()=>{closed=true;});await flush();expect(closed).toBe(false);release!({status:204});await ticking;await closing;await s.tick();expect(calls).toBe(1);
    expect((await f.service().get('wf','admin',batch.batchId)).items[0].state).toBe('received');
  });
  it.each([false,true])('slow evidence cannot POST after claim expires (peer recovery=%s)',async peer=>{
    let release:()=>void;let slow=false;const gate=new Promise<void>(resolve=>{release=resolve;});
    const s=f.service({evidence:{async resolve(q){if(slow)await gate;return f.evidence.resolve(q);}}});
    const {batch}=await f.queued(s);slow=true;const ticking=s.tick();
    while((f.raw.prepare('SELECT state FROM workflow_callback_compensation_items').get() as any).state!=='sending')await flush();
    f.advance(61000);if(peer)await f.service().tick();release!();await ticking;
    expect(f.calls).toHaveLength(0);expect((await s.get('wf','admin',batch.batchId)).items[0].state).toBe('unknown');
    await f.service().tick();expect(f.calls).toHaveLength(0);
  });
  it('stop during slow preflight cannot start a late POST',async()=>{
    let release:()=>void;let slow=false;const gate=new Promise<void>(resolve=>{release=resolve;});
    const s=f.service({evidence:{async resolve(q){if(slow)await gate;return f.evidence.resolve(q);}}});
    const {batch}=await f.queued(s);slow=true;const ticking=s.tick();
    while((f.raw.prepare('SELECT state FROM workflow_callback_compensation_items').get() as any).state!=='sending')await flush();
    await s.stop('wf','admin',batch.batchId);release!();await ticking;
    expect(f.calls).toHaveLength(0);expect((await s.get('wf','admin',batch.batchId)).items[0]).toMatchObject({state:'skipped',reason:'ADMIN_STOP_BEFORE_POST'});
  });
  it.each(["UPDATE flow_runs SET status='running'", "UPDATE http_callback_configs SET secret='rotated'"])
    ('drift during slow evidence resolution is rejected: %s',async sql=>{
    let release:()=>void;let slow=false;const gate=new Promise<void>(resolve=>{release=resolve;});
    const s=f.service({evidence:{async resolve(q){if(slow)await gate;return f.evidence.resolve(q);}}});
    const {batch}=await f.queued(s);slow=true;const ticking=s.tick();
    while((f.raw.prepare('SELECT state FROM workflow_callback_compensation_items').get() as any).state!=='sending')await flush();
    f.raw.exec(sql);release!();await ticking;
    expect(f.calls).toHaveLength(0);expect((await s.get('wf','admin',batch.batchId)).items[0].state).toBe('skipped');
  });
  it('approved exact URL is preserved for POST but path/query tokens never reach audit or DTOs',async()=>{
    const url='https://cortex.example.test/SENSITIVE-PATH?token=fake';
    f.raw.prepare('UPDATE http_callback_configs SET url=?').run(url);
    await f.authorize('flow-1',{url,urls:[url]});const {batch,preview,service:s}=await f.queued();await s.tick();
    expect(f.calls[0].url).toBe(url);expect(JSON.stringify([preview,batch,await s.get('wf','admin',batch.batchId)])).not.toMatch(/SENSITIVE-PATH|token=fake/);
    expect(f.raw.prepare('SELECT callback_url FROM http_callback_logs').get()).toEqual({callback_url:'https://cortex.example.test'});
  });
  it('unsigned callbacks retain string config identity but no signature',()=>{
    expect(callbackHeaders('string-id','', '{}',NOW)).toEqual({'Content-Type':'application/json','X-Callback-Config-Id':'string-id'});
  });
});
