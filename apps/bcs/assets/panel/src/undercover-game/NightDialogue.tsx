import React from 'react';
import styled from 'styled-components';
import { assignBotAppearances } from './appearance';
import { nightJudgePortrait, nightPortraitSprite, NIGHT_PORTRAIT_VARIANTS, spriteUrl, type SpriteCoordinate } from './assets';
import type { NightDialogueEntry } from './nightView';
import type { ActorViewModel, HostActor } from './types';
import type { RoomComposition } from './sceneLayout';

const Panel = styled.section`display: grid;grid-template-columns: auto minmax(0, 1fr);gap: 10px;align-items: start;padding: 2px 0;> div{min-width: 0;}`;
const Avatar = styled.span<{ $sprite: SpriteCoordinate; $compact: boolean }>`display: block;width: ${p => (p.$compact ? 44 : 56)}px;height: ${p => (p.$compact ? 44 : 56)}px;border: 2px solid #e8b45e;background: #121f2f url("${p => spriteUrl(p.$sprite)}") center / 100% 100% no-repeat;image-rendering: pixelated;box-shadow: 0 0 0 2px #080a10;`;
const Head = styled.div`display: flex;flex-wrap: wrap;align-items: center;gap: 6px 8px;margin-bottom: 4px;color: #8f9aab;font-size: 10px;letter-spacing: 0.06em;strong{color: #f2e7ce;font-size: 12px;letter-spacing: 0.03em;}em{font-style: normal;color: #e8b45e;}`;
const Body = styled.p`margin: 0;color: #f2e7ce;font-size: 13px;line-height: 1.7;overflow-wrap: anywhere;white-space: pre-wrap;`;
const Note = styled.span`display: block;margin-top: 5px;color: #6e7686;font-size: 10px;letter-spacing: 0.04em;`;

export interface NightDialogueProps {
  actors: ActorViewModel[];
  host?: ActorViewModel;
  dialogue?: NightDialogueEntry;
  notice?: string;
  voting: boolean;
  finished: boolean;
  round: number;
  phaseLabel: string;
  completedTurns: number;
  totalTurns: number;
  composition: RoomComposition;
}

/** Bottom-panel dialogue from the static describe/discuss/lobby pages, fed by live data. */
export function NightDialogue({ actors, host, dialogue, notice, voting, finished, round, phaseLabel, completedTurns, totalTurns, composition }: NightDialogueProps) {
  const players = actors.filter((actor) => actor.kind === 'player');
  const appearances = assignBotAppearances(players.map((actor) => actor.actor.actorId), NIGHT_PORTRAIT_VARIANTS);
  const compact = composition === 'narrow';
  const hostName = (host?.actor as HostActor | undefined)?.displayName ?? '主持人';
  if (dialogue) {
    const isHost = dialogue.kind === 'host';
    const eliminated = !isHost && players.find((actor) => actor.actor.actorId === dialogue.actorId)?.state === 'eliminated';
    const variant = appearances[dialogue.actorId] ?? 0;
    return <Panel data-region="dialogue" aria-live="polite">
      <Avatar aria-hidden="true" $compact={compact} $sprite={isHost ? nightJudgePortrait : nightPortraitSprite(variant, eliminated)} />
      <div>
        <Head>
          {typeof dialogue.seatNumber === 'number' && <em>{dialogue.seatNumber}号</em>}
          <strong>{dialogue.displayName}</strong>
          <span>{isHost ? '主持人公开播报' : dialogue.stage === 'pk' ? 'PK 公开发言' : '公开发言'}</span>
        </Head>
        <Body>{dialogue.text}</Body>
        <Note>{isHost ? '主持人播报对所有人可见' : '你和所有玩家都能看到这段话'}</Note>
      </div>
    </Panel>;
  }
  if (voting) {
    return <Panel data-region="dialogue" aria-live="polite">
      <Avatar aria-hidden="true" $compact={compact} $sprite={nightJudgePortrait} />
      <div>
        <Head><strong>投票阶段</strong><span>{phaseLabel}</span></Head>
        <Body>选出你怀疑的卧底；未公开的票向不会显示。</Body>
        <Note>已投 {completedTurns} / {totalTurns} · 每人一票，请确认后提交</Note>
      </div>
    </Panel>;
  }
  if (finished) {
    return <Panel data-region="dialogue" aria-live="polite">
      <Avatar aria-hidden="true" $compact={compact} $sprite={nightJudgePortrait} />
      <div>
        <Head><strong>本局落幕</strong><span>终局</span></Head>
        <Body>公开复盘已经生成，可在终局面板中查看。</Body>
        <Note>感谢参与这场夜宴推理</Note>
      </div>
    </Panel>;
  }
  if (notice) {
    return <Panel data-region="dialogue" aria-live="polite">
      <Avatar aria-hidden="true" $compact={compact} $sprite={nightJudgePortrait} />
      <div>
        <Head><strong>阶段状态</strong><span>{phaseLabel}</span></Head>
        <Body>{notice}</Body>
        <Note>主持人正在处理当前阶段</Note>
      </div>
    </Panel>;
  }
  return <Panel data-region="dialogue" aria-live="polite">
    <Avatar aria-hidden="true" $compact={compact} $sprite={nightJudgePortrait} />
    <div>
      <Head><strong>第一章 · 夜宴</strong><span>{phaseLabel}</span></Head>
      <Body>{players.length} 位入席 · 只说特征，不说本名</Body>
      <Note>{round} 轮 · {players.length} 位玩家 · {host ? `${hostName}已就位` : '等待主持人'}</Note>
    </div>
  </Panel>;
}
