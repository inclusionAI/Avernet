const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
// Run only against an explicitly supplied, disposable local BCS instance.
// Requires `npm install` in openclaw-channel-bcn. This is a transport/lifecycle
// probe, NOT evidence of a real model retaining conversation history.
const { createRequire } = require('node:module');
const { resolve } = require('node:path');
const pluginRequire = createRequire(resolve(__dirname, '../crates/plugins/openclaw-channel-bcn/package.json'));
const WebSocket = pluginRequire('ws');
const base = process.argv[2];
if (!base || !/^http:\/\/(127\.0\.0\.1|localhost):[0-9]+$/.test(base)) {
  throw Error('Usage: node task_handle_runtime_probe.cjs http://127.0.0.1:<DISPOSABLE-BCS-PORT>');
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function api(path, body, token, method=body?'POST':'GET') {
  const r=await fetch(base+path,{method,headers:{'content-type':'application/json',...(token?{'X-BCS-Bot-Token':token}:{})},body:body?JSON.stringify(body):undefined});
  const text=await r.text(); if(!r.ok) throw Error(`${method} ${path}: ${r.status} ${text}`);
  return text?JSON.parse(text):null;
}
async function until(fn, label, ms=12000) { const end=Date.now()+ms; do { const x=await fn(); if(x) return x; await sleep(30); } while(Date.now()<end); throw Error(`timeout: ${label}`); }
class Bot {
  constructor(name) {this.name=name; this.pending=new Map(); this.sends=[]; this.runs=new Map(); this.aborts=0;}
  send(value){this.ws.send(JSON.stringify(value));}
  request(method,params){return new Promise((resolve,reject)=>{ const id=randomUUID(); const timer=setTimeout(()=>{this.pending.delete(id);reject(Error(`timeout ${method}`));},20000);this.pending.set(id,{resolve,reject,timer});this.send({type:'req',id,method,params});});}
  final(req,text,state='final'){const p=req.params;this.runs.delete(req.id);this.send({type:'event',event:'chat.event',payload:{run_id:req.id,bcs_group_id:p.bcs_group_id,state,...(state==='error'?{error_message:text,error_kind:'probe_failure'}:{message:{role:'assistant',content:[{type:'text',text}],timestamp:Date.now()},stop_reason:'complete'})},seq:0});}
  async connect(){
    this.ws=new WebSocket(base.replace('http:','ws:')+'/ws/bot');
    this.ws.on('message',data=>{const f=JSON.parse(data);if(f.type==='res'){const p=this.pending.get(f.id);if(p){clearTimeout(p.timer);this.pending.delete(f.id);f.ok?p.resolve(f.payload):p.reject(Error(JSON.stringify(f.error)));}return;}
      if(f.type!=='req')return;
      if(f.method==='chat.send'){this.sends.push(f);this.runs.set(f.id,f);this.send({type:'res',id:f.id,ok:true,payload:{run_id:f.id}});if(this.name==='Manager')setTimeout(()=>this.final(f,'ack'),20);}
      else if(f.method==='chat.abort'){
        this.aborts++;if(this.ignoreAbort)return;
        setTimeout(()=>{
          if(this.rejectAbort){this.send({type:'res',id:f.id,ok:false,error:{code:'unknown_method',message:'chat.abort is unsupported'}});return;}
          const ids=f.params.run_id?[f.params.run_id]:[...this.runs.keys()];
          for(const id of ids)this.runs.delete(id);
          this.send({type:'res',id:f.id,ok:true,payload:{aborted:ids.length>0,aborted_run_ids:ids}});
        },this.abortDelayMs||0);
      }
      else this.send({type:'res',id:f.id,ok:true,payload:f.method==='chat.history'?{messages:[]}:{}});
    });
    await new Promise((resolve,reject)=>{this.ws.once('open',resolve);this.ws.once('error',reject);});
    const c=await this.request('bot.connect',{protocol_version:2});this.id=c.bot_uuid;this.token=c.token;
    await api('/bots/onboard',{name:'TaskHandleProbe-'+this.name,summary:'isolated task API probe',domains:[],skills:[],scopes:[]},this.token);
    await this.request('bot.status',{status:'idle'});
    await api(`/bots/${this.id}/visibility`,{visibility:'public'},this.token,'PUT');
  }
  text(req){return req.params.message.content.filter(x=>x.type==='text').map(x=>x.text).join('');}
}
(async()=>{
  const manager=new Bot('Manager'),worker=new Bot('Worker');let original;
  try {
    await manager.connect(); await worker.connect();
    console.log('connected isolated probe peers');
    original=await api('/admin/message-delivery/policy');
    const policy=structuredClone(original.policy);policy.flow_enabled.task=true;policy.flow_enabled.group=false;policy.flow_enabled.system=false;policy.defaults.mode='enforce';policy.defaults.max_running=1;policy.defaults.min_send_interval_ms=0;policy.defaults.max_queued=20;policy.task_timeout_min_s=1;policy.task_timeout_max_s=10500;policy.task_cancel_wait_ms=5000;policy.pause_dispatch=false;
    await api('/admin/message-delivery/policy',{expected_version:original.version,policy},null,'PUT');
    const g=await api('/groups',{driver_bot:manager.id,group_strategy:'manager_worker',label:'task-handle-live-probe',participants:[{bot_uuid:manager.id,role:'manager'},{bot_uuid:worker.id,role:'worker'}],create_initial_session:false},manager.token);
    const gid=g.group_id||g.id; assert(gid,JSON.stringify(g));
    const s=await api(`/groups/${gid}/sessions`,{group_context_delivery:'inject'},manager.token); let sid=s.session_id||s.id;assert(sid,JSON.stringify(s));
    const newSession=async()=>{const next=await api(`/groups/${gid}/sessions`,{group_context_delivery:'inject'},manager.token);sid=next.session_id||next.id;assert(sid,JSON.stringify(next));};
    console.log('created isolated group and Session');
    const dispatch=(message,timeout_s)=>manager.request('task.dispatch',{group_id:gid,session_id:sid,target_bot:worker.id,message,response_mode:'full',...(timeout_s?{timeout_s}:{})});
    const ref=x=>'T-'+x.task_id.replaceAll('-','').slice(0,8);
    const status=async r=>(await api('/tasks/status',{task_refs:[r]})).tasks[0].status;
    const cancel=r=>api('/tasks/cancel',{task_ref:r,reason:'isolated test stop'});
    assert.equal(await status('T-00000000'),'not_found');
    const a=await dispatch('first probe'),b=await dispatch('second probe'); const ar=ref(a),br=ref(b);
    await until(()=>worker.sends.length===1,'first dispatched');assert.match(worker.text(worker.sends[0]),new RegExp('^\\[来自 .*ref='+ar+'\\]'));
    await until(async()=>await status(ar)==='running','first acceptance');assert.equal(await status(br),'queued');
    assert.equal((await cancel(br)).status,'cancelled'); assert.equal((await cancel(br)).status,'cancelled');
    worker.final(worker.sends[0],'Need more details to continue');
    await until(async()=>await status(ar)==='completed','first completion');
    await until(()=>manager.sends.some(x=>manager.text(x).includes(ar)&&manager.text(x).includes('Need more details')),'Manager result');
    assert.equal((await cancel(ar)).status,'already_finished');
    const c=await dispatch('follow-up details'),cr=ref(c);await until(()=>worker.sends.length===2,'followup delivered');worker.final(worker.sends[1],'finished followup');
    await until(async()=>await status(cr)==='completed','followup completion');
    const d=await dispatch('test error'),dr=ref(d);await until(()=>worker.sends.length===3,'error probe delivered');worker.final(worker.sends[2],'intentional probe failure','error');
    await until(async()=>await status(dr)==='failed','error status');
    const e=await dispatch('cancel live run'),er=ref(e);await until(async()=>await status(er)==='running','cancel probe running');assert.equal((await cancel(er)).status,'cancelled');assert.equal(worker.aborts,1);
    const f=await dispatch('timeout live run',1),fr=ref(f);await until(async()=>await status(fr)==='timed_out','custom timeout',8000);assert.equal(worker.aborts,2);
    assert.equal((await manager.request('task.status',{task_refs:[fr]})).tasks[0].status,'timed_out');
    await require('./task_handle_scenarios.cjs')({api, until, manager, worker, dispatch, ref, status, cancel, policy, newSession});
    console.log('PASS: native WS assign; HTTP/WS status; FIFO; completed/failed; queued and running cancel; duplicate cancel; custom timeout; Manager result refs');
    console.log('TASK_REFS='+JSON.stringify([ar,br,cr,dr,er,fr]));
    console.log('NOT PROVEN: real model/plugin history continuation (probe peers are scripted)');
  } finally {
    manager.ws?.close();worker.ws?.close();
    if(original){const current=await api('/admin/message-delivery/policy');await api('/admin/message-delivery/policy',{expected_version:current.version,policy:original.policy},null,'PUT');}
  }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
