import React, { useEffect, useRef } from 'react';
import styled from 'styled-components';
import { assignBotAppearances } from './appearance';
import { nightPortraitSprite, NIGHT_PORTRAIT_VARIANTS, spriteUrl } from './assets';
import type { GameResult } from './gameResult';
import type { PlayerActor } from './types';

const Backdrop = styled.div`position: absolute;inset: 0;z-index: 1100;overflow-y: auto;display: flex;align-items: center;justify-content: center;padding: clamp(10px, 2.5vw, 28px);background: rgba(5, 7, 12, 0.92);`;
const Result = styled.section`display: flex;flex-direction: column;align-items: center;width: min(1080px, 100%);height: 100%;max-height: 100%;min-height: 0;color: #f2e7ce;container: finale / size;`;
const Header = styled.div`flex: 0 0 auto;display: flex;flex-direction: column;align-items: center;width: 100%;`;
const Body = styled.div`flex: 1 1 auto;min-height: 0;overflow-y: auto;width: 100%;`;
const Kicker = styled.div`color: #8f9aab;font-size: clamp(10px, 1.1vw, 13px);`;
const Title = styled.h2`margin: 0.35em 0 0.1em;color: #ffe7b0;font-size: clamp(28px, 4.6vw, 58px);font-weight: 800;line-height: 1.2;`;
const Sub = styled.p`margin: 0;color: #8f9aab;font-size: clamp(11px, 1.35vw, 18px);`;
const Winner = styled.div<{ $known: boolean }>`margin: 0.7em 0 0.2em;padding: 0.4em 1.1em;border: 1px solid ${p => (p.$known ? '#e8b45e' : '#6e7686')};color: ${p => (p.$known ? '#ffe7b0' : '#aab4c4')};background: ${p => (p.$known ? '#e8b45e12' : '#101b2a')};font-size: clamp(11px, 1.25vw, 15px);`;
const Cards = styled.div`display: grid;grid-template-columns: repeat(auto-fit, minmax(clamp(86px, 10vw, 150px), 1fr));justify-content: center;gap: clamp(6px, 1vw, 16px);width: 100%;`;
const Card = styled.div<{ $eliminated: boolean; $spy: boolean }>`display: flex;flex-direction: column;align-items: center;min-width: 0;padding: clamp(5px, 0.7vw, 10px) clamp(4px, 0.6vw, 8px);border: 2px solid ${p => (p.$spy ? '#e05252' : p.$eliminated ? '#5f5a52' : '#8e6531')};background: ${p => (p.$eliminated ? '#10141c' : '#141a26')};opacity: ${p => (p.$eliminated ? 0.82 : 1)};`;
const Portrait = styled.span<{ $sprite: ReturnType<typeof nightPortraitSprite> }>`display: block;width: 100%;aspect-ratio: 1;max-height: clamp(64px, 8vw, 118px);border: 1px solid #8e6531;background: #121f2f url("${p => spriteUrl(p.$sprite)}") center / 100% 100% no-repeat;image-rendering: pixelated;`;
const Tag = styled.span`color: #8f9aab;font-size: clamp(9px, 0.9vw, 11px);`;
const Name = styled.span`overflow: hidden;text-overflow: ellipsis;white-space: nowrap;font-size: clamp(11px, 1.1vw, 15px);color: #f2e7ce;`;
const Job = styled.span`margin-top: 2px;color: #6e7686;font-size: clamp(9px, 0.85vw, 11px);`;
const Words = styled.div`display: flex;flex-wrap: wrap;justify-content: center;gap: clamp(16px, 3vw, 48px);`;
const WordBox = styled.div`text-align: center;`;
const WordLabel = styled.div`color: #8f9aab;font-size: clamp(10px, 0.95vw, 13px);`;
const WordValue = styled.div<{ $spy: boolean }>`margin-top: 4px;color: ${p => (p.$spy ? '#f29a8e' : '#f7e8c4')};font-size: clamp(16px, 2.2vw, 30px);font-weight: 800;`;
const Note = styled.p`margin: clamp(6px, 1vw, 12px) 0 0;color: #8f9aab;font-size: clamp(10px, 0.95vw, 12px);line-height: 1.6;`;
const Section = styled.div`width: min(760px, 100%);margin-inline: auto;text-align: left;`;
const SectionTitle = styled.h3`margin: 0 0 4px;color: #e8b45e;font-size: clamp(10px, 1vw, 12px);`;
const Prose = styled.div`max-height: clamp(90px, 18vh, 180px);overflow: auto;padding: 8px 10px;border: 1px solid #8e653155;background: #0b0e1688;color: #e5d6b8;font-size: clamp(12px, 1.1vw, 14px);white-space: pre-wrap;`;
const Footer = styled.footer`display: flex;flex-direction: column;align-items: center;gap: 6px;margin-top: clamp(10px, 2vw, 22px);padding-bottom: 4px;button{cursor: pointer;width: min(360px, 90vw);min-height: 42px;border: 2px solid #5e4320;padding: 8px 16px;color: #2a1b10;background: linear-gradient(180deg, #ffd98a 0%, #e8b45e 46%, #c68e3c 100%);font: inherit;font-size: 13px;font-weight: 800;}small{color: #8f9aab;font-size: 11px;}`;

function winnerText(result: GameResult): string {
  if (result.winner === 'civilian') return '平民阵营';
  if (result.winner === 'undercover') return '卧底阵营';
  return '胜负未公开';
}

export interface GameOverDialogProps {
  result: GameResult;
  round: number;
  players?: PlayerActor[];
  onClose: () => void;
}

/**
 * Night-banquet finale modelled on the static `result.html` page. Only public
 * result data is rendered, including verified public reveal information.
 */
export function GameOverDialog({ result, round, players = [], onClose }: GameOverDialogProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const ordered = [...players].sort((left, right) => left.seatNumber - right.seatNumber);
  const appearances = assignBotAppearances(ordered.map((player) => player.actorId), NIGHT_PORTRAIT_VARIANTS);
  const knownWinner = result.winner === 'civilian' || result.winner === 'undercover';
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const panel = dialogRef.current?.closest('[data-panel-viewport]');
    const siblings = panel?.querySelectorAll<HTMLElement>('[data-game-content]') ?? [];
    closeRef.current?.focus();
    siblings.forEach((node) => node.setAttribute('inert', ''));
    return () => {
      siblings.forEach((node) => node.removeAttribute('inert'));
      if (previous?.isConnected && previous !== document.body) previous.focus();
      else panel?.querySelector<HTMLElement>('[data-result-trigger]')?.focus();
    };
  }, []);
  return <Backdrop data-region="game-over-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <Result ref={dialogRef} role="dialog" aria-modal="true" aria-label="游戏结束" onKeyDown={(event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
      if (event.key === 'Tab') {
        const controls = dialogRef.current?.querySelectorAll<HTMLElement>('button,[tabindex="0"]');
        if (!controls?.length) return;
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }}>
      <Header>
      <Kicker>第一章 · 夜宴 · 完结</Kicker>
      <Title>{result.title || '本局游戏结束'}</Title>
      <Sub>第 {round} 轮 · 宴散</Sub>
      <Winner $known={knownWinner}>{knownWinner ? `获胜方 · ${winnerText(result)}` : '公开结果未提供获胜方'}</Winner>
      </Header>
      <Body>
      {ordered.length ? <Cards data-region="result-cards">
        {ordered.map((player) => <Card key={player.actorId} $spy={result.reveal?.roles[player.seatNumber] === 'undercover'} $eliminated={player.eliminated === true || player.alive === false} data-result-player={player.actorId}>
          <Portrait aria-hidden="true" $sprite={nightPortraitSprite(appearances[player.actorId] ?? 0, player.eliminated === true || player.alive === false)} />
          <Tag>{player.eliminated === true || player.alive === false ? '已出局' : '在场'}</Tag>
          <Name title={player.displayName}>{player.seatNumber}号 · {player.displayName}</Name>
          <Job>{result.reveal?.roles[player.seatNumber] === 'civilian' ? '平民' : result.reveal?.roles[player.seatNumber] === 'undercover' ? '卧底' : '身份未公开'}</Job>
        </Card>)}
      </Cards> : <Note>公开结果未提供玩家名单。</Note>}
      <Words>
        <WordBox><WordLabel>平民词语</WordLabel><WordValue $spy={false}>{result.reveal?.civilianWord ?? '未公开'}</WordValue></WordBox>
        <WordBox><WordLabel>卧底词语</WordLabel><WordValue $spy>{result.reveal?.undercoverWord ?? '未公开'}</WordValue></WordBox>
      </Words>
      {!result.reveal && <Note>身份和词语未公开。</Note>}
      <Section>
        <SectionTitle>公开判词</SectionTitle>
        <Prose>{result.reason?.trim() || '公开结果未提供判词。'}</Prose>
      </Section>
      <Section>
        <SectionTitle>公开复盘</SectionTitle>
        <Prose tabIndex={0} aria-label="终局公开复盘">{result.summary?.trim() || '本局已结束。感谢参与这场推理，回到圆桌查看公开发言。'}</Prose>
      </Section>
      </Body>
      <Footer><button ref={closeRef} type="button" onClick={onClose}>回到圆桌</button><small>想再来一局？请在聊天中新建会话</small></Footer>
    </Result>
  </Backdrop>;
}
