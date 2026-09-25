// @vitest-environment node
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, mkdir, writeFile, readFile, rm, appendFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { WebSocketServer } from "ws";
import { expect, it, vi } from "vitest";
import { ENGINE_DELIVERY_SCRIPT } from "../internal/engine-message-script.js";
const run = promisify(execFile);

it.each([false, true])("fixed sender deduplicates and preserves original execution (Engine detach=%s)", async (supportsDetach) => {
  const home = await mkdtemp(join(tmpdir(), "recovery-engine-"));
  const directory = join(home,".openclaw/agents/main/sessions");
  await mkdir(directory,{recursive:true});
  await writeFile(join(directory,"sessions.json"),JSON.stringify({"agent:main:dashboard:test":{sessionId:"original"}}));
  await writeFile(join(directory,"original.jsonl"),"{}\n");
  const server = new WebSocketServer({host:"127.0.0.1",port:0});
  await new Promise<void>(resolve=>server.once("listening",resolve));
  const address=server.address() as {port:number};
  const requests: any[]=[];
  server.on("connection",socket=>socket.on("message",async raw=>{
    const message=JSON.parse(String(raw)); requests.push(message);
    if(message.method==="chat.send") await appendFile(join(directory,"original.jsonl"), JSON.stringify({
      type:"message",id:"actual-message-id",timestamp:"2026-09-22T00:00:00Z",
      message:{role:"user",content:[{type:"text",text:message.params.message}]},
    })+"\n");
    socket.send(JSON.stringify({type:"res",id:message.id,ok:true,payload:message.method==="chat.send"?{accepted:true,...(supportsDetach?{recovery:"process_local_v1",runId:"engine-rewritten-id"}:{})}:{}}));
  }));
  const deliveryId=randomUUID();
  const input=Buffer.from(JSON.stringify({deliveryId,deliveryKey:"test-key",sessionKey:"agent:main:dashboard:test",message:"[会话自愈：test-key] continue",engineAuthToken:"OPEN_API:app:test-app",program:ENGINE_DELIVERY_SCRIPT,
    engineUrl:`ws://127.0.0.1:${address.port}/ws`})).toString("base64");
  try {
    const execute=()=>run(process.execPath,["-e",ENGINE_DELIVERY_SCRIPT,input],{env:{...process.env,HOME:home},timeout:15000});
    const first=JSON.parse((await execute()).stdout);
    expect(first).toMatchObject({status:"accepted",sessionId:"original",locationStatus:"confirmed",messageId:"actual-message-id",messageTimestamp:"2026-09-22T00:00:00Z"});
    expect(JSON.parse((await execute()).stdout)).toEqual(first);
    expect(requests.filter(r=>r.method==="chat.send")).toHaveLength(1);
    expect(requests[1].params).toMatchObject({sessionKey:"agent:main:dashboard:test",idempotencyKey:deliveryId});
    expect(requests[1].params.resumeEnabled).toBe(true);
    expect(requests[1].params["x-iam-token"]).toBe("OPEN_API:app:test-app");
    expect(JSON.stringify(first)).not.toContain("OPEN_API:");
    expect(await readFile(join(directory,"original.jsonl"),"utf8")).not.toContain("OPEN_API:");
    if(supportsDetach) expect(first.runId).toBe("engine-rewritten-id");
    if (supportsDetach) {
      await vi.waitFor(()=>expect(server.clients.size).toBe(0));
    } else expect(server.clients.size).toBe(1);
    for(const socket of server.clients) socket.send(JSON.stringify({type:"event",event:"chat",payload:{sessionKey:"agent:main:dashboard:test",state:"final"}}));
  } finally {
    for(const socket of server.clients) socket.close();
    await new Promise<void>(resolve=>server.close(()=>resolve()));
    await rm(home,{recursive:true,force:true});
  }
},20000);

it("missing original session fails before attempting a connection",async()=>{
  const home=await mkdtemp(join(tmpdir(),"recovery-missing-"));
  try {
    const input=Buffer.from(JSON.stringify({deliveryId:randomUUID(),deliveryKey:"test-key",sessionKey:"agent:main:dashboard:missing",message:"[会话自愈：test-key] continue",
      program:ENGINE_DELIVERY_SCRIPT,engineUrl:"ws://127.0.0.1:1/ws"})).toString("base64");
    const result=await run(process.execPath,["-e",ENGINE_DELIVERY_SCRIPT,input],{env:{...process.env,HOME:home},timeout:15000});
    expect(JSON.parse(result.stdout).status).toBe("failed");
  } finally { await rm(home,{recursive:true,force:true}); }
},20000);


it("an explicit Engine rejection is failed rather than an ambiguous send",async()=>{
  const home=await mkdtemp(join(tmpdir(),"recovery-rejected-"));
  const directory=join(home,".openclaw/agents/main/sessions");
  await mkdir(directory,{recursive:true});
  await writeFile(join(directory,"sessions.json"),JSON.stringify({"agent:main:test":{sessionId:"original"}}));
  await writeFile(join(directory,"original.jsonl"),"{}\n");
  const server=new WebSocketServer({host:"127.0.0.1",port:0});
  await new Promise<void>(resolve=>server.once("listening",resolve));
  server.on("connection",socket=>socket.on("message",raw=>{
    const message=JSON.parse(String(raw));
    socket.send(JSON.stringify({type:"res",id:message.id,ok:message.method!=="chat.send",payload:{},error:{code:"ZERO_CHECK_FAILED"}}));
  }));
  try{
    const input=Buffer.from(JSON.stringify({deliveryId:randomUUID(),deliveryKey:"test-key",sessionKey:"agent:main:test",message:"[会话自愈：test-key] continue",program:ENGINE_DELIVERY_SCRIPT,
      engineUrl:"ws://127.0.0.1:"+(server.address() as {port:number}).port+"/ws"})).toString("base64");
    const result=await run(process.execPath,["-e",ENGINE_DELIVERY_SCRIPT,input],{env:{...process.env,HOME:home},timeout:15000});
    expect(JSON.parse(result.stdout).status).toBe("failed");
  }finally{
    for(const socket of server.clients)socket.close();
    await new Promise<void>(resolve=>server.close(()=>resolve()));await rm(home,{recursive:true,force:true});
  }
},20000);

it.each(["assistant_only", "duplicate", "reset", "missing_record_id"])("message lookup distinguishes %s without inventing metadata", async variant => {
  const home=await mkdtemp(join(tmpdir(),"recovery-location-"));
  const directory=join(home,".openclaw/agents/main/sessions"),file=join(directory,"original.jsonl"),index=join(directory,"sessions.json");
  await mkdir(directory,{recursive:true});await writeFile(index,JSON.stringify({"agent:main:test":{sessionId:"original"}}));await writeFile(file,"{}\n");
  const server=new WebSocketServer({host:"127.0.0.1",port:0});await new Promise<void>(resolve=>server.once("listening",resolve));
  server.on("connection",socket=>socket.on("message",async raw=>{
    const frame=JSON.parse(String(raw));
    if(frame.method==="chat.send"){
      const record={type:"message",id:variant==="missing_record_id"?undefined:"real-id",timestamp:"2026-09-22T00:00:00Z",
        message:{role:variant==="assistant_only"?"assistant":"user",content:[{type:"text",text:frame.params.message}]}};
      await appendFile(file,(JSON.stringify(record)+"\n").repeat(variant==="duplicate"?2:1));
      if(variant==="reset")await writeFile(index,JSON.stringify({"agent:main:test":{sessionId:"replacement"}}));
    }
    socket.send(JSON.stringify({type:"res",id:frame.id,ok:true,payload:frame.method==="chat.send"?{accepted:true,recovery:"process_local_v1",runId:"rewritten"}:{}}));
  }));
  try{
    const input=Buffer.from(JSON.stringify({deliveryId:randomUUID(),deliveryKey:"test-key",sessionKey:"agent:main:test",message:"[会话自愈：test-key] continue",
      program:ENGINE_DELIVERY_SCRIPT,engineUrl:"ws://127.0.0.1:"+(server.address() as {port:number}).port+"/ws"})).toString("base64");
    const result=JSON.parse((await run(process.execPath,["-e",ENGINE_DELIVERY_SCRIPT,input],{env:{...process.env,HOME:home},timeout:20000})).stdout);
    expect(result.status).toBe("accepted");
    expect(result.locationStatus).toBe(variant==="duplicate"?"ambiguous":variant==="reset"?"session_changed":"unconfirmed");
    expect(result.messageId).toBeUndefined();
  }finally{for(const socket of server.clients)socket.close();await new Promise<void>(resolve=>server.close(()=>resolve()));await rm(home,{recursive:true,force:true});}
},25000);
