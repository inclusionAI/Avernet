/** Fixed transport worker; it does not start an observation agent. */
export const ENGINE_DELIVERY_SCRIPT = String.raw`
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const { spawn } = require('node:child_process');
const { StringDecoder } = require('node:string_decoder');
const input = JSON.parse(Buffer.from(process.argv[1], 'base64').toString('utf8'));
const isChild = process.argv[2] === 'worker';
if (!/^[a-f0-9-]{36}$/.test(input.deliveryId) || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(input.deliveryKey)) throw Error('invalid delivery identity');
const marker = input.messageMarker || input.message;
if (!input.message.includes(marker)) throw Error('missing delivery marker');
const root = path.join(os.homedir(), '.cache', 'bot-runtime-message-delivery', input.deliveryId);
const receipt = path.join(root, 'receipt.json');
function save(value) { const tmp = receipt + '.' + process.pid; fs.writeFileSync(tmp, JSON.stringify(value), {mode:0o600}); fs.renameSync(tmp, receipt); }
function read() { try { return JSON.parse(fs.readFileSync(receipt, 'utf8')); } catch { return {status:'unknown'}; } }
const sleep = ms => new Promise(r=>setTimeout(r,ms));
function sessionEntry(indexPath) {
  try { return JSON.parse(fs.readFileSync(indexPath,'utf8'))[input.sessionKey]; }
  catch(e) { if(input.createSession===true && e.code==='ENOENT') return undefined; throw e; }
}
async function parent() {
  fs.mkdirSync(path.dirname(root), {recursive:true, mode:0o700});
  let created = false;
  try { fs.mkdirSync(root, {mode:0o700}); created = true; } catch(e) { if(e.code !== 'EEXIST') throw e; }
  if (created) {
    save({status:'unknown'});
    // Node 20 exposes the built-in WebSocket client behind this startup flag.
    const child = spawn(process.execPath, ['--experimental-websocket', '-e', input.program, process.argv[1], 'worker'], {detached:true, stdio:'ignore'});
    child.unref();
  }
  const deadline = Date.now()+25000;
  while (Date.now()<deadline) {
    const result=read();
    if(result.status==='failed' || (result.status==='accepted' && result.locationStatus)) break;
    await sleep(100);
  }
  process.stdout.write(JSON.stringify(read()));
}
function messageText(message) {
  if(typeof message?.content==='string') return message.content;
  return Array.isArray(message?.content) ? message.content.filter(b=>b?.type==='text').map(b=>b.text||'').join('\n') : '';
}
async function locate(indexPath, file, sessionId, before) {
  let offset=before.size, partial=''; const matches=[], decoder=new StringDecoder('utf8');
  const deadline=Date.now()+8000;
  while(Date.now()<deadline) {
    let stat;
    try {
      if(indexPath&&JSON.parse(fs.readFileSync(indexPath,'utf8'))[input.sessionKey]?.sessionId!==sessionId) return {locationStatus:'session_changed'};
      stat=fs.statSync(file);
    } catch { return {locationStatus:'session_changed'}; }
    if(stat.ino!==before.ino || stat.size<offset) return {locationStatus:'session_changed'};
    if(stat.size-before.size>4*1024*1024) return {locationStatus:'unconfirmed'};
    if(stat.size>offset) {
      const buffer=Buffer.alloc(stat.size-offset), fd=fs.openSync(file,'r');
      try { fs.readSync(fd,buffer,0,buffer.length,offset); } finally { fs.closeSync(fd); }
      offset=stat.size; partial+=decoder.write(buffer);
      const lines=partial.split('\n'); partial=lines.pop();
      for(const line of lines) {
        let record; try { record=JSON.parse(line); } catch { continue; }
        if((record.message?.role||record.type)!=='user' || !messageText(record.message).includes(marker)) continue;
        matches.push(record);
      }
      if(matches.length>1) return {locationStatus:'ambiguous'};
      if(matches.length===1) {
        const record=matches[0];
        if(typeof (record.id||record.uuid)!=='string' || !(record.id||record.uuid) || typeof record.timestamp!=='string') return {locationStatus:'unconfirmed'};
        return {locationStatus:'confirmed',messageId:record.id||record.uuid,messageTimestamp:record.timestamp,locatedAt:Date.now()};
      }
    }
    await sleep(150);
  }
  return {locationStatus:'unconfirmed'};
}
async function worker() {
  let sent=false, rejected=false, socket=null, accepted=false, runId=null, stage='session';
  try {
    const cc=input.engine==='claude_code';
    let sessionId,file,indexPath=null,before,newSession=false,sessions;
    if(cc){
      const id=/(?:^|:)session:([A-Za-z0-9_-]+)(?::|$)/.exec(input.sessionKey)?.[1]||input.sessionKey;
      if(!/^[A-Za-z0-9_-]+$/.test(id))throw Error('CC session mapping unavailable');
      const projects=fs.realpathSync(path.join(process.env.CLAUDE_CONFIG_DIR||path.join(os.homedir(),'.claude'),'projects'));
      const files=fs.readdirSync(projects,{withFileTypes:true}).filter(x=>x.isDirectory()).map(x=>path.join(projects,x.name,id+'.jsonl')).filter(x=>fs.existsSync(x));
      if(files.length!==1)throw Error('CC original session mapping unavailable');
      file=fs.realpathSync(files[0]);if(!file.startsWith(projects+'/'))throw Error('CC session outside scope');sessionId=id;before=fs.statSync(file);
    }else{
      const match=/^agent:([A-Za-z0-9_-]+):.+/.exec(input.sessionKey);if(!match)throw Error('invalid session key');
      const state=process.env.OPENCLAW_STATE_DIR||path.join(os.homedir(),'.openclaw');
      sessions=path.join(state,'agents',match[1],'sessions');indexPath=path.join(sessions,'sessions.json');
      const entry=sessionEntry(indexPath);
      if(!entry && input.createSession===true) newSession=true;
      else {
        if(!entry||typeof entry.sessionId!=='string'||!/^[A-Za-z0-9_-]+$/.test(entry.sessionId))throw Error('original session not found');
        sessionId=entry.sessionId;file=path.join(sessions,sessionId+'.jsonl');before=fs.statSync(file);
      }
    }
    stage='connection';
    const ws=socket=new WebSocket(input.engineUrl), waiters=new Map();
    ws.addEventListener('message',e=>{
      let frame;try{frame=JSON.parse(String(e.data));}catch{return;}
      if(frame.type==='res'&&waiters.has(frame.id)) {
        const {resolve,reject}=waiters.get(frame.id);waiters.delete(frame.id);
        if(frame.ok) resolve(frame.payload);
        else {if(frame.id==='send') rejected=true;reject(Error('Engine rejected request'));}
      }
      if(sent&&frame.type==='event'&&frame.event==='chat'&&frame.payload?.sessionKey===input.sessionKey
        &&(!runId||!frame.payload?.runId||frame.payload.runId===runId)&&['final','error','aborted'].includes(frame.payload?.state)) ws.close();
    });
    const rpc=(id,method,params)=>new Promise((resolve,reject)=>{
      waiters.set(id,{resolve,reject});ws.send(JSON.stringify({type:'req',id,method,params}));
      setTimeout(()=>{if(waiters.delete(id))reject(Error('Engine acknowledgement timeout'));},6000).unref();
    });
    await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});setTimeout(()=>reject(Error('Engine connect timeout')),6000).unref();});
    stage='handshake';
    await rpc('connect','connect',{minProtocol:3,maxProtocol:3,client:{id:'clawinsight-session-recovery',version:'1',platform:'linux',mode:'backend'}});
    stage='session';
    if(indexPath) {
      const current=sessionEntry(indexPath);
      if(newSession ? !!current : current?.sessionId!==sessionId) throw Error('session changed before delivery');
    }
    stage='send';
    sent=true;
    const ack = await rpc('send', 'chat.send', {
      sessionKey: input.sessionKey,
      message: input.message,
      idempotencyKey: input.deliveryId,
      resumeEnabled: true,
      ...(input.engineAuthToken ? {
        'x-iam-token':
          input.engineAuthToken,
      } : {}),
    });
    if(ack?.accepted!==true){rejected=true;throw Error('Engine did not accept message');}
    accepted=true; runId=typeof ack.runId==='string'?ack.runId:null;
    const result={status:'accepted',sessionId,acceptedAt:Date.now(),...(runId?{runId}:{})};save(result);
    let location;try{
      if(newSession) {
        const entry=sessionEntry(indexPath);
        if(!entry||typeof entry.sessionId!=='string'||!/^[A-Za-z0-9_-]+$/.test(entry.sessionId))throw Error('session journal not ready');
        sessionId=entry.sessionId;result.sessionId=sessionId;file=path.join(sessions,sessionId+'.jsonl');
        before={ino:fs.statSync(file).ino,size:0};
      }
      location=await locate(indexPath,file,sessionId,before);
    }catch{location={locationStatus:'unconfirmed'};}
    save({...result,...location});
    if(ack.recovery==='process_local_v1' && runId) {ws.close();return;}
    await new Promise(resolve=>{if(ws.readyState===3)resolve();else ws.addEventListener('close',resolve,{once:true});});
  } catch(e) {
    if(socket)socket.close();
    if(accepted)save({...read(),locationStatus:read().locationStatus||'unconfirmed'});
    else save({status:sent&&!rejected?'unknown':'failed',reasonCode:rejected?'ENGINE_REJECTED':sent?'ENGINE_ACK_UNKNOWN'
      :stage==='connection'?'ENGINE_CONNECTION_FAILED':stage==='handshake'?'ENGINE_HANDSHAKE_FAILED':'SESSION_UNAVAILABLE'});
  }
}
(isChild?worker():parent()).catch(()=>{process.stdout.write(JSON.stringify({status:'unknown'}));process.exitCode=1;});
`;
