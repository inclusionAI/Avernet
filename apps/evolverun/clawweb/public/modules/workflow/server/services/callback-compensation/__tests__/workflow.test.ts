import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { fixture, type Fixture } from './fixture.js';
let f: Fixture;
beforeEach(async () => { f=fixture(); f.run(); f.config(); await f.authorize(); });
afterEach(async () => { await f.close(); });
const input = { configId:'callback-string-id',flowIds:['flow-1'] };
const createInput = (p: any) => ({ previewId:p.previewId,itemIds:[p.items[0].itemId],reason:'Reviewed notification recovery',requestKey:'request-key-12345' });

describe('durable ownership, freezing, request idempotency', () => {
  it('concurrent identical create calls return the same batch; reason/selection changes conflict', async () => {
    const s=f.service(), p=await s.preview('wf','admin',input), body=createInput(p);
    const results=await Promise.all(Array.from({length:8},()=>s.create('wf','admin',body)));
    expect(new Set(results.map(b=>b.batchId)).size).toBe(1);
    expect(f.raw.prepare('SELECT COUNT(*) n FROM workflow_callback_compensation_batches').get()).toEqual({n:1});
    await expect(s.create('wf','admin',{...body,reason:'A different recovery reason'})).rejects.toMatchObject({code:'REQUEST_KEY_CONFLICT'});
    const another=await s.preview('wf','admin',input);
    await expect(s.create('wf','admin',{...body,previewId:another.previewId})).rejects.toMatchObject({code:'REQUEST_KEY_CONFLICT'});
    f.advance(16*60_000);
    expect((await s.create('wf','admin',body)).batchId).toBe(results[0].batchId);
  });
  it('distinct requests/operators cannot reserve same logical delivery', async () => {
    const s=f.service(), p1=await s.preview('wf','admin',input),p2=await s.preview('wf','admin2',input);
    const results=await Promise.allSettled([s.create('wf','admin',createInput(p1)),s.create('wf','admin2',createInput(p2))]);
    expect(results.filter(r=>r.status==='fulfilled')).toHaveLength(1);
    expect(results.filter(r=>r.status==='rejected')).toHaveLength(1);
    expect(f.raw.prepare('SELECT COUNT(*) n FROM workflow_callback_compensation_items').get()).toEqual({n:1});
  });
  it.each([
    ["CREATE TRIGGER fail_batch BEFORE INSERT ON workflow_callback_compensation_batches BEGIN SELECT RAISE(ABORT, 'storage policy failure'); END",'SQLITE_CONSTRAINT_TRIGGER'],
    ['ALTER TABLE workflow_callback_compensation_batches ADD COLUMN storage_guard INTEGER NOT NULL DEFAULT 1 CHECK(storage_guard=0)','SQLITE_CONSTRAINT_CHECK'],
    ['ALTER TABLE workflow_callback_compensation_batches ADD COLUMN storage_guard TEXT NOT NULL','SQLITE_CONSTRAINT_NOTNULL'],
  ])('propagates non-unique database constraint failure (%s), not a reservation conflict',async(sql,code)=>{
    const s=f.service(),p=await s.preview('wf','admin',input);f.raw.exec(sql);
    await expect(s.create('wf','admin',createInput(p))).rejects.toMatchObject({code});
    expect(f.raw.prepare('SELECT COUNT(*) n FROM workflow_callback_compensation_batches').get()).toEqual({n:0});
    expect(f.raw.prepare('SELECT COUNT(*) n FROM workflow_callback_compensation_items').get()).toEqual({n:0});
    expect(f.calls).toHaveLength(0);
  });
  it('expired preview cannot create new batch', async () => {
    const s=f.service(),p=await s.preview('wf','admin',input); f.advance(15*60_000);
    await expect(s.create('wf','admin',createInput(p))).rejects.toMatchObject({code:'PREVIEW_EXPIRED'});
  });
  it.each(['admin2',''])('preview/batch never usable by another or missing actor %j', async actor => {
    const {service:s,preview:p,batch}=await f.queued();
    await expect(s.create('wf',actor,{...createInput(p),requestKey:'another-key'})).rejects.toMatchObject({status:actor?404:403});
    await expect(s.get('wf',actor,batch.batchId)).rejects.toMatchObject({status:actor?404:403});
    await expect(s.stop('wf',actor,batch.batchId)).rejects.toMatchObject({status:actor?404:403});
  });
  it('cross-workflow and cross-environment ownership is enforced end-to-end', async () => {
    const {service:s,preview:p,batch}=await f.queued();
    await expect(s.create('other','admin',createInput(p))).rejects.toMatchObject({status:404});
    await expect(s.get('other','admin',batch.batchId)).rejects.toMatchObject({status:404});
    const prod=f.service({environment:'prod'});
    await expect(prod.create('wf','admin',createInput(p))).rejects.toMatchObject({status:404});
    await expect(prod.get('wf','admin',batch.batchId)).rejects.toMatchObject({status:404});
    await prod.tick(); expect(f.calls).toHaveLength(0);
    await expect(s.preview('other','admin',input)).rejects.toMatchObject({code:'CONFIG_NOT_FOUND'});
  });
  it.each(['short','x'.repeat(501)])('reason bounds: %j',async reason=>{
    const s=f.service(),p=await s.preview('wf','admin',input);
    await expect(s.create('wf','admin',{...createInput(p),reason})).rejects.toMatchObject({code:'REASON_REQUIRED_10_500'});
  });
  it('cannot select unknown, review or delivered items',async()=>{
    const s=f.service(),p=await s.preview('wf','admin',input);
    await expect(s.create('wf','admin',{...createInput(p),itemIds:['outside-preview']})).rejects.toMatchObject({code:'SELECTION_NOT_ELIGIBLE'});
    f.log(); const delivered=await s.preview('wf','admin',input);
    await expect(s.create('wf','admin',createInput(delivered))).rejects.toMatchObject({code:'SELECTION_NOT_ELIGIBLE'});
  });
  it.each([
    "UPDATE flow_runs SET status='running'", "UPDATE flow_runs SET retry_epoch=retry_epoch+1",
    "UPDATE flow_runs SET result_json='\"changed\"'", "UPDATE node_executions SET output_json='\"changed\"'",
    "UPDATE flow_runs SET current_phase='P7'",
  ])('rejects run/node/generation drift on create: %s',async sql=>{
    const s=f.service(),p=await s.preview('wf','admin',input); f.raw.exec(sql);
    await expect(s.create('wf','admin',createInput(p))).rejects.toMatchObject({code:'RUN_SNAPSHOT_DRIFT'});
    expect(f.calls).toHaveLength(0);
  });
  it.each(["UPDATE http_callback_configs SET secret='rotated'", "UPDATE http_callback_configs SET enabled=0",
    "UPDATE http_callback_configs SET timeout_ms=9000", "UPDATE http_callback_configs SET url='https://other.example.test'",
    "UPDATE http_callback_configs SET include_node_output=1"] )('freezes config including secret/output/url on create: %s',async sql=>{
    const s=f.service(),p=await s.preview('wf','admin',input); f.raw.exec(sql);
    await expect(s.create('wf','admin',createInput(p))).rejects.toMatchObject({code:'CONFIG_DRIFT'});
  });
  it('receiver config version is not delivery identity; rotated/new config cannot unlock reserved delivery',async()=>{
    await f.queued();
    f.config('new-config',{url:'https://cortex.example.test/new-version'});
    const e=f.proofs.get('flow-1')!;e.configIds.push('new-config');e.urls.push('https://cortex.example.test/new-version');e.url='https://cortex.example.test/new-version';
    const p=await f.service().preview('wf','admin',{configId:'new-config',flowIds:['flow-1']});
    expect(p.items[0]).toMatchObject({status:'review',reasons:['DELIVERY_ALREADY_RESERVED']});
  });
  it('all plaintext storage and DTOs exclude body values and signing secrets',async()=>{
    const {batch,preview}=await f.queued();
    const tables=['workflow_callback_compensation_previews','workflow_callback_compensation_batches','workflow_callback_compensation_items'];
    const stored=tables.flatMap(t=>f.raw.prepare(`SELECT * FROM ${t}`).all());
    expect(JSON.stringify([batch,preview,stored])).not.toMatch(/SENSITIVE|SIGNING-SECRET|NEVER-COPY-CREDENTIAL|secret_param/);
    expect(JSON.stringify(stored)).toContain('v1.');
  });
  it('tampered frozen ciphertext cannot execute',async()=>{
    const {batch,service:s}=await f.queued();
    f.raw.exec("UPDATE workflow_callback_compensation_items SET frozen_cipher='v1.invalid.invalid.invalid'");
    await expect(s.tick()).rejects.toMatchObject({code:'PROTECTED_SNAPSHOT_INVALID'});expect(f.calls).toHaveLength(0);
    expect((await s.get('wf','admin',batch.batchId)).counts.queued).toBe(1);
  });
});
