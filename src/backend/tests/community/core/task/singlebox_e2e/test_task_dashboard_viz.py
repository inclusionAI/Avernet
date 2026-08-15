#!/usr/bin/env python3
"""任务执行图谱可视化 — 赛博霓虹风, 自带 API 代理(规避 CORS), 每 10s 轮询。

    python3 src/backend/tests/community/core/task/singlebox_e2e/test_task_dashboard_viz.py
    浏览器打开 http://localhost:8899/  (默认展示最新 task;可下拉按标题切换)
"""
import http.server, urllib.request, urllib.parse, json

BACKEND = "http://localhost:8888"
USER_ID = "146836"
PORT = 8899

HTML = r'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>任务执行图谱 · Live</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
:root{
  --bg:#06080f; --panel:rgba(18,22,38,.72); --border:rgba(120,140,200,.18);
  --text:#dfe6f5; --muted:#7a89ad;
  --pending:#6e7681; --planning:#4cc9f0; --running:#ff8a3d; --done:#3fe07a;
  --failed:#ff5c6c; --hung:#b388ff; --accent:#4cc9f0;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);overflow:hidden;
  font-family:"SF Pro Display",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
/* 动态背景 */
.bg{position:fixed;inset:0;z-index:-2;
  background:
    radial-gradient(900px 600px at 15% 10%, rgba(76,201,240,.10), transparent 60%),
    radial-gradient(900px 700px at 85% 90%, rgba(179,136,255,.10), transparent 60%),
    radial-gradient(700px 500px at 50% 50%, rgba(63,224,122,.05), transparent 60%),
    var(--bg);}
.grid{position:fixed;inset:0;z-index:-1;opacity:.35;
  background-image:linear-gradient(rgba(120,140,200,.06) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(120,140,200,.06) 1px,transparent 1px);
  background-size:42px 42px;
  mask-image:radial-gradient(120% 120% at 50% 40%,#000 40%,transparent 100%);
  animation:gridmove 24s linear infinite;}
@keyframes gridmove{to{background-position:42px 42px,42px 42px}}
/* 顶栏 */
header{display:flex;align-items:center;gap:14px;padding:12px 20px;
  background:var(--panel);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border);position:relative;z-index:20}
header h1{font-size:16px;margin:0;font-weight:700;letter-spacing:.5px;
  background:linear-gradient(90deg,#4cc9f0,#b388ff,#3fe07a);-webkit-background-clip:text;background-clip:text;color:transparent}
.pill{padding:4px 12px;border-radius:999px;font-size:12px;font-weight:700;border:1px solid currentColor}
.stat{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)}
.stat b{color:var(--text);font-variant-numeric:tabular-nums}
#taskid{width:120px;background:rgba(10,14,26,.8);color:var(--text);border:1px solid var(--border);
  border-radius:8px;padding:6px 10px;font-family:inherit;outline:none}
#taskid:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(76,201,240,.15)}
#tasksel{cursor:pointer}
#tasksel:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(76,201,240,.15)}
#tasksel option{background:#0e1322;color:var(--text)}
button{background:linear-gradient(135deg,#4cc9f0,#5a7cff);color:#06101f;border:none;border-radius:8px;
  padding:7px 16px;cursor:pointer;font-size:13px;font-weight:700;transition:transform .15s,box-shadow .15s}
button:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(76,201,240,.35)}
.live{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)}
.live .dot{width:9px;height:9px;border-radius:50%;background:var(--failed);box-shadow:0 0 10px var(--failed);animation:blink 1.4s infinite}
@keyframes blink{50%{opacity:.2}}
.err{color:var(--failed);font-size:12px;font-weight:600}
/* 圆形倒计时 */
.cdwrap{position:relative;width:34px;height:34px}
.cdwrap svg{transform:rotate(-90deg)}
.cdwrap .track{stroke:rgba(120,140,200,.2)}
.cdwrap .prog{stroke:var(--accent);stroke-linecap:round;transition:stroke-dashoffset 1s linear;filter:drop-shadow(0 0 4px var(--accent))}
.cdwrap .num{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:var(--accent)}
/* 图区 */
#wrap{position:relative;height:calc(100vh - 108px)}
svg#graph{width:100%;height:100%;display:block}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--muted);padding:8px 20px;
  background:var(--panel);backdrop-filter:blur(10px);border-bottom:1px solid var(--border);align-items:center}
.legend span{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.legend .dot{width:9px;height:9px;border-radius:50%}
.legend .sep{width:1px;height:14px;background:var(--border);margin:0 4px}
/* 节点 */
.node{cursor:pointer;transition:transform .18s}
.node:hover{transform:translate(var(--tx),var(--ty)) scale(1.05)}
.node rect.body{stroke-width:2}
.node text.t{font-size:12px;font-weight:700;fill:var(--text);pointer-events:none}
.node text.s{font-size:10px;fill:var(--muted);pointer-events:none}
.node text.st{font-size:9.5px;font-weight:700;pointer-events:none;letter-spacing:.3px}
.edge{fill:none;stroke:rgba(120,140,200,.35);stroke-width:1.8}
.edge.live{stroke:rgba(76,201,240,.5);stroke-dasharray:6 8;animation:flow 1.2s linear infinite}
@keyframes flow{to{stroke-dashoffset:-28}}
.edge-arrow{fill:rgba(120,140,200,.5)}
/* 详情面板 */
#detail{position:fixed;right:16px;top:74px;width:460px;max-height:calc(100vh - 150px);
  background:var(--panel);backdrop-filter:blur(18px);border:1px solid var(--border);border-radius:14px;
  padding:16px 18px;overflow:auto;display:none;z-index:30;
  box-shadow:0 12px 40px rgba(0,0,0,.55);animation:slidein .25s ease}
@keyframes slidein{from{opacity:0;transform:translateY(-8px)}}
#detail h3{margin:0 0 6px;font-size:15px;font-weight:700;line-height:1.3}
#detail .x{position:absolute;right:12px;top:10px;cursor:pointer;color:var(--muted);font-size:18px;line-height:1}
#detail .x:hover{color:var(--text)}
#detail .k{color:var(--muted);font-size:11px;margin:10px 0 3px;text-transform:uppercase;letter-spacing:.4px}
#detail .v{word-break:break-word;white-space:pre-wrap;line-height:1.55;font-size:13px}
#detail .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;background:rgba(6,10,20,.8);
  padding:10px;border-radius:8px;border:1px solid var(--border);max-height:280px;overflow:auto;line-height:1.5}
#detail .acc{font-weight:700}
#detail .miss{color:var(--hung)}
#detail .grp{color:var(--planning)}
.tip{position:fixed;left:20px;bottom:14px;font-size:11px;color:var(--muted);z-index:20}
</style>
</head>
<body>
<div class="bg"></div><div class="grid"></div>
<header>
  <h1>◈ 任务执行图谱</h1>
  <span id="gstatus" class="pill">—</span>
  <span class="stat">run_id <b id="runid">—</b></span>
  <span class="stat">loop <b id="loop">—</b></span>
  <span class="stat">nodes <b id="nodes">—</b></span>
  <span class="stat" title="当前任务标题">▸ <b id="curtitle">—</b></span>
  <select id="tasksel" style="width:300px;background:rgba(10,14,26,.8);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:6px 10px;font-family:inherit;font-size:12px;outline:none"></select>
  <input id="taskid" value="" placeholder="手动输入 task_id" style="width:150px;background:rgba(10,14,26,.8);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:6px 10px;font-family:inherit;outline:none"/>
  <div style="display:flex;align-items:center;gap:10px">
    <div class="cdwrap" title="下次刷新">
      <svg width="34" height="34" viewBox="0 0 34 34"><circle class="track" cx="17" cy="17" r="14" fill="none" stroke-width="3"/><circle id="cdprog" class="prog" cx="17" cy="17" r="14" fill="none" stroke-width="3"/></svg>
      <span class="num" id="cdnum">10</span>
    </div>
    <button id="refresh">⟳ Refresh</button>
  </div>
  <span class="live" id="live"><span class="dot"></span>轮询中</span>
  <span id="err" class="err"></span>
</header>
<div class="legend" id="legend"></div>
<div id="wrap"><svg id="graph"></svg></div>
<div id="detail"><span class="x" onclick="document.getElementById('detail').style.display='none'">✕</span><div id="detailBody"></div></div>
<div class="tip">点击节点查看详情 · 滚轮缩放 · 拖拽平移</div>

<script>
const C={PENDING:'#6e7681',PLANNING:'#4cc9f0',RUNNING:'#ff8a3d',DONE:'#3fe07a',FAILED:'#ff5c6c',HUNG:'#b388ff'};
const ICON={planning:'⚙',single_bot:'👤',coop_group:'👥',bbs:'📡'};
const SVG=document.getElementById('graph');
function inferParent(n,all){
  const ep=(n.context&&n.context.extend_props)||{};
  if(ep.parent_node&&all.some(x=>x.node_id===ep.parent_node))return ep.parent_node;
  let best=null;for(const x of all){if(x.node_id===n.node_id)continue;
    if(n.node_id.startsWith(x.node_id+"_")){if(!best||x.node_id.length>best.length)best=x.node_id;}}
  return best;
}
const TN=document.getElementById('taskid');
const TS=document.getElementById('tasksel');
let CURRENT_TITLE='';
function loadLists(){return fetch('/proxy-list').then(r=>r.json()).then(j=>{
  const items=(j&&j.success&&j.data)||[];
  // 默认选最新(run_id 降序第一;list 接口已按 run_id desc)
  if(!TN.value && items.length){TN.value=items[0].task_id;}
  // 重建下拉(标题展示 + 状态色点)
  const cur=TN.value;
  TS.innerHTML=items.map(x=>{
    const c=C[x.status]||'#6e7681';
    return `<option value="${x.task_id}" ${x.task_id===cur?'selected':''}>● ${x.title||x.task_id}  [${x.status}]</option>`;
  }).join('');
  // 找当前选中项的 title
  const hit=items.find(x=>x.task_id===cur);
  if(hit)CURRENT_TITLE=hit.title||hit.task_id;else CURRENT_TITLE=cur;
  return items;
});}
function load(){return fetch('/proxy?'+new URLSearchParams({task_id:TN.value})).then(r=>r.json()).then(j=>{
  if(!j||!j.success)throw new Error((j&&j.message)||'请求失败');return j.data;});}
function truncate(s,n){s=String(s);return s.length>n?s.slice(0,n)+'…':s;}
const R=14,CIRC=2*Math.PI*R;const cdprog=document.getElementById('cdprog');cdprog.setAttribute('stroke-dasharray',CIRC);
function setCD(sec){cdprog.setAttribute('stroke-dashoffset',CIRC*(1-sec/10));document.getElementById('cdnum').textContent=sec;}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function render(d){
  document.getElementById('gstatus').textContent=d.status;
  document.getElementById('gstatus').style.color=C[d.status]||'#fff';
  document.getElementById('gstatus').style.borderColor=C[d.status]||'#fff';
  document.getElementById('gstatus').style.textShadow=`0 0 12px ${C[d.status]||'#fff'}`;
  document.getElementById('runid').textContent=d.run_id;
  document.getElementById('loop').textContent=d.loop_round;
  document.getElementById('nodes').textContent=d.tasks.length;
  const tasks=d.tasks, rootId=tasks[0].task_id;
  const rootN=tasks.find(x=>x.node_id===rootId)||tasks[0];
  const dispTitle=(rootN&&rootN.task_spec&&rootN.task_spec.metadata&&rootN.task_spec.metadata.title)||CURRENT_TITLE||TN.value;
  document.getElementById('curtitle').textContent=truncate(dispTitle,24);
  CURRENT_TITLE=dispTitle;
  const edges=[],parentOf={};
  tasks.forEach(t=>{let p=inferParent(t,tasks);if(!p&&t.node_id!==rootId)p=rootId;parentOf[t.node_id]=p;if(p)edges.push({s:p,d:t.node_id});});
  const level={};tasks.forEach(t=>level[t.node_id]=0);
  const adj={};edges.forEach(e=>{(adj[e.s]=adj[e.s]||[]).push(e.d);});
  const q=[rootId];
  while(q.length){const u=q.shift();for(const v of (adj[u]||[])){if(level[v]<=level[u]){level[v]=level[u]+1;q.push(v);}}}
  const maxL=Math.max(...Object.values(level),0);
  const byL={};for(const id in level)(byL[level[id]]=byL[level[id]]||[]).push(id);
  Object.values(byL).forEach(a=>a.sort());
  const W=SVG.clientWidth||window.innerWidth, H=(SVG.clientHeight||window.innerHeight);
  const topPad=70, botPad=50, colH=Math.max(120,(H-topPad-botPad)/(maxL+1));
  const center=(n,i)=>{if(n===1)return W/2;const step=(W-80)/(n-1);return 40+i*step;};
  const pos={};for(let l=0;l<=maxL;l++){const arr=byL[l]||[];arr.forEach((id,i)=>pos[id]={x:center(arr.length,i),y:topPad+l*colH});}
  const svg=d3.select(SVG);svg.selectAll('*').remove();
  const g=svg.append('g');
  svg.call(d3.zoom().scaleExtent([.3,3.5]).on('zoom',e=>g.attr('transform',e.transform)));
  const defs=svg.append('defs');
  // 状态辉光滤镜
  Object.entries(C).forEach(([s,col])=>{
    const f=defs.append('filter').attr('id','glow-'+s).attr('x','-60%').attr('y','-60%').attr('width','220%').attr('height','220%');
    f.append('feGaussianBlur').attr('stdDeviation','4').attr('result','b');
    const m=f.append('feMerge');m.append('feMergeNode').attr('in','b');m.append('feMergeNode').attr('in','SourceGraphic');});
  // 箭头
  defs.append('marker').attr('id','arrow').attr('viewBox','0 -5 10 10').attr('refX',6).attr('refY',0)
    .attr('markerWidth',7).attr('markerHeight',7).attr('orient','auto').append('path').attr('class','edge-arrow').attr('d','M0,-5L10,0L0,5');
  // 边
  edges.forEach(e=>{const a=pos[e.s],b=pos[e.d];if(!a||!b)return;
    const dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy)||1,nx=dx/len,ny=dy/len;
    const off=58,tx=b.x-nx*off,ty=b.y-ny*off-26;
    g.append('path').attr('class','edge '+(d.status==='RUNNING'?'live':''))
      .attr('d',`M${a.x},${a.y+26} C${a.x},${(a.y+b.y)/2} ${b.x},${(a.y+b.y)/2} ${tx},${ty}`)
      .attr('marker-end','url(#arrow)');
  });
  // 节点
  tasks.forEach(t=>{const p=pos[t.node_id];if(!p)return;
    const col=C[t.status]||'#6e7681';const m=t.task_spec.metadata;const ri=t.run_info||{};
    const isRoot=t.node_id===rootId;
    const ng=g.append('g').attr('class','node').style('--tx',p.x+'px').style('--ty',p.y+'px')
      .attr('transform',`translate(${p.x},${p.y})`).on('click',()=>detail(t));
    const w=isRoot?220:184,h=54;
    // 辉光底
    ng.append('rect').attr('x',-w/2).attr('y',-h/2).attr('width',w).attr('height',h).attr('rx',12)
      .attr('fill','none').attr('stroke',col).attr('stroke-width',2).attr('opacity',.5).attr('filter','url(#glow-'+t.status+')');
    // 主体
    ng.append('rect').attr('class','body').attr('x',-w/2).attr('y',-h/2).attr('width',w).attr('height',h).attr('rx',12)
      .attr('fill',`rgba(18,22,38,.92)`).attr('stroke',col).attr('stroke-width',isRoot?2.5:1.8);
    // 状态色顶条
    ng.append('rect').attr('x',-w/2-1).attr('y',-h/2).attr('width',4).attr('height',h).attr('rx',2).attr('fill',col);
    // RUNNING 脉冲环
    if(t.status==='RUNNING'){ng.append('rect').attr('x',-w/2-4).attr('y',-h/2-4).attr('width',w+8).attr('height',h+8).attr('rx',14)
      .attr('fill','none').attr('stroke',col).attr('stroke-width',1.5).attr('opacity',.6)
      .append('animate').attr('attributeName','stroke-width').attr('values','1;5;1').attr('dur','1.6s').attr('repeatCount','indefinite');}
    const icon=ICON[ri.run_mode]||'▢';
    ng.append('text').attr('class','t').attr('text-anchor','middle').attr('y',-8).text(icon+' '+truncate(m.title||t.node_id,18));
    ng.append('text').attr('class','s').attr('text-anchor','middle').attr('y',6).text(t.node_id);
    const st=ng.append('text').attr('class','st').attr('text-anchor','middle').attr('y',20).attr('fill',col);
    st.text(t.status);if(t.status==='RUNNING')st.append('animate').attr('attributeName','opacity').attr('values','1;.35;1').attr('dur','1.4s').attr('repeatCount','indefinite');
  });
}
function detail(t){const m=t.task_spec,ri=t.run_info||{};const ac=ri.acceptance_result;
  const ep=(t.context&&t.context.extend_props)||{};const miss=ep.miss_events||[];const grp=ep.pending_group_formation;
  let h=`<h3 style="color:${C[t.status]}">${esc(m.metadata.title)}</h3>`;
  h+=`<div class="k">task / node</div><div class="v mono">${esc(t.task_id)} :: ${t.node_id}</div>`;
  h+=`<div class="k">状态 / 运行模式 / 执行者</div><div class="v"><b style="color:${C[t.status]}">${t.status}</b> · ${ICON[ri.run_mode]||''} ${ri.run_mode||'—'} · ${ri.assignee||'—'}</div>`;
  h+=`<div class="k">目标 Objective</div><div class="v">${esc(m.goal.objective)}</div>`;
  h+=`<div class="k">指令 Instruction</div><div class="v">${esc(m.metadata.instruction)}</div>`;
  const acs=m.goal.acceptances||[];if(acs.length){h+=`<div class="k">验收标准</div><div class="v">${acs.map(a=>'• '+esc(a.description)).join('<br>')}</div>`;}
  if(ac){h+=`<div class="k">验收结果</div><div class="v acc" style="color:${ac.verdict==='PASS'?'#3fe07a':'#ff5c6c'}">${ac.verdict}${ac.gaps&&ac.gaps.length?'<br>gaps: '+esc(ac.gaps.join('; ')):''}</div>`;}
  if(miss.length){h+=`<div class="k">MISS 事件</div><div class="v miss">${miss.map(x=>'• '+esc(x)).join('<br>')}</div>`;}
  if(grp){h+=`<div class="k">协作群(待建)</div><div class="v grp">${esc(grp.group_name||'')} · 模式:${esc(grp.collab_mode||'')}<br>成员: ${(grp.members_info||[]).map(x=>esc(x.bot_id)).join(', ')}</div>`;}
  if(ri.output&&ri.output.data){h+=`<div class="k">执行产出</div><div class="mono">${esc(ri.output.data)}</div>`;}
  document.getElementById('detailBody').innerHTML=h;document.getElementById('detail').style.display='block';}
// legend
document.getElementById('legend').innerHTML=
  Object.entries(C).map(([s,c])=>`<span><span class="dot" style="background:${c};box-shadow:0 0 8px ${c}"></span>${s}</span>`).join('<span class="sep"></span>')+
  Object.entries(ICON).map(([m,i])=>`<span>${i} ${m}</span>`).join('<span class="sep"></span>')+
  '<span class="sep"></span><span>▬ 依赖边 (<span style="color:#4cc9f0">流动=RUNNING</span>)</span>';
// poll
let sec=10;
function reload(){document.getElementById('err').textContent='';loadLists().then(()=>load()).then(render).catch(e=>document.getElementById('err').textContent=e.message);}
function tick(){sec--;setCD(Math.max(sec,0));if(sec<=0){sec=10;setCD(10);reload();}}
document.getElementById('refresh').onclick=()=>{sec=10;setCD(10);reload();};
TN.addEventListener('change',()=>{sec=10;setCD(10);reload();});
TS.addEventListener('change',()=>{TN.value=TS.value;sec=10;setCD(10);reload();});
const _p=new URLSearchParams(location.search);const _tid=_p.get('task_id');if(_tid){TN.value=_tid;}
setInterval(tick,1000);reload();window.addEventListener('resize',()=>reload());
</script>
</body>
</html>
'''

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):
        if self.path.startswith('/proxy-list'):
            qs=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            st=qs.get('status')
            try:
                url=f"{BACKEND}/api/task/list"
                if st:url+=urllib.parse.urlencode({'status':st[0]})
                req=urllib.request.Request(url, headers={'x-user-id':USER_ID})
                with urllib.request.urlopen(req,timeout=15) as r: body=r.read()
                self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.end_headers();self.wfile.write(body)
            except Exception as ex:
                self.send_response(502);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(json.dumps({'success':False,'message':str(ex)}).encode())
            return
        if self.path.startswith('/proxy'):
            qs=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            tid=(qs.get('task_id') or ['t_case'])[0]
            try:
                req=urllib.request.Request(f"{BACKEND}/api/task/dashboard?task_id={urllib.parse.quote(tid)}",
                                           headers={'x-user-id':USER_ID})
                with urllib.request.urlopen(req,timeout=15) as r: body=r.read()
                self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.end_headers();self.wfile.write(body)
            except Exception as ex:
                self.send_response(502);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(json.dumps({'success':False,'message':str(ex)}).encode())
        else:
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(HTML.encode())

if __name__=='__main__':
    print(f"◈ 可视化: http://localhost:{PORT}/  (默认最新 task;下拉按标题切换)")
    print(f"◈ 代理 → {BACKEND}  (user {USER_ID})  GET /api/task/list + /api/task/dashboard")
    http.server.HTTPServer(('127.0.0.1',PORT),H).serve_forever()
