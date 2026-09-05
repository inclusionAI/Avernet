import assert from 'node:assert/strict';import{createRequire}from'node:module';import{JSDOM}from'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://local.test/'});Object.assign(globalThis,{window:dom.window,document:dom.window.document,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,getComputedStyle:dom.window.getComputedStyle,IS_REACT_ACT_ENVIRONMENT:true});window.confirm=()=>true;HTMLElement.prototype.attachEvent=function(){};HTMLElement.prototype.detachEvent=function(){};
let resizeCallback;globalThis.ResizeObserver=class{constructor(cb){resizeCallback=cb}observe(){}disconnect(){}};window.ResizeObserver=globalThis.ResizeObserver;
const ReactModule=await import('react');const React=ReactModule.default;const {act}=ReactModule;const {createRoot}=await import('react-dom/client');
const require=createRequire(import.meta.url);const{UndercoverGamePanel}=require('../dist/index.umd.js');
const base={runId:'run-component',groupId:'group',sessionId:'session',gameSessionId:'session',phase:'speaking',round:1,attempt:1,host:{actorId:'host',displayName:'主持人'},seatOrder:['a','b','c'],turnOrder:['a','b'],players:[{actorId:'a',displayName:'小甲',seatNumber:1,isHuman:true},{actorId:'b',displayName:'小乙',seatNumber:2},{actorId:'c',displayName:'小丙',seatNumber:3,alive:false,eliminated:true}],nodeActorMap:{'node-a':'a','node-b':'b','node-host':'host'},currentViewerActorId:'a',rules:{speechMaxChars:5,forbidOwnWord:true,bluntness:4},publicHistory:[{round:1,speeches:[{actorId:'c',seatNumber:3,displayName:'小丙',text:'历史发言'}]}],pollingInterval:5,autoRefresh:false,display:{showVoteResults:false,showHostOutput:true}};
function response(body,status=200){return new Response(JSON.stringify({code:20000,data:body}),{status,headers:{'content-type':'application/json'}})}
function mount(props){const container=document.createElement('div');document.body.appendChild(container);Object.defineProperty(container,'getBoundingClientRect',{value:()=>({width:800,height:700,top:0,left:0,right:800,bottom:700,x:0,y:0,toJSON(){}})});const root=createRoot(container);root.render(React.createElement(UndercoverGamePanel,props));return{container,render:p=>root.render(React.createElement(UndercoverGamePanel,p)),unmount:()=>{root.unmount();container.remove()}}}
function setInputValue(element,value){const setter=Object.getOwnPropertyDescriptor(element.constructor.prototype,'value').set;setter.call(element,value);element.dispatchEvent(new window.Event('input',{bubbles:true}))}
const settle=async(ms=0)=>{await Promise.resolve();await Promise.resolve();if(ms)await new Promise(r=>setTimeout(r,ms));await Promise.resolve()};const text=p=>p.container.textContent??'';const button=(p,label)=>[...p.container.querySelectorAll('button')].find(b=>b.textContent?.includes(label));
function fetchFor(snapshots,posts){let index=0;return async(url,init={})=>{const path=new URL(url,'http://local.test').pathname;if(init.method==='POST'&&path.endsWith('/respond')){posts.push(JSON.parse(init.body));return response({accepted:true})}const snapshot=snapshots[Math.min(index,snapshots.length-1)];if(path.endsWith('/graph'))return response(snapshot.graph);if(path.endsWith('/pending-human-nodes'))return response(snapshot.pending);if(path.endsWith('/messages')){index+=1;return response(snapshot.messages??[])}const nodeId=path.split('/').at(-1);return response({node:snapshot.graph.nodes.find(n=>n.node_id===nodeId)??{node_id:nodeId,status:'ready'}})}}
const originalFetch=globalThis.fetch;
try{
 const speechInstruction=`请发言\n[UNDERCOVER_UI_CONTEXT_V1]\n{"action":"speech","round":1,"seatNumber":1,"word":"苹果","maxChars":5,"forbidOwnWord":true,"bluntness":4}\n[/UNDERCOVER_UI_CONTEXT_V1]`;const posts=[];globalThis.fetch=fetchFor([{graph:{run:{run_id:'run-component',status:'running'},nodes:[{node_id:'node-a',status:'ready',attempt:1},{node_id:'node-b',status:'completed'}]},pending:[{node_id:'node-a',attempt:1,instruction:speechInstruction,timeout_deadline_ms:Date.now()+60000,upstream_artifacts:[{node_id:'node-b',text:'上游发言'}]}],messages:[{content:'主持开场',metadata:{state_machine:{event:'output',run_id:'run-component',node_id:'node-host'}}}]},{graph:{run:{run_id:'run-component',status:'running'},nodes:[{node_id:'node-a',status:'completed'},{node_id:'node-b',status:'completed'}]},pending:[],messages:[]}],posts);
 let p;await act(async()=>{p=mount(base);await settle(15)});const wallLayer=p.container.querySelector('[data-room-layer="wall"]');const floorLayer=p.container.querySelector('[data-room-layer="floor"]');const tableLayer=p.container.querySelector('[data-room-layer="table"]');assert.ok(wallLayer&&floorLayer&&tableLayer);assert.notEqual(getComputedStyle(wallLayer).backgroundImage,getComputedStyle(floorLayer).backgroundImage);assert.equal(getComputedStyle(wallLayer).backgroundRepeat,'repeat');assert.equal(getComputedStyle(tableLayer).backgroundSize,'100% 100%');assert.match(text(p),/轮到你发言/);assert.match(text(p),/上游发言/);assert.match(text(p),/历史发言/);assert.equal(document.activeElement?.getAttribute('aria-label'),'你的公开发言');assert.match(text(p),/••••/);await act(async()=>{button(p,'显示').click();await settle()});assert.match(text(p),/苹果/);
 const draft=p.container.querySelector('textarea');await act(async()=>{setInputValue(draft,'草稿');resizeCallback([{contentRect:{width:560}}]);await settle();resizeCallback([{contentRect:{width:400}}]);await settle()});assert.equal(p.container.querySelector('textarea').value,'草稿');assert.equal(p.container.firstElementChild?.getAttribute('data-layout'),'narrow');
 const textarea=p.container.querySelector('textarea');await act(async()=>{setInputValue(textarea,'苹果很好');await settle();p.container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await settle()});assert.match(text(p),/不能包含你自己的词/);assert.equal(posts.length,0);
 await act(async()=>{const ta=p.container.querySelector('textarea');setInputValue(ta,'😀中');await settle();p.container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await settle(5)});assert.deepEqual(posts,[{content:'😀中'}]);assert.match(text(p),/提交成功|等待主持人/);
 await act(async()=>{resizeCallback([{contentRect:{width:400}}]);await settle()});assert.equal(p.container.firstElementChild?.getAttribute('data-layout'),'narrow');assert.match(text(p),/3号 · 小丙/);
 const seat=button(p,'3号 · 小丙');await act(async()=>{seat.click();await settle()});assert.match(text(p),/公开历史/);assert.doesNotMatch(text(p),/节点node-a/);assert.match(text(p),/技术详情/);const close=button(p,'关闭');await act(async()=>{close.click();await settle()});assert.equal(document.activeElement,seat);await act(async()=>p.unmount());

 const votePosts=[];const voteParams={...base,phase:'voting',voteCandidates:[{actorId:'b',displayName:'小乙',seatNumber:2,eligible:true},{actorId:'c',displayName:'小丙',seatNumber:3,eligible:false,eliminated:true}]};const voteInstruction=`投票\n[UNDERCOVER_UI_CONTEXT_V1]\n{"action":"vote","round":1,"seatNumber":1,"word":"苹果","allowAbstain":true}\n[/UNDERCOVER_UI_CONTEXT_V1]`;globalThis.fetch=fetchFor([{graph:{run:{run_id:'run-component',status:'running'},nodes:[{node_id:'node-a',status:'ready'},{node_id:'node-b',status:'completed'}]},pending:[{node_id:'node-a',instruction:voteInstruction}],messages:[{content:'{"kind":"vote","target_actor_id":"b"}',metadata:{state_machine:{event:'output',run_id:'run-component',node_id:'node-b'}}}]}],votePosts);let v;await act(async()=>{v=mount(voteParams);await settle(5)});assert.match(text(v),/2号 · 小乙/);assert.doesNotMatch(text(v),/3号 · 小丙.*选择投票对象/);assert.doesNotMatch(text(v),/target_actor_id/);
 const radio=v.container.querySelector('input[value="b"]');const confirm=v.container.querySelector('input[type="checkbox"]');await act(async()=>{radio.click();confirm.click();await settle();v.container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await settle(5)});assert.deepEqual(votePosts,[{content:'{"kind":"vote","target_actor_id":"b"}'}]);await act(async()=>v.unmount());

 const abstainPosts=[];globalThis.fetch=fetchFor([{graph:{run:{run_id:'run-component',status:'running'},nodes:[{node_id:'node-a',status:'ready'}]},pending:[{node_id:'node-a',instruction:voteInstruction}],messages:[]}],abstainPosts);let a;await act(async()=>{a=mount(voteParams);await settle(5)});await act(async()=>{a.container.querySelector('input[value="__abstain__"]').click();a.container.querySelector('input[type="checkbox"]').click();await settle();a.container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await settle(5)});assert.deepEqual(abstainPosts,[{content:'{"kind":"vote","abstain":true}'}]);await act(async()=>a.unmount());

 const actions=[];globalThis.fetch=fetchFor([{graph:{run:{run_id:'run-component',status:'completed'},nodes:[]},pending:[],messages:[]}],[]);let r;await act(async()=>{r=mount({...base,onAction:async x=>{actions.push(x)}});await settle(5)});assert.match(text(r),/主持人正在打开投票/);await act(async()=>{button(r,'告诉主持人卡住了').click();button(r,'告诉主持人卡住了').click();await settle(5)});assert.deepEqual(actions,[{type:'send_message',content:'卡住了'}]);assert.match(text(r),/主持人已收到/);await act(async()=>r.unmount());


 // Manager-worker one-shot: no session output messages, completed mapped nodes resolve through node detail.
 const detailCalls=new Map();let oneShotPolls=0;
 globalThis.fetch=async(url,init={})=>{const path=new URL(url,'http://local.test').pathname;if(path.endsWith('/graph')){oneShotPolls+=1;return response({run:{run_id:'run-detail',status:'running',updated_at:1},nodes:[{node_id:'node-a',status:'completed',attempt:1},{node_id:'node-b',status:'completed',attempt:1},{node_id:'node-host',status:'completed',attempt:1}]})}if(path.endsWith('/pending-human-nodes'))return response([]);if(path.endsWith('/messages'))return response([]);const nodeId=path.split('/').at(-1);detailCalls.set(nodeId,(detailCalls.get(nodeId)??0)+1);const artifact=nodeId==='node-a'?'人类公开发言':nodeId==='node-b'?'Bot 公开发言':'主持人公告';return response({node:{node_id:nodeId,run_id:'run-detail',status:'completed',attempt:1,artifact_text:artifact}})};
 const detailParams={...base,runId:'run-detail',autoRefresh:true,pollingInterval:5,style:{height:'280px'}};let d;await act(async()=>{d=mount(detailParams);await settle(8)});await act(async()=>{await settle(35)});assert.match(text(d),/人类公开发言/);assert.match(text(d),/Bot 公开发言/);assert.match(text(d),/主持人公告/);assert.ok(oneShotPolls>=2,`unchanged non-terminal snapshots must keep polling (got ${oneShotPolls})`);assert.equal(detailCalls.get('node-a'),1);assert.equal(detailCalls.get('node-b'),1);assert.equal(detailCalls.get('node-host'),1);
 const dockBody=d.container.querySelector('[data-region="dock-body"]');const dockFooter=d.container.querySelector('[data-region="dock-footer"]');assert.ok(dockBody);assert.ok(dockFooter);assert.equal(getComputedStyle(dockBody).overflowY,'auto');assert.equal(d.container.firstElementChild.style.height,'280px');assert.match(dockFooter.textContent,/告诉主持人卡住了/);
 const bubble=[...d.container.querySelectorAll('button')].find(x=>x.textContent?.includes('人类公开发言'));await act(async()=>{bubble.click();await settle()});const overlay=d.container.querySelector('[data-region="panel-overlay"]');assert.ok(overlay);assert.equal(getComputedStyle(overlay).position,'absolute');assert.ok(Number(getComputedStyle(overlay).zIndex)>Number(getComputedStyle(bubble).zIndex||0),'detail overlay must stack above scene actors and speech bubbles');assert.match(text(d),/人类公开发言/);await act(async()=>{window.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await settle()});assert.equal(d.container.querySelector('[data-region="panel-overlay"]'),null);assert.equal(document.activeElement,bubble);await act(async()=>d.unmount());

 // Later attempts replace the earlier phase-local bubble and each identity is fetched once.
 const attemptCalls=[];let attemptSnapshot=0;let currentGraphAttempt=1;
 globalThis.fetch=async(url)=>{const path=new URL(url,'http://local.test').pathname;if(path.endsWith('/graph')){currentGraphAttempt=attemptSnapshot===0?1:2;return response({run:{run_id:'run-attempt',status:'running',updated_at:1},nodes:[{node_id:'node-a',status:'completed',attempt:currentGraphAttempt}]})}if(path.endsWith('/pending-human-nodes'))return response([]);if(path.endsWith('/messages')){attemptSnapshot+=1;return response([])};const attempt=currentGraphAttempt;attemptCalls.push(`${path.split('/').at(-1)}:${attempt}`);return response({node:{node_id:'node-a',run_id:'run-attempt',status:'completed',attempt,artifact_text:attempt===1?'第一次尝试':'第二次尝试'}})};
 let retryPanel;await act(async()=>{retryPanel=mount({...base,runId:'run-attempt',autoRefresh:true,pollingInterval:5});await settle(8)});await act(async()=>{await settle(25)});assert.match(text(retryPanel),/第二次尝试/);assert.doesNotMatch(text(retryPanel),/第一次尝试/);assert.equal(attemptCalls.filter(x=>x==='node-a:1').length,1);assert.equal(attemptCalls.filter(x=>x==='node-a:2').length,1);await act(async()=>retryPanel.unmount());

 // A failed node-detail read is retried by the next poll without overlapping fan-out.
 let recoverableDetailCalls=0;
 globalThis.fetch=async(url)=>{const path=new URL(url,'http://local.test').pathname;if(path.endsWith('/graph'))return response({run:{run_id:'run-recoverable',status:'running'},nodes:[{node_id:'node-a',status:'completed',attempt:1}]});if(path.endsWith('/pending-human-nodes')||path.endsWith('/messages'))return response([]);recoverableDetailCalls+=1;if(recoverableDetailCalls===1)return response({message:'temporary'},500);return response({node:{node_id:'node-a',run_id:'run-recoverable',status:'completed',attempt:1,artifact_text:'重试后可见'}})};
 let recoverable;await act(async()=>{recoverable=mount({...base,runId:'run-recoverable',autoRefresh:true,pollingInterval:5});await settle(8)});await act(async()=>{await settle(25)});assert.equal(recoverableDetailCalls,2);assert.match(text(recoverable),/重试后可见/);await act(async()=>recoverable.unmount());

 // Rebinding the stable panel to a new run clears old bubbles and output cache.
 globalThis.fetch=async(url)=>{const path=new URL(url,'http://local.test').pathname;const runId=path.includes('run-new')?'run-new':'run-old';if(path.endsWith('/graph'))return response({run:{run_id:runId,status:'running'},nodes:[{node_id:'node-a',status:'completed',attempt:1}]});if(path.endsWith('/pending-human-nodes')||path.endsWith('/messages'))return response([]);return response({node:{node_id:'node-a',run_id:runId,status:'completed',attempt:1,artifact_text:runId==='run-old'?'旧运行气泡':'新运行气泡'}})};
 let rebound;await act(async()=>{rebound=mount({...base,runId:'run-old',autoRefresh:false});await settle(8)});assert.match(text(rebound),/旧运行气泡/);await act(async()=>{rebound.render({...base,runId:'run-new',autoRefresh:false});await settle(8)});assert.match(text(rebound),/新运行气泡/);assert.doesNotMatch(text(rebound),/旧运行气泡/);await act(async()=>rebound.unmount());

 // Vote-node artifacts are reduced to target-free completion markers.
 globalThis.fetch=async(url)=>{const path=new URL(url,'http://local.test').pathname;if(path.endsWith('/graph'))return response({run:{run_id:'run-vote-detail',status:'running'},nodes:[{node_id:'node-b',status:'completed',attempt:1}]});if(path.endsWith('/pending-human-nodes')||path.endsWith('/messages'))return response([]);return response({node:{node_id:'node-b',run_id:'run-vote-detail',status:'completed',attempt:1,artifact_text:'{"kind":"vote","target_actor_id":"a"}'}})};
 let privateVote;await act(async()=>{privateVote=mount({...voteParams,runId:'run-vote-detail',autoRefresh:false});await settle(5)});assert.match(text(privateVote),/已投票/);assert.doesNotMatch(text(privateVote),/target_actor_id|run-vote-detail.*node-b/);await act(async()=>privateVote.unmount());

 // Labeled upstream rows exclude host/unknown/entry/collector artifacts and follow turnOrder.
 const orderedInstruction=`发言\n[UNDERCOVER_UI_CONTEXT_V1]\n{"action":"speech","round":1,"seatNumber":1,"word":"苹果","maxChars":20,"forbidOwnWord":true}\n[/UNDERCOVER_UI_CONTEXT_V1]`;
 globalThis.fetch=fetchFor([{graph:{run:{run_id:'run-upstream',status:'running'},nodes:[{node_id:'node-a',status:'ready'},{node_id:'node-b',status:'completed'}]},pending:[{node_id:'node-a',instruction:orderedInstruction,upstream_artifacts:[{node_id:'node-host',text:'不要显示主持人'},{node_id:'node-b',text:'二号线索'},{node_id:'unknown',text:'不要显示未知'},{node_id:'speak_open',text:'不要显示入口'},{node_id:'node-a',text:'一号线索'},{node_id:'collect',text:'不要显示汇总'}]}],messages:[]}],[]);
 let u;await act(async()=>{u=mount({...base,runId:'run-upstream',style:{height:'280px'},nodeActorMap:{...base.nodeActorMap,speak_open:'host',collect:'host'},autoRefresh:false});await settle(5)});const upstreamText=text(u);assert.match(upstreamText,/1号 小甲：一号线索/);assert.match(upstreamText,/2号 小乙：二号线索/);assert.ok(upstreamText.indexOf('1号 小甲：一号线索')<upstreamText.indexOf('2号 小乙：二号线索'));assert.doesNotMatch(upstreamText,/不要显示主持人|不要显示未知|不要显示入口|不要显示汇总/);assert.ok(u.container.querySelector('[data-region="dock-body"]'));assert.match(u.container.querySelector('[data-region="dock-footer"]').textContent,/提交发言/);await act(async()=>u.unmount());

 // A phase completion is not a game finale; only a completed public host verdict opens it.
 const finale='🎉 游戏结束！平民胜利！\n🏆 终局揭秘\n主持人公开复盘：最后一轮找到了卧底。';
 const finishGraph={run:{run_id:'run-finale',status:'completed'},nodes:[{node_id:'node-host',status:'completed'}]};
 const hostMessage=content=>({content,metadata:{state_machine:{event:'output',run_id:'run-finale',node_id:'node-host'}}});
 for(const snapshot of [
   {graph:finishGraph,pending:[],messages:[hostMessage('投票已收齐，主持人正在计票或准备下一轮。')]},
   {graph:finishGraph,pending:[],messages:[hostMessage('如果游戏结束，平民胜利就公布身份。')]},
   {graph:finishGraph,pending:[],messages:[{...hostMessage(finale),pending:true}]},
   {graph:finishGraph,pending:[],messages:[{content:finale,metadata:{state_machine:{event:'output',run_id:'run-finale',node_id:'node-b'}}}]},
   {graph:finishGraph,pending:[],messages:[{content:finale,metadata:{state_machine:{event:'output',run_id:'run-finale',node_id:'node-host',visibility:'private'}}}]},
   {graph:{...finishGraph,run:{run_id:'run-finale',status:'running'}},pending:[],messages:[hostMessage(finale)]},
 ]){
   globalThis.fetch=fetchFor([snapshot],[]);let ordinary;
   await act(async()=>{ordinary=mount({...base,runId:'run-finale',phase:'voting'});await settle(5)});
   assert.equal(ordinary.container.querySelector('[aria-label="游戏结束"]'),null);
   await act(async()=>ordinary.unmount());
 }
 globalThis.fetch=fetchFor([{graph:finishGraph,pending:[],messages:[hostMessage(finale)]}],[]);
 let finalePanel;await act(async()=>{finalePanel=mount({...base,runId:'run-finale',phase:'voting'});await settle(5)});
 assert.match(finalePanel.container.querySelector('[aria-label="游戏结束"]').textContent,/平民阵营获胜/);
 assert.equal(document.activeElement,button(finalePanel,'回到圆桌'));
 assert.ok([...finalePanel.container.querySelectorAll('[data-game-content]')].every(node=>node.hasAttribute('inert')));
 await act(async()=>{document.activeElement.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));await settle()});
 assert.equal(document.activeElement?.getAttribute('aria-label'),'终局公开复盘');
 await act(async()=>{document.activeElement.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));await settle()});
 assert.equal(finalePanel.container.querySelector('[aria-label="游戏结束"]'),null);
 assert.equal(document.activeElement,button(finalePanel,'查看终局'));
 await act(async()=>{button(finalePanel,'刷新').click();await settle(5)});
 assert.ok([...finalePanel.container.querySelectorAll('[data-game-content]')].every(node=>!node.hasAttribute('inert')));
 assert.equal(finalePanel.container.querySelector('[aria-label="游戏结束"]'),null,'refresh must not reopen a dismissed finale');
 await act(async()=>{button(finalePanel,'查看终局').click();await settle()});
 assert.ok(finalePanel.container.querySelector('[aria-label="游戏结束"]'));
 await act(async()=>{button(finalePanel,'回到圆桌').click();finalePanel.render({...base,gameSessionId:'new-game',runId:'run-finale',phase:'voting',display:{showPublicReveal:false}});await settle(5)});
 const hiddenReveal=finalePanel.container.querySelector('[aria-label="游戏结束"]');
 assert.ok(hiddenReveal);assert.doesNotMatch(hiddenReveal.textContent,/最后一轮找到了卧底|平民阵营获胜/);
 await act(async()=>finalePanel.unmount());

 // Latest speech expands once; old speech markers switch the bubble's speaker in place.
 globalThis.fetch=fetchFor([{graph:{run:{run_id:'bubble-run',status:'running'},nodes:[{node_id:'node-a',status:'completed'},{node_id:'node-b',status:'completed'}]},pending:[],messages:[
   {content:'一号的公开线索',sequence:1,metadata:{state_machine:{event:'output',run_id:'bubble-run',node_id:'node-a'}}},
   {content:'二号的最新线索',sequence:2,metadata:{state_machine:{event:'output',run_id:'bubble-run',node_id:'node-b'}}},
 ]}],[]);
 let bubbles;await act(async()=>{bubbles=mount({...base,runId:'bubble-run'});await settle(5)});
 assert.match(bubbles.container.querySelector('[data-region="speech-bubble"]').textContent,/最新 · 2号 小乙/);
 assert.equal(bubbles.container.querySelectorAll('[data-region="speech-bubble"]').length,1);
 const oldMarker=[...bubbles.container.querySelectorAll('button')].find(node=>node.getAttribute('aria-label')==='回看 1号 小甲 的发言');
 await act(async()=>{oldMarker.click();await settle()});
 assert.match(bubbles.container.querySelector('[data-region="speech-bubble"]').textContent,/回看 · 1号 小甲/);
 await act(async()=>{button(bubbles,'刷新').click();await settle(5)});
 assert.match(bubbles.container.querySelector('[data-region="speech-bubble"]').textContent,/回看 · 1号 小甲/);
 await act(async()=>{bubbles.render({...base,runId:'bubble-run',phase:'voting'});await settle(5)});
 assert.equal(bubbles.container.querySelector('[data-region="speech-bubble"]'),null);
 await act(async()=>bubbles.unmount());

 // Speech state indicators are exclusive, even if a running node has public text.
 globalThis.fetch=fetchFor([{graph:{run:{run_id:'marker-run',status:'running'},nodes:[{node_id:'node-a',status:'running'},{node_id:'node-b',status:'completed'},{node_id:'node-c',status:'completed'}]},pending:[],messages:['a','b','c'].map((id,index)=>({content:`${id}的公开发言`,sequence:index+1,metadata:{state_machine:{event:'output',run_id:'marker-run',node_id:`node-${id}`}}}))}],[]);
 let markers;await act(async()=>{markers=mount({...base,runId:'marker-run',players:base.players.map(player=>({...player,eliminated:false,alive:true})),nodeActorMap:{...base.nodeActorMap,'node-c':'c'}});await settle(5)});
 assert.equal(markers.container.querySelectorAll('[data-state-marker="active_speech"]').length,1);
 assert.equal(markers.container.querySelectorAll('[data-state-marker="completed_speech"]').length,0);
 assert.ok(markers.container.querySelector('[data-host-statue]'));
 assert.doesNotMatch(markers.container.querySelector('[data-actor-id="b"]').textContent,/✓/);
 assert.equal(markers.container.querySelector('[aria-label="回看 1号 小甲 的发言"]'),null);
 assert.ok(markers.container.querySelector('[aria-label="回看 2号 小乙 的发言"]'));
 assert.match(markers.container.querySelector('[data-region="speech-bubble"]').textContent,/3号 小丙/);
 await act(async()=>markers.unmount());

}finally{globalThis.fetch=originalFetch}
console.log('Component lifecycle tests passed: dock speech/vote/abstain, privacy, compact layout, focus, transition, and recovery.');
