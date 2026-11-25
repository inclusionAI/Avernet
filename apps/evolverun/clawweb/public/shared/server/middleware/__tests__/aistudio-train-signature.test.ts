import express from 'express';
import { once } from 'node:events';
import { gzipSync } from 'node:zlib';
import { generateKeyPairSync, sign } from 'node:crypto';
import { afterEach, describe, expect, it } from 'vitest';
import { captureAIStudioRawBody, createAIStudioServiceAuthenticator } from '../aistudio-train-signature.js';
const key = generateKeyPairSync('ed25519');
const publicKeyB64 = key.publicKey.export({format:'der',type:'spki'}).toString('base64');
const servers: ReturnType<express.Application['listen']>[] = [];
afterEach(async () => { await Promise.all(servers.splice(0).map(s => new Promise<void>(resolve => s.close(() => resolve())))); });
async function fixture(config = publicKeyB64, capture = true) {
  const auth = createAIStudioServiceAuthenticator({ publicKeyB64:config, now:() => 1000000 });
  const app = express(); app.use(express.json({ verify:capture ? captureAIStudioRawBody : undefined }));
  app.use(async (req,res) => {
    try { res.status(await auth(req) ? 200 : 401).json({}); }
    catch { res.status(503).json({}); }
  });
  const server = app.listen(0,'127.0.0.1'); servers.push(server); await once(server,'listening');
  return `http://127.0.0.1:${(server.address() as {port:number}).port}/api/internal/aistudio-train-watches/create`;
}
function headers(body = '', timestamp = '1000000') { return { 'content-type':'application/json', 'x-timestamp':timestamp,
  'x-signature':sign(null,Buffer.from(`${timestamp}.${body}`),key.privateKey).toString('base64') }; }
describe('AIStudio Ed25519 service auth, no dev bypass', () => {
  it('rejects missing configuration, invalid keys, unsigned and forged user headers', async () => {
    expect((await fetch(await fixture(''))).status).toBe(503);
    expect((await fetch(await fixture('not-a-key'))).status).toBe(503);
    expect((await fetch(await fixture(),{headers:{'x-user-id':'admin',cookie:'user=admin'}})).status).toBe(401);
  });
  it('verifies exact raw JSON whitespace/UTF8 bytes, never a reserialized object', async () => {
    const url = await fixture(); const body = '{ "watch": "训练" }';
    expect((await fetch(url,{method:'POST',body,headers:headers(body)})).status).toBe(200);
    expect((await fetch(url,{method:'POST',body,headers:headers(JSON.stringify(JSON.parse(body)))})).status).toBe(401);
  });
  it('rejects compressed bodies rather than authenticating parser-inflated bytes as raw wire JSON', async () => {
    const url=await fixture(); const body='{"watch":"train"}';
    expect((await fetch(url,{method:'POST',body:gzipSync(body),headers:{...headers(body),'content-encoding':'gzip'}})).status).toBe(401);
  });
  it('requires parser-captured raw bytes for POST and uses empty body for GET', async () => {
    expect((await fetch(await fixture(publicKeyB64,false),{method:'POST',body:'{}',headers:headers('{}')})).status).toBe(401);
    expect((await fetch(await fixture(),{headers:headers()})).status).toBe(200);
  });
  it('rejects stale, future, seconds timestamps and modified bodies/signatures', async () => {
    const url=await fixture();
    for (const timestamp of ['0','5000000','1000','1000000.0']) expect((await fetch(url,{headers:headers('',timestamp)})).status).toBe(401);
    expect((await fetch(url,{method:'POST',body:'{}',headers:headers('{"x":1}')})).status).toBe(401);
    expect((await fetch(url,{headers:{...headers(),'x-signature':'abc'}})).status).toBe(401);
  });
});
