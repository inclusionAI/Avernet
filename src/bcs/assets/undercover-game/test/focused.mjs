import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url);const api=require('../dist/index.umd.js');
const {ApiRequestError,eligibleVoteCandidates,getSeatCoordinates,latestAttemptEvents,mapPublicOutputEvents,mergePublicOutputEvents,normalizeCurrentRoundUpstream,normalizePanelParams,normalizeUndercoverGameViewModel,parsePrivateActionContext,publicEventFromNodeDetail,requestJson,serializeVoteAbstain,serializeVoteTarget,spriteUrl,sprites,truncateBubbleText,unicodeLength,validateSpeech}=api;
assert.equal(api.joinUrl('/api/','state-machine-runs'),'/api/state-machine-runs');
assert.deepEqual(api.unwrapEnvelope({code:20000,request_id:'r',data:{ok:true}}),{ok:true});
await assert.rejects(requestJson('/api','/conflict',{},async()=>new Response(JSON.stringify({message:'expired'}),{status:409})),e=>e instanceof ApiRequestError&&e.conflict);
const params=normalizePanelParams({runId:'run-example',groupId:'group-example',sessionId:'session-example',gameSessionId:'game-example',phase:'voting',round:2,attempt:2,host:{actorId:'host',displayName:'Host'},seatOrder:['a','b','c'],turnOrder:['a','b'],players:[{actorId:'a',displayName:'A',seatNumber:1,isHuman:true},{actorId:'b',displayName:'B',seatNumber:2},{actorId:'c',displayName:'C',seatNumber:3,alive:false}],nodeActorMap:{'node-a':'a','node-b':'b','node-host':'host','node-host-2':'host'},currentViewerActorId:'a',currentAction:{actorId:'a',type:'vote',nodeId:'node-a'},rules:{speechMaxChars:25,forbidOwnWord:true},publicHistory:[{round:1,speeches:[{actorId:'c',seatNumber:3,displayName:'C',text:'older clue'}]}],voteCandidates:[{actorId:'b',displayName:'B',seatNumber:2,eligible:true},{actorId:'c',displayName:'C',seatNumber:3,eligible:true,eliminated:true}]});
assert.equal(params.apiBaseUrl,'/bcnproxy');assert.deepEqual(params.turnOrder,['a','b']);assert.equal(params.players[2].seatNumber,3);assert.equal(params.publicHistory[0].speeches[0].text,'older clue');assert.deepEqual(eligibleVoteCandidates(params.voteCandidates).map(x=>x.actorId),['b']);assert.deepEqual(getSeatCoordinates(params.seatOrder).map(x=>x.actorId),['a','b','c']);assert.deepEqual(truncateBubbleText('你好世界',3),{text:'你好…',truncated:true});
const wallSprite=decodeURIComponent(spriteUrl(sprites.wall));const floorSprite=decodeURIComponent(spriteUrl(sprites.floor));const tableSprite=decodeURIComponent(spriteUrl(sprites.table));assert.match(wallSprite,/viewBox="0 0 32 32"/);assert.match(floorSprite,/viewBox="32 0 32 32"/);assert.match(tableSprite,/viewBox="96 0 32 32"/);assert.notEqual(wallSprite,floorSprite);
const legacy=normalizePanelParams({runId:'r',groupId:'g',sessionId:'s',phase:'speaking',round:1,host:{actorId:'h',displayName:'H'},seatOrder:['a'],players:[{actorId:'a',displayName:'A',publicFields:{seat:7}}],nodeActorMap:{}});assert.equal(legacy.players[0].seatNumber,7);assert.deepEqual(legacy.turnOrder,['a']);
const instruction=`human text\n[UNDERCOVER_UI_CONTEXT_V1]\n{"action":"speech","round":2,"seatNumber":1,"word":"秘密词","maxChars":5,"forbidOwnWord":true,"bluntness":3}\n[/UNDERCOVER_UI_CONTEXT_V1]`;
assert.deepEqual(parsePrivateActionContext(instruction).context,{version:1,action:'speech',round:2,seatNumber:1,word:'秘密词',maxChars:5,forbidOwnWord:true,bluntness:3});assert.equal(parsePrivateActionContext('plain').present,false);assert.match(parsePrivateActionContext('[UNDERCOVER_UI_CONTEXT_V2]\n{"word":"DO_NOT_LEAK"}').error,/不支持/);assert.doesNotMatch(parsePrivateActionContext('[UNDERCOVER_UI_CONTEXT_V1]\n{"word":"DO_NOT_LEAK"}').error,/DO_NOT_LEAK/);
assert.equal(unicodeLength('A😀中'),3);assert.deepEqual(validateSpeech('',5),{valid:false,error:'empty',count:0});assert.equal(validateSpeech('一二三四五六',5).error,'too_long');assert.equal(validateSpeech('这是秘密词',20,'秘密词',true).error,'contains_own_word');assert.equal(validateSpeech('安全描述',20,'秘密词',true).valid,true);assert.equal(serializeVoteTarget('b'),'\{"kind":"vote","target_actor_id":"b"\}'.replace('\\{','{').replace('\\}','}'));assert.equal(serializeVoteAbstain(),'{"kind":"vote","abstain":true}');
const messages=[{id:'raw-vote',sequence:1,content:'{"kind":"vote","target_actor_id":"b"}',metadata:{state_machine:{event:'output',run_id:'run-example',node_id:'node-a',attempt:1}}},{id:'host1',sequence:2,content:'opening',metadata:{state_machine:{event:'output',run_id:'run-example',node_id:'node-host',attempt:1}}},{id:'host2',sequence:3,content:'tallying',metadata:{state_machine:{event:'output',run_id:'run-example',node_id:'node-host-2',attempt:1}}},{id:'other',sequence:4,content:'other',metadata:{state_machine:{event:'output',run_id:'other',node_id:'node-a',attempt:1}}}];
const events=mapPublicOutputEvents(messages,params);assert.deepEqual(events.map(x=>x.text),['opening','tallying']);assert.doesNotMatch(JSON.stringify(events),/target_actor_id|"b"/);
const model=normalizeUndercoverGameViewModel(params,{run:{run_id:'run-example',status:'running'},nodes:[{node_id:'node-a',status:'completed',attempt:2},{node_id:'node-b',status:'running'},{node_id:'node-host',status:'completed'},{node_id:'node-host-2',status:'running'}]},[{node_id:'node-a',attempt:2,instruction,upstream_artifacts:[{node_id:'x',text:'prior clue'}]}],messages);
assert.deepEqual(model.actors.filter(x=>x.kind==='player').map(x=>x.actor.actorId),['a','b','c']);assert.equal(model.actors.find(x=>x.actor.actorId==='c').state,'eliminated');assert.equal(model.actors.find(x=>x.kind==='host').latestOutput.text,'tallying');assert.equal(model.pendingHumanActorId,'a');assert.equal(model.totalTurns,2);assert.equal(model.completedTurns,1);assert.equal(model.actors.find(x=>x.actor.actorId==='c').publicHistory[0].text,'older clue');
assert.throws(()=>normalizePanelParams({phase:'speaking',round:1}),/runId is required/);

const speakingParams={...params,phase:'speaking'};
const normalizedUpstream=normalizeCurrentRoundUpstream([
  {node_id:'node-host',text:'主持人开场'},
  {node_id:'node-b',text:'二号先说'},
  {node_id:'unknown',text:'未知节点'},
  {node_id:'speak_open',text:'入口节点'},
  {node_id:'node-a',text:'一号后返回但应排前'},
  {node_id:'collect',text:'汇总节点'},
],speakingParams);
assert.deepEqual(normalizedUpstream.map(x=>`${x.seatNumber}号 ${x.displayName}：${x.text}`),['1号 A：一号后返回但应排前','2号 B：二号先说']);

const speechDetail=publicEventFromNodeDetail({node:{node_id:'node-a',run_id:'run-example',status:'completed',attempt:1,completed_at:10,artifact_text:'来自节点详情的公开发言'}},speakingParams);
assert.equal(speechDetail.text,'来自节点详情的公开发言');
assert.equal(speechDetail.source,'node-detail');
const voteDetail=publicEventFromNodeDetail({node:{node_id:'node-b',run_id:'run-example',status:'completed',attempt:1,artifact_text:'{"kind":"vote","target_actor_id":"a"}'}},params);
assert.equal(voteDetail.text,'已投票');
assert.doesNotMatch(JSON.stringify(voteDetail),/target_actor_id|"a"/);
assert.equal(publicEventFromNodeDetail({node:{node_id:'node-a',run_id:'other-run',status:'completed',artifact_text:'stale secret'}},speakingParams),undefined);

const older={...speechDetail,identity:'game-example:run-example:node-a:1',attempt:1,text:'旧尝试'};
const newer={...speechDetail,identity:'game-example:run-example:node-a:2',attempt:2,text:'新尝试'};
const merged=latestAttemptEvents(mergePublicOutputEvents([older],[newer]));
assert.deepEqual(merged.map(x=>x.text),['新尝试']);
const detailModel=normalizeUndercoverGameViewModel(speakingParams,{run:{run_id:'run-example',status:'running'},nodes:[{node_id:'node-a',status:'completed',attempt:2}]},[],[],[older,newer]);
assert.equal(detailModel.actors.find(x=>x.actor.actorId==='a').latestOutput.text,'新尝试');

console.log('Focused undercover-game behavior tests passed.');
