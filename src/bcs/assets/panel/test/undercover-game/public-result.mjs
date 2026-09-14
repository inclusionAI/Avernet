import assert from 'node:assert/strict';import{createRequire}from'node:module';import{JSDOM}from'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://local.test/'});Object.assign(globalThis,{window:dom.window,document:dom.window.document,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,getComputedStyle:dom.window.getComputedStyle,IS_REACT_ACT_ENVIRONMENT:true});window.confirm=()=>true;HTMLElement.prototype.attachEvent=function(){};HTMLElement.prototype.detachEvent=function(){};
let resizeCallback;globalThis.ResizeObserver=class{constructor(cb){resizeCallback=cb}observe(){}disconnect(){}};window.ResizeObserver=globalThis.ResizeObserver;
const ReactModule=await import('react');const React=ReactModule.default;const {act}=ReactModule;const {createRoot}=await import('react-dom/client');
const require=createRequire(import.meta.url);const{UndercoverGamePanel}=require('../../dist/index.umd.js');
const base={runId:'run-component',groupId:'group',sessionId:'session',gameSessionId:'session',phase:'speaking',round:1,attempt:1,host:{actorId:'host',displayName:'主持人'},seatOrder:['a','b','c'],turnOrder:['a','b'],players:[{actorId:'a',displayName:'小甲',seatNumber:1,isHuman:true},{actorId:'b',displayName:'小乙',seatNumber:2},{actorId:'c',displayName:'小丙',seatNumber:3,alive:false,eliminated:true}],nodeActorMap:{'node-a':'a','node-b':'b','node-host':'host'},currentViewerActorId:'a',rules:{speechMaxChars:5,forbidOwnWord:true,bluntness:4},publicHistory:[{round:1,speeches:[{actorId:'c',seatNumber:3,displayName:'小丙',text:'历史发言'}]}],pollingInterval:5,autoRefresh:false,display:{showVoteResults:false,showHostOutput:true}};
function response(body,status=200){return new Response(JSON.stringify({code:20000,data:body}),{status,headers:{'content-type':'application/json'}})}
function mount(props){const container=document.createElement('div');document.body.appendChild(container);Object.defineProperty(container,'getBoundingClientRect',{value:()=>({width:800,height:700,top:0,left:0,right:800,bottom:700,x:0,y:0,toJSON(){}})});const root=createRoot(container);root.render(React.createElement(UndercoverGamePanel,props));return{container,render:p=>root.render(React.createElement(UndercoverGamePanel,p)),unmount:()=>{root.unmount();container.remove()}}}
function setInputValue(element,value){const setter=Object.getOwnPropertyDescriptor(element.constructor.prototype,'value').set;setter.call(element,value);element.dispatchEvent(new window.Event('input',{bubbles:true}))}
const settle=async(ms=0)=>{await Promise.resolve();await Promise.resolve();if(ms)await new Promise(r=>setTimeout(r,ms));await Promise.resolve()};const text=p=>p.container.textContent??'';const button=(p,label)=>[...p.container.querySelectorAll('button')].find(b=>b.textContent?.includes(label));
function fetchFor(snapshots,posts){let index=0;let snapshot=snapshots[0];return async(url,init={})=>{const path=new URL(url,'http://local.test').pathname;if(init.method==='POST'&&path.endsWith('/respond')){posts.push(JSON.parse(init.body));return response({accepted:true})}if(path.endsWith('/graph')){snapshot=snapshots[Math.min(index++,snapshots.length-1)];return response(snapshot.graph)}if(path.endsWith('/pending-human-nodes'))return response(snapshot.pending);const nodeId=path.split('/').at(-1);if(path.includes('/nodes/'))return response({node:{...snapshot.graph.nodes.find(n=>n.node_id===nodeId),node_id:nodeId,artifact_text:snapshot.artifacts?.[nodeId]}});throw new Error(`Unexpected panel request: ${path}`)}}
const originalFetch=globalThis.fetch;

const resultFile='undercover-result-v1-r1-a1.json';
const result={kind:'undercover.game-result',version:1,status:'finished',gameSessionId:'session',hostActorId:'host',round:1,attempt:1,winner:'undercover',reason:'场上只剩两个人，卧底还在',summary:'游戏已经落幕，卧底坚持到了最后。'};
const params={...base,phase:'voting',resultFile};
let body=result,ready=true,owner='host',fileSession='session',lists=0,downloads=0,status='completed',fail=false,legacyProse='随意措辞的主持稿';
globalThis.fetch=async(url,init={})=>{
 const path=new URL(url,'http://local.test').pathname;
 if(path.endsWith('/graph'))return response({run:{run_id:path.split('/').at(-2),status},nodes:[{node_id:'node-host',status:'completed'}]});
 if(path.endsWith('/pending-human-nodes'))return response([]);
 if(path.endsWith('/nodes/node-host'))return response({node:{node_id:'node-host',status:'completed',artifact_text:legacyProse}});
 if(path.endsWith('/files')){lists++;return response({items:ready?[{file_id:'result',session_id:fileSession,file_name:resultFile,status:'Ready',size:1000,owner:{actor_kind:'Bot',actor_id:owner}}]:[],total:ready?1:0})}
 if(path.endsWith('/files/result/content')){assert.equal(new URL(url,'http://local.test').search,'');assert.equal(init.credentials,'same-origin');downloads++;return fail?response({},503):response(body)}
 throw new Error(`Unexpected request ${path}`);
};
const dialog=p=>p.container.querySelector('[aria-label="游戏结束"]');
let panel;
try {
 // Delayed publication after a terminal run must still be discovered.
 ready=false;await act(async()=>{panel=mount({...params,autoRefresh:true});await settle(10)});
 assert.equal(Boolean(dialog(panel)),false);ready=true;
 await act(async()=>{for(let i=0;i<12&&!dialog(panel);i++)await settle(10)});if(!dialog(panel))await act(async()=>{button(panel,'刷新').click();await settle(10)});assert.ok(dialog(panel));assert.match(dialog(panel).textContent,/卧底阵营获胜/);
 await act(async()=>{button(panel,'回到圆桌').click();await settle()});
 await act(async()=>{button(panel,'刷新').click();await settle(5)});assert.equal(Boolean(dialog(panel)),false);
 await act(async()=>panel.unmount());panel=null;
 // The result is independent of run completion and chat-session closure.
 status='running';await act(async()=>{panel=mount(params);await settle(10)});assert.ok(dialog(panel));
 await act(async()=>panel.unmount());panel=null;status='completed';
 // Source errors cannot be masked by valid legacy prose.
 legacyProse='游戏结束！平民胜利！';
 for(const invalid of [{...result,stage:'pk'},{...result,version:2},{...result,gameSessionId:'other'},{...result,attempt:2},{...result,round:2},{...result,winner:'unknown'},{...result,winner:['civilian']},'bad-json']) {
  body=invalid;await act(async()=>{panel=mount(params);await settle(10)});
  assert.equal(Boolean(dialog(panel)),false);assert.match(panel.container.querySelector('[role="alert"]').textContent,/结算结果加载失败/);
  await act(async()=>panel.unmount());panel=null;
 }
 body=result;legacyProse='随意措辞的主持稿';
 for(const identity of ['player','other-session']) {
  owner=identity==='player'?'a':'host';fileSession=identity==='other-session'?'other':'session';const before=downloads;
  await act(async()=>{panel=mount(params);await settle(10)});assert.equal(Boolean(dialog(panel)),false);assert.equal(downloads,before);
  await act(async()=>panel.unmount());panel=null;
 }
 owner='host';fileSession='session';fail=true;
 await act(async()=>{panel=mount(params);await settle(10)});assert.equal(Boolean(dialog(panel)),false);assert.ok(panel.container.querySelector('[role="alert"]'));
 await act(async()=>panel.unmount());panel=null;legacyProse='游戏结束！平民胜利！';await act(async()=>{panel=mount(params);await settle(10)});assert.ok(dialog(panel),'a transport failure retains conservative legacy compatibility');
 fail=false;await act(async()=>{button(panel,'刷新').click();await settle(10)});assert.ok(dialog(panel));
 legacyProse='新局主持稿';
 await act(async()=>panel.render({...params,sessionId:'new',gameSessionId:'new',runId:'other'}));assert.equal(Boolean(dialog(panel)),false);
 await act(async()=>panel.unmount());panel=null;
 ready=false;lists=0;
 await act(async()=>{panel=mount({...params,autoRefresh:true});await settle(10)});
 for(let i=0;i<8;i++)await act(async()=>{await settle(10)});
 assert.ok(lists>=5&&lists<=8,`terminal polling is bounded (got ${lists})`);await act(async()=>panel.unmount());panel=null;
 ready=true;
 await act(async()=>{panel=mount({...params,display:{showPublicReveal:false}});await settle(10)});
 assert.match(dialog(panel).textContent,/本局游戏结束/);assert.doesNotMatch(dialog(panel).textContent,/卧底阵营获胜|坚持到了最后/);
 console.log('Public-result lifecycle passed: late publication, identity, malformed data, retry, dismissal, privacy, and bounded polling.');
} finally {if(panel)await act(async()=>panel.unmount());globalThis.fetch=originalFetch;dom.window.close()}
