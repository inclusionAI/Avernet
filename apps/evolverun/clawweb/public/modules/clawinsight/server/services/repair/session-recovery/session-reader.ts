import { BotRuntimeError } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { BotRuntime, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { redactEvidenceValue } from "@avernet/clawweb-shared/server/services/redaction";
import { SessionRecoveryError } from "./errors.js";
import { gzipSync } from "node:zlib";
import { cronRunSessionId, cronConversationKey } from "./session-identity.js";

const HEADER = String.raw`
const fs=require('node:fs'), path=require('node:path'), readline=require('node:readline');
const input=JSON.parse(require('node:zlib').gunzipSync(Buffer.from(process.argv[1],'base64')).toString('utf8'));
async function readHistorical(file,sessionId,requireHeader=false){
  const cutoff=input.cutoff, from=input.fromLine||1, messages=[];
  let line=0,size=0,endLine=null,nextLine=null,pageClosed=false;
  for await(const text of readline.createInterface({input:fs.createReadStream(file),crlfDelay:Infinity})){
    line++;
    if(Buffer.byteLength(text)>80000) throw Error('historical session record exceeds read budget');
    let record;try{record=JSON.parse(text)}catch{continue}
    if(!record||typeof record!=='object')continue;
    if(line===1&&requireHeader&&(record.type!=='session'||record.id!==sessionId))throw Error('historical session header mismatch');
    if(record.type==='session'&&record.id!==sessionId)throw Error('historical session header mismatch');
    if(record.sessionId&&record.sessionId!==sessionId)throw Error('historical session identity mismatch');
    const rawTime=record.timestamp??record.message?.timestamp;
    const at=typeof rawTime==='number'?rawTime:Date.parse(rawTime);
    if(Number.isFinite(at)&&at>cutoff.atMs)throw Error('historical cutoff is not in the permitted time range');
    if(line>=from&&!pageClosed){
      if(messages.length>=input.limit||size+Buffer.byteLength(text)>80000)pageClosed=true;
      else {
      messages.push({line_no:line,record});size+=Buffer.byteLength(text);nextLine=line+1;}
    }
    if((record.id??record.uuid)===cutoff.messageId){endLine=line;break;}
  }
  if(endLine===null)throw Error('historical cutoff message unavailable');
  if(!messages.length&&from<=endLine)throw Error('historical session page exceeds read budget');
  return {source:file,session_id:sessionId,delivery_key:input.deliveryKey,messages,failure_context:[],
    delivery:null,delivery_confirmed:false,delivery_matches:[],next_line:nextLine!==null&&nextLine<=endLine?nextLine:null,
    historical_boundary:{message_id:cutoff.messageId,cutoff_at_ms:cutoff.atMs,end_line:endLine},
    readCoverage:{startLine:from,endLine:messages.at(-1)?.line_no??null,hasMore:nextLine!==null&&nextLine<=endLine},transport:'shell'};
}
(async()=>{
`;
const CC_PROGRAM = String.raw`    const id=/(?:^|:)session:([A-Za-z0-9_-]+)(?::|$)/.exec(input.sessionKey)?.[1]||input.sessionKey;
    if(!/^[A-Za-z0-9_-]+$/.test(id)) throw Error('CC session mapping unavailable');
    if(input.expectedSessionId&&input.expectedSessionId!==id) throw Error('CC session changed');
    const projects=fs.realpathSync(path.join(process.env.CLAUDE_CONFIG_DIR||path.join(process.env.HOME||'/home/admin','.claude'),'projects'));
    const candidates=fs.readdirSync(projects,{withFileTypes:true}).filter(x=>x.isDirectory()).map(x=>path.join(projects,x.name,id+'.jsonl')).filter(x=>fs.existsSync(x));
    if(candidates.length!==1) throw Error('CC session mapping unavailable or ambiguous');
    const file=fs.realpathSync(candidates[0]); if(!file.startsWith(projects+'/')) throw Error('CC session outside scope');
    if(input.cutoff){console.log(JSON.stringify(await readHistorical(file,id)));return;}
    const messages=[],matches=[]; let line=0,size=0;
    for await(const text of readline.createInterface({input:fs.createReadStream(file),crlfDelay:Infinity})){
      line++; if(Buffer.byteLength(text)>80000) continue; let record;try{record=JSON.parse(text)}catch{continue}
      const msg=record.message||{},content=typeof msg.content==='string'?msg.content:Array.isArray(msg.content)?msg.content.filter(p=>p.type==='text').map(p=>p.text).join('\n'):'';
      if((msg.role||record.type)==='user'&&content.includes(input.marker+'\n')) matches.push({message_id:record.uuid||record.id||null,line_no:line,text:content});
      if(line>=(input.fromLine||1)){if(size+Buffer.byteLength(text)>80000)break;messages.push({line_no:line,record});size+=Buffer.byteLength(text);if(messages.length>=input.limit)break;}
    }
    if(input.expectedSessionId&&input.expectedSessionId!==id)throw Error('CC session changed');
    console.log(JSON.stringify({session_id:id,source:file,messages,delivery:matches[0]||null,delivery_matches:matches,delivery_confirmed:matches.length>0,next_line:line+1,transport:'shell'})); return;
`;
const OC_PROGRAM = String.raw`  const match=/^agent:([A-Za-z0-9_-]+):.+/.exec(input.sessionKey);
  if(!match) throw Error('invalid session key');
  const state=process.env.OPENCLAW_STATE_DIR||path.join(process.env.HOME||'/home/admin','.openclaw');
  const root=fs.realpathSync(path.join(state,'agents',match[1],'sessions'));
  const indexPath=path.join(root,'sessions.json');
  const store=fs.existsSync(indexPath)?JSON.parse(fs.readFileSync(indexPath,'utf8')):{};
  let indexedKey=input.sessionKey, entry=store[indexedKey];
  if(input.requireIndex&&input.conversationKey!==indexedKey&&store[input.conversationKey]?.sessionId===input.expectedSessionId){
    indexedKey=input.conversationKey;entry=store[indexedKey];
  }
  if(input.cutoff&&input.expectedSessionId&&entry?.sessionId!==input.expectedSessionId)entry=undefined;
  if(input.requireIndex&&!entry) throw Error('original session is not indexed for message delivery');
  const sessionId=entry?entry.sessionId:input.expectedSessionId;
  if(typeof sessionId!=='string'||!/^[A-Za-z0-9_-]+$/.test(sessionId)) throw Error('session not found');
  if(input.expectedSessionId&&input.expectedSessionId!==sessionId) throw Error('session changed');
  const file=fs.realpathSync(entry?.sessionFile?path.resolve(root,entry.sessionFile):path.join(root,sessionId+'.jsonl'));
  if(path.dirname(file)!==root) throw Error('session path outside scope');
  if(input.cutoff){console.log(JSON.stringify(await readHistorical(file,sessionId,!entry)));return;}
  const stream=fs.createReadStream(file), reader=readline.createInterface({input:stream,crlfDelay:Infinity});
  let line=0, size=0, beforeSize=0, found=false, located=null, matches=[], messages=[], before=[], headerVerified=!!entry;
  try { for await(const text of reader){
    line++; const lineBytes=Buffer.byteLength(text,'utf8'); if(lineBytes>80000) continue;
    let record; try{record=JSON.parse(text)}catch{if(!entry&&line===1)throw Error('invalid session header');continue}
    if(!entry&&line===1&&(record.type!=='session'||record.id!==sessionId)) throw Error('session header mismatch');
    if(!entry&&line===1) headerVerified=true;
    const item={line_no:line,record};
    const msg=record.type==='message'?record.message:null;
    const content=typeof msg?.content==='string'?msg.content:Array.isArray(msg?.content)?msg.content.filter(x=>x.type==='text'&&typeof x.text==='string').map(x=>x.text).join('\n'):'';
    // Engine prepends conversation metadata/timestamps to actual user messages.
    if(msg?.role==='user'&&content.includes(input.marker+'\n')){
      found=true; const delivery={message_id:record.id||null,line_no:line,text:content};
      matches.push({message_id:delivery.message_id,line_no:line}); if(!located) located=delivery;
    }
    const select=input.fromLine?line>=input.fromLine:found;
    if(select){ if(size+lineBytes>80000) break; messages.push(item); size+=lineBytes; if(messages.length>=input.limit) break; }
    else { before.push(item); beforeSize+=lineBytes; while(before.length>10||beforeSize>8000){const removed=before.shift(); beforeSize-=Buffer.byteLength(JSON.stringify(removed.record),'utf8');} }
  }}finally{reader.close();stream.destroy()}
  if(!headerVerified) throw Error('missing session header');
  const after=fs.existsSync(indexPath)?JSON.parse(fs.readFileSync(indexPath,'utf8'))[indexedKey]:undefined;
  if(entry?after?.sessionId!==sessionId:after&&after.sessionId!==sessionId) throw Error('session changed while reading');
  if(!input.fromLine&&!found) messages=before;
  console.log(JSON.stringify({session_id:sessionId,session_key:indexedKey,source:file,delivery:located,messages,
    failure_context:input.fromLine?[]:before,next_line:line+1,delivery_confirmed:!!located,delivery_matches:matches}));
`;
const FOOTER = String.raw`})().catch(error=>{console.error(String(error.code||'')+': '+String(error.message||'original session unavailable or changed'));process.exitCode=1});
`;
function quote(value: string): string { return `'${value.replaceAll("'", `'"'"'`)}'`; }
function unavailable(detail: unknown): SessionRecoveryError {
  return new SessionRecoveryError(503, "original_session_unavailable", `原会话暂不可读或已变化，不能判断效果：${String(redactEvidenceValue(detail)).slice(0, 500)}`);
}
/** OC/CC original-session reads and delivery matching use the controlled relay. */
export class RecoverySessionReader {
  constructor(private readonly runtime: Pick<BotRuntime, "executeShell">) {}
  async read(input: { target: ResolvedBotTarget; sessionKey: string; deliveryKey: string; expectedSessionId?: string; requireIndex?: boolean; fromLine?: number; offset?: number; limit?: number; cutoff?: { messageId: string; atMs: number } }) {
    const limit = input.limit ?? 100;
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 500 || (input.fromLine !== undefined && (!Number.isSafeInteger(input.fromLine) || input.fromLine < 1))) {
      throw new SessionRecoveryError(422, "invalid_session_range", "会话读取范围无效");
    }
    if (input.target.activeEngine === "teclaw") throw new SessionRecoveryError(422, "unsupported_session_read", "TE 通过 trajectory_read 读取 AIVision，不提供会话 API 读取");
    if (input.cutoff && (!/^[A-Za-z0-9_.:-]+$/.test(input.cutoff.messageId) || !Number.isSafeInteger(input.cutoff.atMs) || input.cutoff.atMs <= 0)) {
      throw new SessionRecoveryError(422, "invalid_session_cutoff", "历史会话截止节点无效");
    }
    const cronId = input.target.activeEngine === "openclaw" ? cronRunSessionId(input.sessionKey) : undefined;
    if (cronId && input.expectedSessionId && cronId !== input.expectedSessionId) throw unavailable("Cron session ID mismatch");
    const encoded = gzipSync(Buffer.from(JSON.stringify({ sessionKey: input.sessionKey, engine: input.target.activeEngine, marker: `[会话自愈：${input.deliveryKey}]`,
      expectedSessionId: input.expectedSessionId ?? cronId, conversationKey: cronConversationKey(input.sessionKey, input.expectedSessionId ?? cronId), requireIndex: input.requireIndex, fromLine: input.fromLine, limit, deliveryKey: input.deliveryKey, ...(input.cutoff ? { cutoff: input.cutoff } : {}) }))).toString("base64");
    // Compress the fixed read-only program so runtime-user wrapping stays within BaaS's command limit.
    const loader = `eval(require("node:zlib").gunzipSync(Buffer.from("${gzipSync(HEADER + (input.target.activeEngine === "claude_code" ? CC_PROGRAM : OC_PROGRAM) + FOOTER).toString("base64")}","base64")).toString())`;
    const result = await this.runtime.executeShell({ target: input.target, command: `node -e ${quote(loader)} ${quote(encoded)}`, timeoutMs: 30000 })
      .catch((error: unknown) => { if (error instanceof BotRuntimeError && error.code === "target_unavailable") throw new SessionRecoveryError(410, error.code, "目标运行环境已不可用");
        const cause = error as { stderr?: string; code?: string }; throw unavailable(cause?.stderr || cause?.code || "受控 Shell 请求失败"); });
    if (result.status !== "success" || result.exitCode !== 0) throw unavailable(result.stderr || `Shell exit=${result.exitCode}, status=${result.status}`);
    try { return redactEvidenceValue(JSON.parse(result.stdout)) as Record<string, unknown>; }
    catch { throw new SessionRecoveryError(503, "original_session_unavailable", "原会话记录格式无效"); }
  }
}
