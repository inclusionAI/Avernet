import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import styled from 'styled-components';
import { assignBotAppearances } from './appearance';
import { atlasUrls, nightJudgePortrait, nightPortraitSprite, NIGHT_PORTRAIT_VARIANTS, sprites, spriteUrl, type SpriteCoordinate } from './assets';
import { nightSeatSlots, NIGHT_HOST_SLOT } from './nightStage';
import { primaryOutputActor, stateLabels } from './visualState';
import { truncateBubbleText } from './layout';
import { SpeechBubble } from './SpeechBubble';
import { placeSpeechBubble, type BubblePlacement } from './speechBubbleLayout';
import type { ActorViewModel, HostActor, PlayerActor, PlayerState } from './types';
import type { RoomComposition } from './sceneLayout';

const Stage = styled.div<{ $composition: RoomComposition }>`
  position: relative;
  width: 100%;
  overflow: hidden;
  border: 2px solid #8e6531;
  border-radius: 8px;
  background: #080a10 url("${atlasUrls['night-hall']}") center / 100% 100% no-repeat;
  box-shadow: inset 0 0 0 3px #080a10, inset 0 0 46px #05070caa, 0 10px 24px #05070c88;
  isolation: isolate;
  aspect-ratio: ${p => (p.$composition === 'narrow' ? '16 / 7' : '16 / 9')};
  min-height: 0;
  margin: 0 auto;
  container-type: inline-size;
  image-rendering: auto;
  @supports (height: 1cqh) {
    width: ${p => (p.$composition === 'narrow' ? 'min(100cqw, calc(100cqh * 16 / 7))' : '100%')};
  }
  &::after {
    content: '';
    position: absolute;
    inset: 0;
    z-index: 90;
    pointer-events: none;
    background: radial-gradient(ellipse at 50% 40%, transparent 42%, #05070c66 100%);
  }
`;
const HostButton = styled.button`
  position: absolute;
  z-index: 30;
  left: ${NIGHT_HOST_SLOT.x}%;
  top: ${NIGHT_HOST_SLOT.y}%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  border: 0;
  padding: 0;
  color: #f2e7ce;
  background: transparent;
  font: inherit;
  cursor: pointer;
  &:focus-visible {
    outline: 2px solid #ffe7b0;
    outline-offset: 3px;
  }
  &:hover {
    filter: brightness(1.1);
  }
`;
const Seat = styled.button<{ $x: number; $y: number; $attention: boolean; $eliminated: boolean }>`
  position: absolute;
  z-index: ${p => 20 + Math.round(p.$y)};
  left: ${p => p.$x}%;
  top: ${p => p.$y}%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  width: clamp(96px, 16cqw, 150px);
  border: 0;
  padding: 0;
  color: #f2e7ce;
  background: transparent;
  font: inherit;
  text-align: center;
  cursor: pointer;
  opacity: ${p => (p.$eliminated ? 0.66 : 1)};
  ${p => (p.$attention ? 'filter: drop-shadow(0 0 6px #e8b45ecc);' : '')}
  &:focus-visible {
    outline: 2px solid #ffe7b0;
    outline-offset: 3px;
  }
`;
const PortraitBox = styled.span<{ $attention: boolean; $human: boolean; $selected: boolean; $eliminated: boolean }>`
  position: relative;
  display: block;
  width: clamp(38px, 8.2cqw, 86px);
  height: clamp(38px, 8.2cqw, 86px);
  border: 2px solid ${p => (p.$selected ? '#ffe7b0' : p.$human ? '#5fb7a6' : p.$attention ? '#e8b45e' : '#8e6531')};
  background: #101b2a;
  box-shadow: 0 0 0 2px #080a10, 0 3px 0 #080a10, ${p => (p.$selected ? '0 0 14px #e8b45ecc' : 'none')};
  image-rendering: pixelated;
  &::after {
    content: '';
    position: absolute;
    inset: 0;
    pointer-events: none;
    border: ${p => (p.$eliminated ? '2px solid #c0504a66' : 'none')};
  }
`;
const Portrait = styled.span<{ $sprite: SpriteCoordinate }>`
  display: block;
  width: 100%;
  height: 100%;
  background: url("${p => spriteUrl(p.$sprite)}") center / 100% 100% no-repeat;
  image-rendering: pixelated;
`;
const Plate = styled.span`
  display: block;
  max-width: clamp(92px, 18cqw, 190px);
  padding: 2px 6px;
  border: 1px solid #8e6531;
  background: #1d1811ee;
  box-shadow: 0 2px 0 #080a10;
  font-size: clamp(9px, 1.45cqw, 13px);
  line-height: 1.35;
  white-space: normal;
  strong {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
    overflow-wrap: anywhere;
    font-size: clamp(10px, 1.8cqw, 15px);
    font-weight: 700;
    line-height: 1.3;
    color: #f2e7ce;
  }
  span {
    color: #8f9aab;
    font-size: clamp(8.5px, 1.3cqw, 11px);
    letter-spacing: 0.04em;
    white-space: nowrap;
  }
`;
const HostPlate = styled(Plate)`
  strong {
    color: #ffe7b0;
  }
`;
const Marker = styled.span<{ $sprite: SpriteCoordinate }>`
  position: absolute;
  right: -6px;
  top: -6px;
  width: clamp(22px, 2.6cqw, 30px);
  height: clamp(22px, 2.6cqw, 30px);
  background: url("${p => spriteUrl(p.$sprite)}") center / 100% 100% no-repeat;
  image-rendering: pixelated;
  filter: drop-shadow(0 1px 0 #080a10);
`;
const PkBadge = styled.span`
  position: absolute;
  left: -6px;
  top: -6px;
  padding: 0 4px;
  border: 1px solid #ffe7b0;
  background: #c0504a;
  color: #fff3e2;
  font-size: 8px;
  font-weight: 800;
  letter-spacing: 0.08em;
  line-height: 14px;
`;
const Reticle = styled.span`
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 60;
  width: calc(100% + 18px);
  height: calc(100% + 18px);
  transform: translate(-50%, -50%);
  border: 2px dashed #ffe7b0cc;
  box-shadow: 0 0 12px #e8b45e66;
  pointer-events: none;
  &::after {
    content: '';
    position: absolute;
    inset: 6px;
    border: 2px solid #e8b45e;
  }
`;
const CompactMarker = styled.button<{ $x: number; $y: number }>`
  position: absolute;
  z-index: 40;
  left: calc(${p => p.$x}% + 20px);
  top: calc(${p => p.$y}% - 44px);
  width: 28px;
  height: 28px;
  border: 0;
  padding: 0;
  color: #514235;
  background: url("${spriteUrl(sprites.bubble)}") center / 100% 100% no-repeat;
  image-rendering: pixelated;
  font: inherit;
  font-size: 10px;
  font-weight: 700;
  cursor: pointer;
  &:hover {
    filter: brightness(1.12);
  }
  &:focus-visible {
    outline: 2px solid #ffe7b0;
    outline-offset: 2px;
  }
`;
const VignetteTitle = styled.span`
  position: absolute;
  left: 10px;
  bottom: 8px;
  z-index: 80;
  padding: 3px 8px;
  border: 1px solid #8e6531;
  border-radius: 4px;
  background: #0b0e16dd;
  color: #f2e7ce;
  font-size: 11px;
`;
const Broadcast = styled.button`
  display: block;
  width: 100%;
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid #8e6531;
  border-radius: 8px;
  color: #f2e7ce;
  background: #121f2f;
  font: inherit;
  text-align: left;
  cursor: pointer;
  strong {
    display: block;
    margin-bottom: 4px;
    color: #e8b45e;
    font-size: 10px;
    letter-spacing: 0.12em;
  }
  span {
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
    overflow-wrap: anywhere;
    font-size: 12px;
    line-height: 1.65;
  }
  small {
    display: block;
    margin-top: 5px;
    color: #8f9aab;
    font-size: 10px;
  }
`;
const RosterHeading = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 12px 1px 6px;
  color: #f2e7ce;
  font-size: 12px;
  span {
    color: #8f9aab;
    font-size: 10px;
  }
`;
const Roster = styled.div`
  display: grid;
  gap: 6px;
`;
const RosterButton = styled.button<{ $attention: boolean; $eliminated: boolean; $viewer: boolean }>`
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 54px;
  border: 1px solid ${p => (p.$attention ? '#e8b45e' : p.$viewer ? '#5fb7a6' : '#3e5570')};
  border-radius: 8px;
  padding: 7px 10px;
  color: #f2e7ce;
  background: ${p => (p.$attention ? '#e8b45e12' : p.$viewer ? '#16233a' : '#101b2a')};
  font: inherit;
  text-align: left;
  cursor: pointer;
  opacity: ${p => (p.$eliminated ? 0.66 : 1)};
  &:hover {
    background: #16233a;
  }
`;
const RosterPortrait = styled.span<{ $sprite: SpriteCoordinate }>`
  flex: none;
  display: block;
  width: 42px;
  height: 42px;
  border: 1px solid #8e6531;
  background: #121f2f url("${p => spriteUrl(p.$sprite)}") center / 100% 100% no-repeat;
  image-rendering: pixelated;
`;
const RosterText = styled.span`
  display: grid;
  min-width: 0;
  gap: 2px;
  font-size: 12px;
  strong {
    font-weight: 700;
  }
  > span {
    color: #8f9aab;
    font-size: 11px;
    overflow-wrap: anywhere;
  }
`;

function markerFor(state: PlayerState) {
  if (state === 'action_required') return sprites.action;
  if (state === 'active_speech') return sprites.speaking;
  if (state === 'voted') return sprites.voted;
  if (state === 'eliminated') return sprites.eliminated;
  if (state === 'retrying') return sprites.retrying;
  if (state === 'error') return sprites.error;
  return undefined;
}

export interface RoomSceneProps {
  pkCandidates?: string[];
  composition: RoomComposition;
  actors: ActorViewModel[];
  host?: ActorViewModel;
  currentViewerActorId?: string;
  voting: boolean;
  showHostOutput: boolean;
  finished?: boolean;
  selectedVoteTarget?: string;
  onOpen: (actorId: string, type: 'select-actor' | 'select-bubble', element: HTMLElement) => void;
}

export function RoomScene(props: RoomSceneProps) {
  const { pkCandidates = [], composition, actors, host, currentViewerActorId, voting, showHostOutput, finished = false, selectedVoteTarget, onOpen } = props;
  const players = actors.filter((actor) => actor.kind === 'player');
  const ids = players.map((actor) => actor.actor.actorId);
  const appearances = assignBotAppearances(ids, NIGHT_PORTRAIT_VARIANTS);
  const slots = nightSeatSlots(ids.length);
  const completedSpeakers = players.filter((actor) => actor.state === 'completed_speech' && !actor.latestOutput?.pending);
  const primary = primaryOutputActor(completedSpeakers);
  const [reviewed, setReviewed] = useState<string | null>(null);
  const [placement, setPlacement] = useState<BubblePlacement>();
  const roomRef = useRef<HTMLDivElement>(null);
  const latestOutput = players.find((actor) => actor.actor.actorId === primary)?.latestOutput;
  useEffect(() => setReviewed(null), [latestOutput?.identity, latestOutput?.text, voting]);
  const expanded = completedSpeakers.find((actor) => actor.actor.actorId === (reviewed ?? primary) && actor.latestOutput);
  useLayoutEffect(() => {
    const roomElement = roomRef.current;
    if (!roomElement || composition === 'narrow' || voting || !expanded) {
      setPlacement(undefined);
      return undefined;
    }
    const measure = () => {
      const roomBounds = roomElement.getBoundingClientRect();
      const elements = [...roomElement.querySelectorAll<HTMLElement>('[data-actor-id], [data-host]')];
      const speaker = elements.find((element) => element.dataset.actorId === expanded.actor.actorId);
      if (!speaker || !roomBounds.width) return;
      const rect = (element: HTMLElement) => {
        const bounds = element.getBoundingClientRect();
        return { x: bounds.left - roomBounds.left, y: bounds.top - roomBounds.top, width: bounds.width, height: bounds.height };
      };
      const next = placeSpeechBubble({ x: 0, y: 0, width: roomBounds.width, height: roomBounds.height }, rect(speaker), elements.map(rect));
      setPlacement((previous) => (previous && JSON.stringify(previous) === JSON.stringify(next) ? previous : next));
    };
    measure();
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(measure);
      observer.observe(roomElement);
      return () => observer.disconnect();
    }
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [composition, voting, expanded?.actor.actorId, actors]);
  const hostActor = host?.actor as HostActor | undefined;
  const stage = <Stage ref={roomRef} $composition={composition} aria-label="谁是卧底夜宴像素牌桌" data-room-composition={composition} data-room-layer="night-hall">
    <HostButton data-host="true" data-host-portrait="true" type="button" aria-label={`打开主持人 ${hostActor?.displayName ?? '主持人'} 详情`} onClick={(element) => host && onOpen(host.actor.actorId, 'select-actor', element.currentTarget)}>
      <PortraitBox $attention={false} $human={false} $selected={false} $eliminated={false}>
        <Portrait $sprite={nightJudgePortrait} aria-hidden="true" />
      </PortraitBox>
      <HostPlate><strong>{hostActor?.displayName ?? '主持人'}</strong><span>{finished ? '本局结束' : '主持中'}</span></HostPlate>
    </HostButton>
    {composition !== 'narrow' && players.map((actor, index) => {
      const player = actor.actor as PlayerActor;
      const slot = slots[index];
      const human = player.actorId === currentViewerActorId;
      const attention = actor.state === 'active_speech' || actor.state === 'action_required';
      const eliminated = actor.state === 'eliminated';
      const marker = markerFor(actor.state as PlayerState);
      const plate = <Plate data-seat-plate="true"><strong>{pkCandidates.includes(player.actorId) ? 'PK · ' : ''}{player.seatNumber}号 · {player.displayName}{human && player.displayName !== '你' ? '（你）' : ''}</strong><span>{stateLabels[actor.state as PlayerState]}</span></Plate>;
      return <Seat key={player.actorId} data-actor-id={player.actorId} data-player-state={actor.state} data-pk-candidate={pkCandidates.includes(player.actorId) || undefined} data-vote-target={selectedVoteTarget === player.actorId || undefined} $x={slot.x} $y={slot.y} $attention={attention} $eliminated={eliminated} type="button" onClick={(element) => onOpen(player.actorId, 'select-actor', element.currentTarget)} aria-label={`打开 ${player.seatNumber}号 ${player.displayName} 详情，${stateLabels[actor.state as PlayerState]}${human ? '，当前玩家' : ''}`}>
        {composition === 'wide' && slot.labelAbove && plate}
        <PortraitBox $attention={attention} $human={human} $selected={selectedVoteTarget === player.actorId || player.actorId === expanded?.actor.actorId} $eliminated={eliminated}>
          <Portrait $sprite={nightPortraitSprite(appearances[player.actorId] ?? 0, eliminated)} />
          {marker && <Marker data-state-marker={actor.state} aria-hidden="true" $sprite={marker} />}
          {pkCandidates.includes(player.actorId) && <PkBadge>PK</PkBadge>}
          {selectedVoteTarget === player.actorId && <Reticle aria-hidden="true" />}
        </PortraitBox>
        {composition === 'wide' && !slot.labelAbove && plate}
      </Seat>;
    })}
    {composition === 'wide' && players.map((actor, index) => {
      const player = actor.actor as PlayerActor;
      if (actor.state !== 'completed_speech' || !actor.latestOutput || actor.latestOutput.pending || voting || player.actorId === expanded?.actor.actorId) return null;
      return <CompactMarker key={`marker-${player.actorId}`} $x={slots[index].x} $y={slots[index].y} type="button" onClick={() => setReviewed(player.actorId)} aria-label={`回看 ${player.seatNumber}号 ${player.displayName} 的发言`} title="点击在角色旁回看发言">•••<span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clipPath: 'inset(50%)' }}>{actor.latestOutput.text}</span></CompactMarker>;
    })}
    {composition === 'wide' && !voting && expanded?.latestOutput && placement?.targetX!==undefined && placement.targetY!==undefined && <svg aria-hidden="true" style={{position:'absolute',inset:0,width:'100%',height:'100%',pointerEvents:'none',zIndex:19}}><line x1={Math.max(placement.x,Math.min(placement.x+placement.width,placement.targetX))} y1={Math.max(placement.y,Math.min(placement.y+placement.height,placement.targetY))} x2={placement.targetX} y2={placement.targetY} stroke="#d2b978" strokeWidth="2" strokeDasharray="4 3"/></svg>}
    {composition === 'wide' && !voting && expanded?.latestOutput && <SpeechBubble key={expanded.actor.actorId} focusOnOpen={reviewed !== null} ready={Boolean(placement)} placement={placement ?? { x: 0, y: 0, width: 216, height: 96, side: 'above' }} name={`${(expanded.actor as PlayerActor).seatNumber}号 ${expanded.actor.displayName}`} text={expanded.latestOutput.text} latest={expanded.actor.actorId === primary} onOpen={(element) => onOpen(expanded.actor.actorId, 'select-bubble', element)} />}
    {composition === 'narrow' && <VignetteTitle data-region="room-caption">室内圆桌 · {players.find((actor) => actor.state === 'active_speech' || actor.state === 'action_required')?.actor.displayName ?? '等待下一步'}</VignetteTitle>}
  </Stage>;
  const broadcast = host?.latestOutput && showHostOutput
    ? <Broadcast type="button" data-region="host-broadcast" onClick={(element) => onOpen(host.actor.actorId, 'select-bubble', element.currentTarget)} aria-label="查看主持人完整播报"><strong>主持人播报</strong><span>{host.latestOutput.text}</span><small>查看完整播报 ↗</small></Broadcast>
    : null;
  if (composition === 'wide') return <>{stage}{broadcast}</>;
  return <>
    {stage}
    <RosterHeading>圆桌玩家<span>点击查看发言与状态</span></RosterHeading>
    <Roster data-region="participant-roster">
      {players.map((actor) => {
        const player = actor.actor as PlayerActor;
        const human = player.actorId === currentViewerActorId;
        return <RosterButton data-pk-candidate={pkCandidates.includes(player.actorId) || undefined} key={player.actorId} $eliminated={actor.state === 'eliminated'} $viewer={human} $attention={actor.state === 'action_required' || actor.state === 'active_speech'} type="button" onClick={(element) => onOpen(player.actorId, 'select-actor', element.currentTarget)} aria-label={`打开 ${player.seatNumber}号 ${player.displayName} 详情`}>
          <RosterPortrait $sprite={nightPortraitSprite(appearances[player.actorId] ?? 0, actor.state === 'eliminated')} aria-hidden="true" />
          <RosterText><strong>{pkCandidates.includes(player.actorId) ? 'PK · ' : ''}{player.seatNumber}号 · {player.displayName}{human && player.displayName !== '你' ? '（你）' : ''}</strong><span>{stateLabels[actor.state as PlayerState]}{actor.latestOutput && !voting ? ` · ${truncateBubbleText(actor.latestOutput.text, 38).text}` : ''}</span></RosterText>
        </RosterButton>;
      })}
    </Roster>
    {broadcast}
  </>;
}
