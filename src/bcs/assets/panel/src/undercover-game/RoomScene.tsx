import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import styled from 'styled-components';
import { assignBotAppearances } from './appearance';
import { botSprite, spriteUrl, sprites, type SpriteCoordinate } from './assets';
import { sceneSeatGeometry, type Facing, type RoomComposition } from './sceneLayout';
import { primaryOutputActor, stateLabels } from './visualState';
import { truncateBubbleText } from './layout';
import { SpeechBubble } from './SpeechBubble';
import knightStatue from './assets/knight-statue.svg?raw';
import { placeSpeechBubble, type BubblePlacement } from './speechBubbleLayout';
import type { ActorViewModel, HostActor, PlayerActor, PlayerState } from './types';

const Room=styled.div<{ $composition:RoomComposition }>`position:relative;min-height:${p=>p.$composition==='wide'?'520px':p.$composition==='medium'?'520px':'132px'};overflow:hidden;border:1px solid #74664e;border-radius:12px;background:#303e47;box-shadow:inset 0 0 0 5px #151e29,0 8px 24px #080e1826;isolation:isolate;&::after{content:'';position:absolute;inset:0;pointer-events:none;z-index:190;box-shadow:inset 0 0 65px #101a2970;background:radial-gradient(ellipse at 50% 50%,#f4c77c0d,transparent 65%)}`;
const Surface=styled.div`pointer-events:none;position:absolute;inset:0;`;
const Wall=styled(Surface)`height:32%;background-color:#343e4c;background-image:url("${spriteUrl(sprites.wall)}");background-repeat:repeat;background-size:48px 48px;box-shadow:inset 0 7px #171f2d,inset 0 -5px #b19360;&::after{content:'';position:absolute;inset:65% 0 5px;background:repeating-linear-gradient(90deg,#202b35 0 4px,#3e4544 4px 7px,#2c363c 7px 48px);border-top:4px solid #827053;box-shadow:0 -3px #1b2631}`;
const Floor=styled(Surface)`top:32%;background-color:#564437;background-image:url("${spriteUrl(sprites.floor)}");background-repeat:repeat;background-size:64px 64px;box-shadow:inset 0 14px 16px #151d2a55;`;
const BaseSprite=styled.span<{ $sprite:SpriteCoordinate;$scale?:number }>`display:inline-block;width:${p=>p.$sprite.width*(p.$scale??1)}px;height:${p=>p.$sprite.height*(p.$scale??1)}px;background-image:url("${p=>spriteUrl(p.$sprite)}");background-size:100% 100%;background-position:center;background-repeat:no-repeat;image-rendering:pixelated;`;
const Deco=styled(BaseSprite)<{ $x:number;$y:number }>`position:absolute;left:${p=>p.$x}%;top:${p=>p.$y}%;transform:translate(-50%,-50%);`;
const Table=styled(BaseSprite)<{ $composition:RoomComposition }>`position:absolute;left:50%;top:${p=>p.$composition==='narrow'?'72%':'63%'};width:${p=>p.$composition==='narrow'?'96px':'43%'};height:${p=>p.$composition==='narrow'?'65px':'30%'};transform:translate(-50%,-50%);filter:drop-shadow(0 9px 0 #17202b90);`;
const Knight=styled.span<{ $compact:boolean }>`display:block;flex-shrink:0;width:${p=>p.$compact?'36px':'96px'};height:${p=>p.$compact?'48px':'128px'};background:url("${`data:image/svg+xml,${encodeURIComponent(knightStatue)}`}") center / 100% 100% no-repeat;image-rendering:pixelated;filter:drop-shadow(0 4px 0 #19232c70);`;
const HostButton=styled.button<{ $compact:boolean }>`position:absolute;z-index:30;left:50%;top:${p=>p.$compact?'8%':'2%'};transform:translateX(-50%);display:flex;flex-direction:${p=>p.$compact?'row':'column'};align-items:center;gap:${p=>p.$compact?'8px':'2px'};border:0;padding:0;color:#fff;background:transparent;font:inherit;cursor:pointer;&:focus-visible{outline:2px solid #d6c399;outline-offset:4px}&:hover{filter:brightness(1.12)}`;
const HostPlate=styled.span`padding:3px 9px;border:1px solid #7c816e;background:#293840;color:#d6d9c8;box-shadow:0 2px 0 #17232b;border-radius:2px;white-space:nowrap;font-size:10px;letter-spacing:.03em;`;
const ActorButton=styled.button<{ $x:number;$y:number;$depth:number;$attention:boolean;$eliminated:boolean }>`position:absolute;z-index:${p=>40+p.$depth};left:${p=>p.$x}%;top:${p=>p.$y}%;transform:translate(-50%,-50%);display:flex;flex-direction:column;align-items:center;width:104px;border:0;padding:0;color:#fff;background:transparent;font:inherit;cursor:pointer;opacity:${p=>p.$eliminated?.62:1};${p=>p.$attention?'filter:drop-shadow(0 0 6px #f2b36c);animation:pixel-pulse 1s steps(2,end) infinite;':''}&:focus-visible{outline:3px solid #fff;outline-offset:2px}@keyframes pixel-pulse{50%{transform:translate(-50%,-53%)}}`;
const ActorStack=styled.span`position:relative;display:block;width:64px;height:72px;`;
const Portrait=styled(BaseSprite)`position:absolute;left:8px;top:0;width:48px;height:48px;`;
const IdentityFrame=styled(BaseSprite)`position:absolute;left:8px;top:0;width:48px;height:48px;`;
const Marker=styled(BaseSprite)`position:absolute;right:-4px;top:0;width:24px;height:24px;`;
const Plate=styled.span`max-width:104px;border:2px solid #201c30;padding:3px 5px;background:#26273c;font-size:11px;line-height:1.5;text-align:center;border-radius:6px;box-shadow:0 3px 8px #1117;`;
const Chair=styled(BaseSprite)<{ $facing:Facing }>`position:absolute;left:8px;top:27px;width:48px;height:48px;opacity:.95;`;
const ActorShadow=styled(BaseSprite)`position:absolute;left:10px;top:30px;`;
const CompactMarker=styled.button<{ $x:number;$y:number }>`position:absolute;z-index:170;left:calc(${p=>p.$x}% + 24px);top:calc(${p=>p.$y}% - 55px);width:32px;height:32px;border:0;padding:0 0 6px;color:#514235;background:url("${spriteUrl(sprites.bubble)}") center / 100% 100%;image-rendering:pixelated;font:inherit;font-size:11px;font-weight:700;cursor:pointer;&:hover{filter:brightness(1.12)}&:focus-visible{outline:2px solid #94dfcb;outline-offset:3px}`;
const Roster=styled.div`display:grid;gap:6px;margin-top:8px;`;
const RosterButton=styled.button<{ $attention:boolean;$eliminated:boolean;$viewer:boolean }>`display:flex;align-items:center;gap:10px;width:100%;min-height:54px;border:1px solid ${p=>p.$attention?'#d6b37e':p.$viewer?'#538878':'#354255'};border-radius:9px;padding:8px 10px;color:#edf0f6;background:${p=>p.$attention?'#d6b37e15':p.$viewer?'#263e3c':'#202c3c'};font:inherit;text-align:left;cursor:pointer;opacity:${p=>p.$eliminated?.65:1};&:hover{background:#314156} >span:first-child{flex-shrink:0}`;
const RosterText=styled.span`display:grid;min-width:0;gap:3px;font-size:12px;strong{font-weight:600} >span{color:#aebdd1;font-size:11px;overflow-wrap:anywhere}`;
const VignetteTitle=styled.span`position:absolute;left:10px;bottom:8px;z-index:80;padding:3px 7px;border:1px solid #526075;border-radius:5px;background:#182333dd;font-size:10px;`;
const Broadcast=styled.button`display:block;width:100%;margin:10px 0;padding:12px;border:1px solid #495749;border-radius:10px;color:#dce3d4;background:#a6b8900b;font:inherit;text-align:left;cursor:pointer;strong{display:block;margin-bottom:5px;color:#d6b886;font-size:10px;letter-spacing:.08em}span{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;font-size:12px;line-height:1.65}small{display:block;margin-top:6px;color:#a4b5c9;font-size:10px}`;
const RosterHeading=styled.div`display:flex;justify-content:space-between;align-items:center;margin:14px 1px 8px;color:#e0e7f2;font-size:12px;span{color:#9dacc0;font-size:10px}`;

const Rug=styled.div`position:absolute;left:50%;top:63%;width:69%;height:55%;transform:translate(-50%,-50%);background:#243e43;border:6px solid #8a7958;outline:3px solid #1e3038;box-shadow:inset 0 0 0 3px #314d50,inset 0 0 0 6px #8a7958,0 10px 0 #1d252b40;clip-path:polygon(7% 0,93% 0,93% 4%,100% 4%,100% 96%,93% 96%,93% 100%,7% 100%,7% 96%,0 96%,0 4%,7% 4%);&::after{content:'';position:absolute;inset:14px;border:1px dashed #a48e5b66;background:repeating-linear-gradient(45deg,transparent 0 16px,#8baa8509 16px 18px)}`;
const Card=styled.span<{ $side:number }>`position:absolute;left:${p=>p.$side}%;top:64%;width:14px;height:20px;background:#e0caa1;border:2px solid #b39265;box-shadow:3px 3px 0 #152f3266;transform:rotate(${p=>p.$side<50?'-12':'12'}deg);&::after{content:'';position:absolute;inset:4px 3px;border:1px solid #846f4f}`;
const Light=styled.div<{ $x:number }>`position:absolute;left:${p=>p.$x}%;top:10%;width:110px;height:170px;transform:translateX(-50%);pointer-events:none;background:radial-gradient(ellipse at 50% 30%,#f5c87535,transparent 66%);&::before{content:'';position:absolute;left:49px;top:34px;width:12px;height:17px;background:#e9c17c;border:3px solid #836841;box-shadow:0 0 0 3px #28303a,0 0 20px #ffd28755}&::after{content:'';position:absolute;left:51px;top:24px;width:8px;height:8px;background:#27333d}`;

function markerFor(state:PlayerState){return state==='action_required'?sprites.action:state==='active_speech'?sprites.speaking:state==='voted'?sprites.voted:state==='eliminated'?sprites.eliminated:state==='retrying'?sprites.retrying:state==='error'?sprites.error:undefined}
function chairSprite(facing:Facing){return facing==='front'?sprites.chairFront:facing==='rear'?sprites.chairRear:facing==='left'?sprites.chairLeft:sprites.chairRight}
export interface RoomSceneProps{pkCandidates?:string[];composition:RoomComposition;actors:ActorViewModel[];host?:ActorViewModel;currentViewerActorId?:string;voting:boolean;showHostOutput:boolean;finished?:boolean;onOpen:(actorId:string,type:'select-actor'|'select-bubble',element:HTMLElement)=>void}
export function RoomScene({pkCandidates=[],composition,actors,host,currentViewerActorId,voting,showHostOutput,finished=false,onOpen}:RoomSceneProps){
 const players=actors.filter(a=>a.kind==='player');const ids=players.map(a=>a.actor.actorId);const appearances=assignBotAppearances(ids);const geometry=sceneSeatGeometry(ids,composition);const completedSpeakers=players.filter(actor=>actor.state==='completed_speech'&&!actor.latestOutput?.pending);const primary=primaryOutputActor(completedSpeakers);
 const [reviewed,setReviewed]=useState<string|null>(null);
 const [placement,setPlacement]=useState<BubblePlacement>();
 const roomRef=useRef<HTMLDivElement>(null);
 const latestOutput=players.find(actor=>actor.actor.actorId===primary)?.latestOutput;
 useEffect(()=>setReviewed(null),[latestOutput?.identity,latestOutput?.text,voting]);
 const expanded=completedSpeakers.find(actor=>actor.actor.actorId===(reviewed??primary)&&actor.latestOutput);
 useLayoutEffect(()=>{
   const roomElement=roomRef.current;
   if(!roomElement||composition==='narrow'||voting||!expanded){setPlacement(undefined);return}
   const measure=()=>{
     const roomBounds=roomElement.getBoundingClientRect();
     const elements=[...roomElement.querySelectorAll<HTMLElement>('[data-actor-id], [data-host]')];
     const speaker=elements.find(element=>element.dataset.actorId===expanded.actor.actorId);
     if(!speaker||!roomBounds.width)return;
     const rect=(element:HTMLElement)=>{const bounds=element.getBoundingClientRect();return{x:bounds.left-roomBounds.left,y:bounds.top-roomBounds.top,width:bounds.width,height:bounds.height}};
     const next=placeSpeechBubble({x:0,y:0,width:roomBounds.width,height:roomBounds.height},rect(speaker),elements.map(rect));
     setPlacement(previous=>previous&&JSON.stringify(previous)===JSON.stringify(next)?previous:next);
   };
   measure();
   if(typeof ResizeObserver!=='undefined'){const observer=new ResizeObserver(measure);observer.observe(roomElement);return()=>observer.disconnect()}
   window.addEventListener('resize',measure);return()=>window.removeEventListener('resize',measure);
 },[composition,voting,expanded?.actor.actorId,actors]);
 const room=<Room ref={roomRef} $composition={composition} aria-label="谁是卧底像素游戏房间" data-room-composition={composition}>
  <Wall data-room-layer="wall"/><Floor data-room-layer="floor"/>{composition!=='narrow'&&<><Rug aria-hidden="true"/><Light $x={34}/><Light $x={66}/><Deco aria-hidden="true" $sprite={sprites.window} $x={16} $y={17} $scale={2}/><Deco aria-hidden="true" $sprite={sprites.window} $x={84} $y={17} $scale={2}/><Deco aria-hidden="true" $sprite={sprites.shelf} $x={7} $y={40} $scale={3}/><Deco aria-hidden="true" $sprite={sprites.plant} $x={91} $y={40} $scale={3}/><Deco aria-hidden="true" $sprite={sprites.plant} $x={7} $y={89} $scale={3}/><Deco aria-hidden="true" $sprite={sprites.shelf} $x={93} $y={89} $scale={3}/></>}

  <HostButton $compact={composition==='narrow'} data-host="true" type="button" aria-label={`打开主持人 ${(host?.actor as HostActor|undefined)?.displayName??'主持人'} 详情`} onClick={e=>host&&onOpen(host.actor.actorId,'select-actor',e.currentTarget)}><Knight data-host-statue="true" $compact={composition==='narrow'} aria-hidden="true"/><HostPlate>{host?.actor.displayName??'主持人'} · {finished?'本局结束':'主持中'}</HostPlate></HostButton>
  <Table data-room-layer="table" $composition={composition} $sprite={sprites.table} $scale={composition==='narrow'?2:4} aria-hidden="true"/>
  {composition!=='narrow'&&<><Card aria-hidden="true" $side={39}/><Card aria-hidden="true" $side={59}/></>}
  {composition!=='narrow'&&players.map((actor,index)=>{const p=actor.actor as PlayerActor,g=geometry[index];const human=p.actorId===currentViewerActorId;const attention=actor.state==='active_speech'||actor.state==='action_required';const marker=markerFor(actor.state as PlayerState);return <React.Fragment key={p.actorId}><ActorButton data-pk-candidate={pkCandidates.includes(p.actorId)||undefined} data-player-state={actor.state} data-actor-id={p.actorId} $x={g.x} $y={g.y} $depth={g.depth} $attention={attention} $eliminated={actor.state==='eliminated'} type="button" onClick={e=>onOpen(p.actorId,'select-actor',e.currentTarget)} aria-label={`打开 ${p.seatNumber}号 ${p.displayName} 详情，${stateLabels[actor.state as PlayerState]}${human?'，当前玩家':''}`}><ActorStack><Chair $sprite={chairSprite(g.facing)} $facing={g.facing}/><ActorShadow $sprite={sprites.actorShadow}/><Portrait $sprite={botSprite(appearances[p.actorId])}/>{human&&<IdentityFrame $sprite={sprites.humanFrame}/>} {marker&&<Marker data-state-marker={actor.state} aria-hidden="true" $sprite={marker}/>}</ActorStack><Plate><strong>{pkCandidates.includes(p.actorId)?'PK · ':''}{p.seatNumber}号 · {p.displayName}{human&&p.displayName!=='你'?'（你）':''}</strong><br/>{stateLabels[actor.state as PlayerState]}</Plate></ActorButton>{actor.state==='completed_speech'&&actor.latestOutput&&!actor.latestOutput.pending&&!voting&&p.actorId!==expanded?.actor.actorId&&<CompactMarker $x={g.x} $y={g.y} type="button" onClick={()=>setReviewed(p.actorId)} aria-label={`回看 ${p.seatNumber}号 ${p.displayName} 的发言`} title="点击在角色旁回看发言">•••<span style={{position:'absolute',width:1,height:1,overflow:'hidden',clipPath:'inset(50%)'}}>{actor.latestOutput.text}</span></CompactMarker>}</React.Fragment>})}
  {composition!=='narrow'&&!voting&&expanded?.latestOutput&&<SpeechBubble key={expanded.actor.actorId} focusOnOpen={reviewed!==null} ready={Boolean(placement)} placement={placement??{x:0,y:0,width:216,height:96,side:'above',tail:24}} name={`${(expanded.actor as PlayerActor).seatNumber}号 ${expanded.actor.displayName}`} text={expanded.latestOutput.text} latest={expanded.actor.actorId===primary} onOpen={element=>onOpen(expanded.actor.actorId,'select-bubble',element)}/>}
  {composition==='narrow'&&<VignetteTitle data-region="room-caption">室内圆桌 · {players.find(a=>a.state==='active_speech'||a.state==='action_required')?.actor.displayName??'等待下一步'}</VignetteTitle>}
 </Room>;
 const broadcast=host?.latestOutput&&showHostOutput?<Broadcast type="button" onClick={e=>onOpen(host.actor.actorId,'select-bubble',e.currentTarget)} aria-label="查看主持人完整播报"><strong>主持人播报</strong><span>{host.latestOutput.text}</span><small>查看完整播报 ↗</small></Broadcast>:null;
 if(composition!=='narrow')return <>{room}{broadcast}</>;
 return <>{room}<RosterHeading>圆桌玩家<span>点击查看发言与状态</span></RosterHeading><Roster data-region="participant-roster">{players.map(actor=>{const p=actor.actor as PlayerActor;const human=p.actorId===currentViewerActorId;return <RosterButton data-pk-candidate={pkCandidates.includes(p.actorId)||undefined} key={p.actorId} $eliminated={actor.state==='eliminated'} $viewer={human} $attention={actor.state==='action_required'||actor.state==='active_speech'} type="button" onClick={e=>onOpen(p.actorId,'select-actor',e.currentTarget)} aria-label={`打开 ${p.seatNumber}号 ${p.displayName} 详情`}><BaseSprite $sprite={botSprite(appearances[p.actorId])}/><RosterText><strong>{pkCandidates.includes(p.actorId)?'PK · ':''}{p.seatNumber}号 · {p.displayName}{human&&p.displayName!=='你'?'（你）':''}</strong><span>{stateLabels[actor.state as PlayerState]}{actor.latestOutput&&!voting?` · ${truncateBubbleText(actor.latestOutput.text,38).text}`:''}</span></RosterText></RosterButton>})}</Roster>{broadcast}</>;
}
