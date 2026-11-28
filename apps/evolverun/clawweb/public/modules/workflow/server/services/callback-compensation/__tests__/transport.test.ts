import { afterEach, expect, it, vi } from 'vitest';
import { fetchTransport } from '../transport.js';
import { protect, unprotect } from '../protection.js';
import { key } from './fixture.js';
afterEach(()=>vi.unstubAllGlobals());
it('native fetch uses exact URL, redirect:error, POST and abort signal; does not read or persist receiver body',async()=>{
  const cancel=vi.fn(async()=>{});const text=vi.fn();const json=vi.fn();
  const fetch=vi.fn(async()=>({status:204,body:{cancel},text,json}));vi.stubGlobal('fetch',fetch);
  const request={url:'https://cortex.example.test/approved/exact?version=one',rawBody:'{"frozen":"body"}',
    headers:{'X-Callback-Config-Id':'string-id'},timeoutMs:5000,redirect:'error' as const,signal:new AbortController().signal};
  expect(await fetchTransport(request)).toEqual({status:204});
  expect(fetch).toHaveBeenCalledTimes(1);expect(fetch).toHaveBeenCalledWith(request.url,{
    method:'POST',headers:request.headers,body:request.rawBody,redirect:'error',signal:request.signal});
  expect(cancel).toHaveBeenCalledTimes(1);expect(text).not.toHaveBeenCalled();expect(json).not.toHaveBeenCalled();
});
it('native redirect/fetch rejection is not retried',async()=>{
  const fetch=vi.fn(async()=>{throw new TypeError('redirect rejected');});vi.stubGlobal('fetch',fetch);
  await expect(fetchTransport({url:'https://cortex.example.test/exact',rawBody:'{}',headers:{},timeoutMs:1000,redirect:'error',signal:new AbortController().signal}))
    .rejects.toThrow('redirect rejected');expect(fetch).toHaveBeenCalledTimes(1);
});
it('AES256GCM uses fresh IV and authenticated owner binding; wrong key/AAD/tamper rejected',()=>{
  const one=protect({sensitive:'never plaintext'},key,'owner1'),two=protect({sensitive:'never plaintext'},key,'owner1');
  expect(one).not.toBe(two);expect(one).not.toContain('never plaintext');expect(unprotect(one,key,'owner1')).toEqual({sensitive:'never plaintext'});
  expect(()=>unprotect(one,key,'owner2')).toThrow('PROTECTED_SNAPSHOT_INVALID');
  expect(()=>unprotect(one,Buffer.alloc(32,1),'owner1')).toThrow('PROTECTED_SNAPSHOT_INVALID');
  const pieces=one.split('.');pieces[3]=Buffer.from('tampered').toString('base64');
  expect(()=>unprotect(pieces.join('.'),key,'owner1')).toThrow('PROTECTED_SNAPSHOT_INVALID');
  const tag=one.split('.');tag[2]=Buffer.from(tag[2],'base64').subarray(0,12).toString('base64');
  expect(()=>unprotect(tag.join('.'),key,'owner1')).toThrow('PROTECTED_SNAPSHOT_INVALID');
});
it('real native fetch never follows a redirect to a second HTTP receiver',async()=>{
  const {createServer}=await import('node:http');let sourceCalls=0,targetCalls=0;
  const target=createServer((_req,res)=>{targetCalls++;res.end('must not receive');});
  await new Promise<void>(resolve=>target.listen(0,'127.0.0.1',resolve));
  const targetPort=(target.address() as {port:number}).port;
  const source=createServer((_req,res)=>{sourceCalls++;res.writeHead(302,{Location:`http://127.0.0.1:${targetPort}/other`});res.end();});
  await new Promise<void>(resolve=>source.listen(0,'127.0.0.1',resolve));
  try{
    await expect(fetchTransport({url:`http://127.0.0.1:${(source.address() as {port:number}).port}/approved`,rawBody:'{}',headers:{},
      redirect:'error',timeoutMs:1000,signal:AbortSignal.timeout(1000)})).rejects.toThrow();
    expect(sourceCalls).toBe(1);expect(targetCalls).toBe(0);
  }finally{await Promise.all([new Promise<void>(resolve=>source.close(()=>resolve())),new Promise<void>(resolve=>target.close(()=>resolve()))]);}
});
