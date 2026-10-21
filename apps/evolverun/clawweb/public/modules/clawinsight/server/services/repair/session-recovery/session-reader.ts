import type { BotRuntime, ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { redactEvidenceValue } from "@avernet/clawweb-shared/server/services/redaction";
import { SessionRecoveryError } from "./errors.js";
import { gzipSync } from "node:zlib";

const HEADER = String.raw`
const fs=require('node:fs'), path=require('node:path'), readline=require('node:readline');
const input=JSON.parse(require('node:zlib').gunzipSync(Buffer.from(process.argv[1],'base64')).toString('utf8'));
(async()=>{
`;
const CC_PROGRAM = String.raw`    const id=/(?:^|:)session:([A-Za-z0-9_-]+)(?::|$)/.exec(input.sessionKey)?.[1]||input.sessionKey;
    if(!/^[A-Za-z0-9_-]+$/.test(id)) throw Error('CC session mapping unavailable');
    const projects=fs.realpathSync(path.join(process.env.CLAUDE_CONFIG_DIR||path.join(process.env.HOME||'/home/admin','.claude'),'projects'));
    const candidates=fs.readdirSync(projects,{withFileTypes:true}).filter(x=>x.isDirectory()).map(x=>path.join(projects,x.name,id+'.jsonl')).filter(x=>fs.existsSync(x));
    if(candidates.length!==1) throw Error('CC session mapping unavailable or ambiguous');
    const file=fs.realpathSync(candidates[0]); if(!file.startsWith(projects+'/')) throw Error('CC session outside scope');
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
  const entry=JSON.parse(fs.readFileSync(indexPath,'utf8'))[input.sessionKey];
  const sessionId=entry?.sessionId;
  if(typeof sessionId!=='string'||!/^[A-Za-z0-9_-]+$/.test(sessionId)) throw Error('session not found');
  if(input.expectedSessionId&&input.expectedSessionId!==sessionId) throw Error('session changed');
  const file=fs.realpathSync(entry.sessionFile?path.resolve(root,entry.sessionFile):path.join(root,sessionId+'.jsonl'));
  if(path.dirname(file)!==root) throw Error('session path outside scope');
  const stream=fs.createReadStream(file), reader=readline.createInterface({input:stream,crlfDelay:Infinity});
  let line=0, size=0, beforeSize=0, found=false, located=null, matches=[], messages=[], before=[];
  try { for await(const text of reader){
    line++; const lineBytes=Buffer.byteLength(text,'utf8'); if(lineBytes>80000) continue;
    let record; try{record=JSON.parse(text)}catch{continue}
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
  if(JSON.parse(fs.readFileSync(indexPath,'utf8'))[input.sessionKey]?.sessionId!==sessionId) throw Error('session changed while reading');
  if(!input.fromLine&&!found) messages=before;
  console.log(JSON.stringify({session_id:sessionId,source:file,delivery:located,messages,
    failure_context:input.fromLine?[]:before,next_line:line+1,delivery_confirmed:!!located,delivery_matches:matches}));
`;
const FOOTER = String.raw`})().catch(error=>{console.error(String(error.code||'')+': '+String(error.message||'original session unavailable or changed'));process.exitCode=1});
`;
function quote(value: string): string { return `'${value.replaceAll("'", `'"'"'`)}'`; }
function unavailable(detail: unknown): SessionRecoveryError {
  return new SessionRecoveryError(503, "original_session_unavailable", `原会话暂不可读或已变化，不能判断效果：${String(redactEvidenceValue(detail)).slice(0, 500)}`);
}
/** Original-session reads and delivery matching run inside the target through the controlled relay. */
export class RecoverySessionReader {
  constructor(private readonly runtime: Pick<BotRuntime, "executeShell"> & Partial<Pick<BotRuntime, "request">>) {}
  async read(input: { target: ResolvedBotTarget; sessionKey: string; deliveryKey: string; expectedSessionId?: string; fromLine?: number; offset?: number; limit?: number }) {
    const limit = input.limit ?? 100;
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 500 || (input.fromLine !== undefined && (!Number.isSafeInteger(input.fromLine) || input.fromLine < 1))) {
      throw new SessionRecoveryError(422, "invalid_session_range", "会话读取范围无效");
    }
    if (input.target.activeEngine === "teclaw") return this.readTeclaw(input, limit);
    const encoded = gzipSync(Buffer.from(JSON.stringify({ sessionKey: input.sessionKey, engine: input.target.activeEngine, marker: `[会话自愈：${input.deliveryKey}]`,
      expectedSessionId: input.expectedSessionId, fromLine: input.fromLine, limit }))).toString("base64");
    // Compress the fixed read-only program so runtime-user wrapping stays within BaaS's command limit.
    const loader = `eval(require("node:zlib").gunzipSync(Buffer.from("${gzipSync(HEADER + (input.target.activeEngine === "claude_code" ? CC_PROGRAM : OC_PROGRAM) + FOOTER).toString("base64")}","base64")).toString())`;
    const result = await this.runtime.executeShell({ target: input.target, command: `node -e ${quote(loader)} ${quote(encoded)}`, timeoutMs: 30000 })
      .catch((error: unknown) => { const cause = error as { stderr?: string; code?: string }; throw unavailable(cause?.stderr || cause?.code || "受控 Shell 请求失败"); });
    if (result.status !== "success" || result.exitCode !== 0) throw unavailable(result.stderr || `Shell exit=${result.exitCode}, status=${result.status}`);
    try { return redactEvidenceValue(JSON.parse(result.stdout)) as Record<string, unknown>; }
    catch { throw new SessionRecoveryError(503, "original_session_unavailable", "原会话记录格式无效"); }
  }
  private async readTeclaw(input: { target: ResolvedBotTarget; sessionKey: string; deliveryKey: string; expectedSessionId?: string; offset?: number }, limit: number) {
    if (!this.runtime.request) throw new SessionRecoveryError(503, "original_session_unavailable", "TE 会话接口未配置");
    const offset = input.offset ?? 0;
    if (!Number.isSafeInteger(offset) || offset < 0) throw new SessionRecoveryError(422, "invalid_session_range", "会话 offset 无效");
    const encoded = Buffer.from(input.sessionKey).toString("base64url");
    const response = await this.runtime.request({ target: input.target, method: "GET",
      path: `/api/sessions/${encodeURIComponent(encoded)}/messages?engine=teclaw&limit=${limit}&offset=${offset}`, timeoutMs: 30000 });
    const body = response.body as { success?: boolean; data?: unknown; total?: number; detail?: unknown };
    if (response.status !== 200 || !body || body.success === false || !Array.isArray(body.data)) {
      const detail = typeof body?.detail === "string" ? String(redactEvidenceValue(body.detail)).slice(0, 300) : "响应不符合 success/data 消息数组合同";
      throw new SessionRecoveryError(response.status >= 400 ? response.status : 502,
        response.status === 404 ? "original_session_not_found" : "original_session_unavailable",
        `TE 会话查询 HTTP ${response.status}：${detail}；使用 AIVision 轨迹调查，当前接口缺失会话不证明任务未执行`);
    }
    if (input.expectedSessionId && input.expectedSessionId !== input.sessionKey || body.data.some((record: Record<string, unknown>) => record.session_id && record.session_id !== input.sessionKey)) throw new SessionRecoveryError(409, "original_session_changed", "TE 返回的会话身份已变化");
    const marker = `[会话自愈：${input.deliveryKey}]`;
    const records = body.data as Array<Record<string, unknown>>;
    const matches = records.flatMap((record, index) => {
      const content = typeof record.content === "string" ? record.content : Array.isArray(record.content) ? record.content.filter(p => p?.type === "text").map(p => p.text).join("\n") : "";
      return record.role === "user" && content.includes(marker + "\n") ? [{ message_id: record.id ?? record.message_id ?? null,
        offset: offset + index, text: content }] : [];
    });
    return redactEvidenceValue({ source: "teclaw_session_api", session_id: input.sessionKey, messages: records,
      delivery: matches[0] ?? null, delivery_confirmed: matches.length > 0, delivery_matches: matches,
      offset, total: body.total ?? null, next_offset: records.length === limit ? offset + records.length : null,
      coverage: "api_window", missing_message_is_failure: false }) as Record<string, unknown>;
  }

}
