import React, { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import styled from 'styled-components';
import {
  ApiRequestError,
  fetchNodeDetail,
  fetchPendingHumanNodes,
  fetchRunGraph,
  fetchSessionMessages,
  respondToHumanNode,
} from './api';
import { PanelParamsError, normalizePanelParams } from './contracts';
import { getSeatCoordinates, truncateBubbleText } from './layout';
import { eligibleVoteCandidates, normalizeUndercoverGameViewModel, serializeVoteContent } from './viewModel';
import type {
  ActorViewModel,
  PlayerState,
  StateMachineNodeDetailResponse,
  UndercoverGamePanelProps,
  UndercoverGamePanelParams,
  UndercoverGameViewModel,
  PlayerActor,
} from './types';

const Container = styled.section`
  box-sizing: border-box;
  min-height: 100%;
  width: 100%;
  overflow: auto;
  padding: 12px;
  color: #f8f4e8;
  background: #161827;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  image-rendering: pixelated;
`;
const Header = styled.header`
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  padding: 10px;
  border: 3px solid #504768;
  background: #26273c;
  box-shadow: 4px 4px 0 #0c0d16;
`;
const HeaderTitle = styled.div`min-width: 0;`;
const Eyebrow = styled.div`color: #f2b36c; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;`;
const Title = styled.h1`margin: 3px 0 0; font-size: 16px; line-height: 1.2; overflow-wrap: anywhere;`;
const Meta = styled.div`margin-top: 5px; color: #b9bdd1; font-size: 11px; line-height: 1.4;`;
const ActionButton = styled.button`
  cursor: pointer;
  border: 2px solid #f2b36c;
  padding: 6px 8px;
  color: #211b29;
  background: #f2b36c;
  font: inherit;
  font-size: 11px;
  font-weight: 700;
  &:disabled { cursor: wait; opacity: 0.55; }
`;
const ErrorBox = styled.div`margin: 10px 0; padding: 9px; border: 2px solid #e87979; color: #ffd6d6; background: #3d2334; font-size: 12px; line-height: 1.45;`;
const Notice = styled.div`margin: 8px 0; padding: 7px 9px; border-left: 3px solid #f2b36c; color: #f6dfb4; background: #322b3a; font-size: 11px; line-height: 1.4;`;
const Room = styled.div`
  position: relative;
  min-height: 560px;
  overflow: hidden;
  border: 4px solid #504768;
  background:
    linear-gradient(90deg, rgba(255,255,255,.035) 2px, transparent 2px) 0 0 / 24px 24px,
    linear-gradient(rgba(255,255,255,.035) 2px, transparent 2px) 0 0 / 24px 24px,
    #39324b;
  box-shadow: inset 0 0 0 4px #211f32, 6px 6px 0 #0c0d16;
  @media (max-width: 620px) {
    min-height: 620px;
  }
`;
const Window = styled.div`position: absolute; top: 5%; left: 7%; width: 25%; height: 17%; border: 5px solid #201c30; background: #5e7697; box-shadow: inset 0 0 0 5px #9db7c3;`;
const WindowCross = styled.div`position: absolute; inset: 42% 0 auto; height: 5px; background: #201c30; &::after { content: ''; position: absolute; left: 48%; top: -20px; width: 5px; height: 45px; background: #201c30; }`;
const Lamp = styled.div`position: absolute; top: 3%; left: 49%; width: 52px; height: 30px; border: 4px solid #201c30; background: #f2b36c; box-shadow: 0 12px 0 -4px #201c30;`;
const HostArea = styled.div`position: absolute; top: 8%; left: 50%; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; width: 40%; min-width: 190px; z-index: 3;`;
const Podium = styled.div`display: flex; align-items: center; gap: 8px; border: 4px solid #201c30; padding: 8px 12px; background: #b35d5d; box-shadow: 5px 5px 0 #201c30;`;
const HostSprite = styled.div`display: grid; place-items: center; width: 40px; height: 40px; border: 4px solid #201c30; color: #201c30; background: #f2d18a; font-size: 21px; font-weight: 900;`;
const HostCopy = styled.div`min-width: 0;`;
const HostName = styled.div`font-size: 12px; font-weight: 800; overflow-wrap: anywhere;`;
const HostStatus = styled.div`margin-top: 3px; color: #ffe6bd; font-size: 10px;`;
const HostBubble = styled.button`max-width: 230px; margin-top: 8px; border: 3px solid #201c30; padding: 7px 9px; color: #201c30; background: #f9edcf; font-size: 11px; line-height: 1.35; box-shadow: 3px 3px 0 #201c30;`;
const Table = styled.div`position: absolute; left: 50%; top: 59%; width: 62%; height: 33%; transform: translate(-50%, -50%); border: 8px solid #201c30; border-radius: 50%; background: #8b5260; box-shadow: inset 0 0 0 9px #bf7967, 8px 8px 0 #201c30;`;
const TableTop = styled.div`position: absolute; inset: 18% 18%; border: 5px solid #201c30; border-radius: 50%; background: #b67865;`;
const Seat = styled.button<{ $left: number; $top: number; $compactLeft: number; $compactTop: number; $compact?: boolean; $active?: boolean }>`
  position: absolute;
  left: ${({ $left, $compact, $compactLeft }) => ($compact ? `${$compactLeft}%` : `${$left}%`)};
  top: ${({ $top, $compact, $compactTop }) => ($compact ? `${$compactTop}%` : `${$top}%`)};
  transform: translate(-50%, -50%);
  z-index: 4;
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100px;
  border: 0;
  padding: 0;
  color: #fff8e8;
  background: transparent;
  font: inherit;
  cursor: pointer;
  ${({ $active }) => $active ? 'filter: drop-shadow(0 0 7px #f2b36c);' : ''}
  @media (max-width: 620px) { width: 92px; }
`;
const Sprite = styled.div<{ $state: PlayerState | 'host'; $human?: boolean }>`
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border: 4px solid #201c30;
  color: #201c30;
  background: ${({ $state }) => $state === 'eliminated' ? '#777789' : $state === 'error' ? '#e87979' : $state === 'active_speech' ? '#f2b36c' : '#80b6a1'};
  font-size: 22px;
  font-weight: 900;
  ${({ $human }) => $human ? 'outline: 3px dashed #fff1a8; outline-offset: 3px;' : ''}
`;
const SeatLabel = styled.span`max-width: 104px; margin-top: 4px; padding: 2px 4px; border: 2px solid #201c30; background: #26273c; font-size: 10px; line-height: 1.15; overflow-wrap: anywhere;`;
const StateMarker = styled.span`max-width: 104px; margin-top: 2px; color: #f6dfb4; font-size: 9px; line-height: 1.15;`;
const Bubble = styled.button<{ $left: number; $top: number; $compactLeft: number; $compactTop: number; $compact?: boolean; $pending?: boolean }>`
  position: absolute;
  left: ${({ $left, $compact, $compactLeft }) => ($compact ? `${$compactLeft}%` : `${$left}%`)};
  top: ${({ $top, $compact, $compactTop }) => ($compact ? `${$compactTop}%` : `${$top}%`)};
  transform: translate(-50%, -100%);
  z-index: 5;
  max-width: 150px;
  border: 3px solid #201c30;
  padding: 5px 7px;
  color: #201c30;
  background: ${({ $pending }) => $pending ? '#f2d18a' : '#f9edcf'};
  font: inherit;
  font-size: 10px;
  line-height: 1.3;
  text-align: left;
  box-shadow: 3px 3px 0 #201c30;
  cursor: pointer;
  overflow-wrap: anywhere;
  @media (max-width: 620px) { max-width: 116px; }
`;
const Footer = styled.div`display: flex; flex-wrap: wrap; gap: 7px; align-items: center; margin-top: 12px; color: #b9bdd1; font-size: 10px;`;
const Legend = styled.span`padding: 3px 5px; border: 1px solid #504768; background: #26273c;`;
const ModalBackdrop = styled.div`position: fixed; inset: 0; z-index: 20; display: grid; place-items: center; padding: 12px; background: rgba(8, 8, 17, .78);`;
const Modal = styled.div`width: min(560px, 100%); max-height: 90vh; overflow: auto; border: 4px solid #f2b36c; padding: 13px; color: #f8f4e8; background: #26273c; box-shadow: 7px 7px 0 #0c0d16;`;
const ModalHeader = styled.div`display: flex; align-items: flex-start; justify-content: space-between; gap: 8px;`;
const ModalTitle = styled.h2`margin: 0; font-size: 15px;`;
const CloseButton = styled.button`border: 2px solid #aab0c8; padding: 3px 6px; color: #f8f4e8; background: transparent; font: inherit; cursor: pointer;`;
const DetailGrid = styled.dl`display: grid; grid-template-columns: 110px 1fr; gap: 6px 10px; margin: 13px 0; font-size: 11px; dt { color: #f2b36c; } dd { margin: 0; overflow-wrap: anywhere; }`;
const History = styled.ol`margin: 8px 0 14px; padding-left: 20px; font-size: 11px; line-height: 1.4; li { margin-bottom: 6px; }`;
const Form = styled.form`display: grid; gap: 8px; margin-top: 12px; padding-top: 10px; border-top: 2px solid #504768;`;
const TextArea = styled.textarea`box-sizing: border-box; width: 100%; min-height: 94px; resize: vertical; border: 2px solid #aab0c8; padding: 8px; color: #201c30; background: #f9edcf; font: inherit; font-size: 12px;`;
const CandidateList = styled.div`display: grid; gap: 6px;`;
const Candidate = styled.label<{ $selected?: boolean }>`display: flex; gap: 7px; align-items: center; border: 2px solid ${({ $selected }) => $selected ? '#f2b36c' : '#504768'}; padding: 7px; background: ${({ $selected }) => $selected ? '#4b3d46' : '#202135'}; font-size: 11px; cursor: pointer;`;
const Small = styled.div`color: #b9bdd1; font-size: 10px; line-height: 1.4;`;

const STATE_LABELS: Record<PlayerState, string> = {
  waiting: '◌ 等待',
  active_speech: '▶ 发言中',
  completed_speech: '✓ 已发言',
  waiting_for_vote: '◇ 等待投票',
  voted: '◆ 已投票',
  eliminated: '✕ 已淘汰',
  retrying: '↻ 重试中',
  error: '! 出错',
};

function formatDeadline(deadlineAt?: number): string {
  if (!deadlineAt) return '无截止时间';
  const remaining = deadlineAt - Date.now();
  if (remaining <= 0) return '已到期';
  const seconds = Math.ceil(remaining / 1000);
  return `剩余 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function byteLength(value: string): number {
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(value).length;
  return unescape(encodeURIComponent(value)).length;
}

function actorInitial(actor: ActorViewModel): string {
  return Array.from(actor.actor.displayName)[0] || '?';
}

function nodeStatus(node: ActorViewModel['node']): string {
  if (!node) return '未绑定节点';
  return `${node.status ?? 'unknown'}${node.attempt ? ` / attempt ${node.attempt}` : ''}`;
}

function requestErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败。';
}

function InvalidPanel({ error, className, style }: { error: string; className?: string; style?: CSSProperties }) {
  return <Container className={className} style={style}><ErrorBox role="alert"><strong>Undercover panel 参数无效</strong><br />{error}<br /><Small>请修正 runId、groupId、sessionId、seatOrder 与公开参与者参数后重新打开本阶段面板。</Small></ErrorBox></Container>;
}

function GamePanel({ params, className, style, onInteraction }: { params: UndercoverGamePanelParams; className?: string; style?: CSSProperties; onInteraction?: UndercoverGamePanelProps['onInteraction'] }) {
  const [graph, setGraph] = useState<Awaited<ReturnType<typeof fetchRunGraph>> | undefined>();
  const [pendingNodes, setPendingNodes] = useState<Awaited<ReturnType<typeof fetchPendingHumanNodes>>>([]);
  const [messages, setMessages] = useState<Awaited<ReturnType<typeof fetchSessionMessages>>>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedActorId, setSelectedActorId] = useState<string | null>(null);
  const [nodeDetail, setNodeDetail] = useState<StateMachineNodeDetailResponse | null>(null);
  const [nodeDetailLoading, setNodeDetailLoading] = useState(false);
  const [nodeDetailError, setNodeDetailError] = useState<string | null>(null);
  const [speechText, setSpeechText] = useState('');
  const [voteTarget, setVoteTarget] = useState('');
  const [voteConfirmed, setVoteConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [staleAction, setStaleAction] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const pollingRequestRef = useRef<AbortController | null>(null);
  const autoRefreshEnabledRef = useRef(params.autoRefresh);
  const detailRequestRef = useRef<AbortController | null>(null);
  const snapshotRef = useRef<{ runId: string; viewModel: UndercoverGameViewModel } | null>(null);

  const refresh = useCallback(async (initial = false, fromPolling = false) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    if (fromPolling) pollingRequestRef.current = controller;
    setError(null);
    if (initial) setLoading(true); else setRefreshing(true);
    try {
      const [nextGraph, nextPending, nextMessages] = await Promise.all([
        fetchRunGraph(params.apiBaseUrl ?? '', params.runId, controller.signal),
        fetchPendingHumanNodes(params.apiBaseUrl ?? '', params.runId, controller.signal),
        fetchSessionMessages(params.apiBaseUrl ?? '', params.sessionId, controller.signal),
      ]);
      if (requestRef.current !== controller) return;
      snapshotRef.current = {
        runId: params.runId,
        viewModel: normalizeUndercoverGameViewModel(params, nextGraph, nextPending, nextMessages),
      };
      setGraph(nextGraph);
      setPendingNodes(nextPending);
      setMessages(nextMessages);
      setStaleAction(false);
    } catch (requestError) {
      if (requestError instanceof Error && requestError.name === 'AbortError') return;
      if (requestRef.current === controller) setError(requestErrorMessage(requestError));
    } finally {
      if (pollingRequestRef.current === controller) pollingRequestRef.current = null;
      if (requestRef.current === controller) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [params, params.apiBaseUrl, params.runId, params.sessionId]);

  useEffect(() => {
    void refresh(true);
    return () => {
      requestRef.current?.abort();
      detailRequestRef.current?.abort();
    };
  }, [refresh]);

  const viewModel = useMemo(() => normalizeUndercoverGameViewModel(params, graph, pendingNodes, messages), [params, graph, pendingNodes, messages]);

  useEffect(() => {
    const autoRefreshWasDisabled = autoRefreshEnabledRef.current && !params.autoRefresh;
    autoRefreshEnabledRef.current = params.autoRefresh;
    if (!params.autoRefresh || viewModel.terminal) {
      pollingRequestRef.current?.abort();
      if (viewModel.terminal || autoRefreshWasDisabled) requestRef.current?.abort();
      return undefined;
    }
    const timer = window.setTimeout(() => { void refresh(false, true); }, params.pollingInterval);
    return () => window.clearTimeout(timer);
  }, [params.autoRefresh, params.pollingInterval, refresh, viewModel.terminal]);

  const fallbackSnapshot = snapshotRef.current?.runId === params.runId ? snapshotRef.current.viewModel : null;
  const displaySnapshot = error ? fallbackSnapshot ?? viewModel : viewModel;
  const selectedActor = useMemo(() => displaySnapshot.actors.find((actor) => actor.actor.actorId === selectedActorId), [displaySnapshot.actors, selectedActorId]);
  const isDetailOpen = Boolean(selectedActor);
  const selectedNodeId = selectedActor?.node?.node_id;
  const currentViewerAction = displaySnapshot.pendingHumanActorId === params.currentViewerActorId && displaySnapshot.pendingHumanNode;
  const voteMode = params.phase.toLowerCase().includes('vot');
  const candidates = useMemo(() => eligibleVoteCandidates(params.voteCandidates), [params.voteCandidates]);
  const compactLayout = typeof window !== 'undefined' && window.innerWidth <= 620;
  const coordinates = useMemo(() => getSeatCoordinates(params.seatOrder), [params.seatOrder]);

  useEffect(() => {
    if (!isDetailOpen || !selectedNodeId) return;
    detailRequestRef.current?.abort();
    const controller = new AbortController();
    detailRequestRef.current = controller;
    setNodeDetailLoading(true);
    setNodeDetailError(null);
    void fetchNodeDetail(params.apiBaseUrl ?? '', params.runId, selectedNodeId, controller.signal)
      .then((detail) => {
        if (detailRequestRef.current === controller) setNodeDetail(detail);
      })
      .catch((requestError) => {
        if (requestError instanceof Error && requestError.name === 'AbortError') return;
        if (detailRequestRef.current === controller) setNodeDetailError(requestErrorMessage(requestError));
      })
      .finally(() => {
        if (detailRequestRef.current === controller) setNodeDetailLoading(false);
      });
    return () => controller.abort();
  }, [isDetailOpen, params.apiBaseUrl, params.runId, selectedNodeId]);

  const selectActor = useCallback((actorId: string, interactionType: 'select-actor' | 'select-bubble') => {
    setSelectedActorId(actorId);
    onInteraction?.({ type: interactionType, actorId, runId: params.runId });
  }, [onInteraction, params.runId]);

  const submitHumanInput = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!currentViewerAction || submitting || staleAction) return;
    let content = '';
    if (voteMode) {
      if (!voteConfirmed || !candidates.some((candidate) => candidate.actorId === voteTarget)) {
        setActionError('请选择一个仍然符合公开资格的候选人，并确认投票。');
        return;
      }
      content = serializeVoteContent(voteTarget);
    } else {
      content = speechText.trim();
      if (!content) { setActionError('发言内容不能为空。'); return; }
      if (byteLength(content) > (params.maxResponseBytes ?? 64 * 1024)) {
        setActionError(`发言不能超过 ${params.maxResponseBytes ?? 64 * 1024} UTF-8 bytes。`);
        return;
      }
    }
    setSubmitting(true);
    setActionError(null);
    try {
      await respondToHumanNode(params.apiBaseUrl ?? '', params.runId, currentViewerAction.node_id, content);
      setSpeechText('');
      setVoteTarget('');
      setVoteConfirmed(false);
      setStaleAction(false);
      onInteraction?.({ type: 'submit-human-input', actorId: params.currentViewerActorId, nodeId: currentViewerAction.node_id, runId: params.runId });
      await refresh(false);
    } catch (requestError) {
      if (requestError instanceof Error && requestError.name === 'AbortError') return;
      const message = requestErrorMessage(requestError);
      if (requestError instanceof ApiRequestError && requestError.conflict) {
        setStaleAction(true);
        setActionError(`此操作已失效或与当前运行冲突：${message} 请刷新后查看最新阶段状态。`);
        void refresh(false);
      } else {
        setActionError(message);
      }
    } finally {
      setSubmitting(false);
    }
  }, [candidates, currentViewerAction, onInteraction, params.apiBaseUrl, params.currentViewerActorId, params.maxResponseBytes, params.runId, refresh, speechText, staleAction, submitting, voteConfirmed, voteMode, voteTarget]);

  if (loading && !fallbackSnapshot) {
    return <Container className={className} style={style}><Notice>正在加载本阶段公开状态…</Notice></Container>;
  }

  const statusText = displaySnapshot.terminal ? `阶段已${displaySnapshot.status === 'completed' ? '完成' : displaySnapshot.status === 'aborted' ? '中止' : '结束'}` : (refreshing ? '刷新中…' : '进行中');
  const host = displaySnapshot.actors.find((actor) => actor.kind === 'host');
  const playerActors = displaySnapshot.actors.filter((actor) => actor.kind === 'player');

  return (
    <Container className={className} style={style}>
      <Header>
        <HeaderTitle>
          <Eyebrow>UNDERCOVER / PUBLIC ROOM</Eyebrow>
          <Title>谁是卧底 · {displaySnapshot.phase}</Title>
          <Meta>第 {displaySnapshot.round} 轮 · {statusText} · {params.gameSessionId ? `game ${params.gameSessionId}` : 'phase run'}<br />{params.display?.showTimer !== false ? formatDeadline(params.deadlineAt) : '计时器隐藏'}</Meta>
        </HeaderTitle>
        <ActionButton type="button" onClick={() => { onInteraction?.({ type: 'refresh', runId: params.runId }); void refresh(false); }} disabled={refreshing}>↻ 刷新</ActionButton>
      </Header>

      {error && <ErrorBox role="alert">公开状态刷新失败：{error}<br /><Small>保留最近一次成功快照；可以手动重试。</Small><br /><ActionButton type="button" onClick={() => { onInteraction?.({ type: 'retry', runId: params.runId }); void refresh(false); }}>重试</ActionButton></ErrorBox>}
      {staleAction && <Notice role="status">当前 HumanInput 已被服务器接受、过期或发生冲突；操作已禁用，请查看刷新后的阶段状态。</Notice>}

      <Room aria-label="谁是卧底像素房间">
        <Window><WindowCross /></Window>
        <Lamp />
        {host && (
          <HostArea>
            <Podium>
              <HostSprite aria-hidden="true">♟</HostSprite>
              <HostCopy><HostName>{host.actor.displayName}</HostName><HostStatus>主持 Bot · {statusText}</HostStatus></HostCopy>
            </Podium>
            {params.display?.showHostOutput !== false && host.latestOutput && (
              <HostBubble type="button" onClick={() => selectActor(host.actor.actorId, 'select-bubble')}>
                {truncateBubbleText(host.latestOutput.text).text}
                {truncateBubbleText(host.latestOutput.text).truncated ? ' 点击查看全部' : ''}
              </HostBubble>
            )}
          </HostArea>
        )}
        <Table aria-hidden="true"><TableTop /></Table>
        {playerActors.map((actor, index) => {
          const coordinate = coordinates[index];
          const player = actor.actor as PlayerActor;
          const bubble = actor.latestOutput;
          const compactLeft = coordinate.compactLeft;
          const compactTop = coordinate.compactTop;
          return <React.Fragment key={player.actorId}>
            {bubble && <Bubble type="button" $left={coordinate.left} $top={coordinate.top - 6} $compactLeft={compactLeft} $compactTop={compactTop - 7} $compact={compactLayout} $pending={bubble.pending} onClick={() => selectActor(player.actorId, 'select-bubble')} aria-label={`${player.displayName} 的公开输出`}>
              {bubble.pending ? '⌛ ' : ''}{truncateBubbleText(bubble.text).text}{truncateBubbleText(bubble.text).truncated ? ' …' : ''}
            </Bubble>}
            <Seat type="button" $left={coordinate.left} $top={coordinate.top} $compactLeft={compactLeft} $compactTop={compactTop} $compact={compactLayout} $active={actor.state === 'active_speech' || actor.actor.actorId === params.currentViewerActorId} onClick={() => selectActor(player.actorId, 'select-actor')} aria-label={`打开 ${player.displayName} 详情`}>
              <Sprite $state={actor.state} $human={player.isHuman}>{actorInitial(actor)}</Sprite>
              <SeatLabel>{player.displayName}</SeatLabel>
              <StateMarker>{STATE_LABELS[actor.state as PlayerState] ?? `• ${actor.state}`}{player.isHuman && actor.actor.actorId === params.currentViewerActorId ? ' · 你' : ''}</StateMarker>
            </Seat>
          </React.Fragment>;
        })}
      </Room>

      <Footer>
        <Legend>▶ 发言中</Legend><Legend>◇ 待操作</Legend><Legend>↻ 重试</Legend><Legend>✕ 淘汰</Legend><Legend>虚线 = 当前玩家</Legend>
        <span>节点输出仅按显式 run/node/attempt 映射展示。</span>
      </Footer>

      {isDetailOpen && selectedActor && (
        <ModalBackdrop role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedActorId(null); }}>
          <Modal role="dialog" aria-modal="true" aria-label={`${selectedActor.actor.displayName} 详情`}>
            <ModalHeader><div><Eyebrow>{selectedActor.kind === 'host' ? 'HOST DETAIL' : 'PLAYER DETAIL'}</Eyebrow><ModalTitle>{selectedActor.actor.displayName}</ModalTitle></div><CloseButton type="button" onClick={() => setSelectedActorId(null)}>关闭</CloseButton></ModalHeader>
            <DetailGrid>
              <dt>当前阶段</dt><dd>{displaySnapshot.phase} · 第 {displaySnapshot.round} 轮</dd>
              <dt>公开状态</dt><dd>{selectedActor.kind === 'host' ? '主持 Bot' : STATE_LABELS[selectedActor.state as PlayerState] ?? selectedActor.state}</dd>
              <dt>关联节点</dt><dd>{selectedNodeId ?? '无'}</dd>
              <dt>运行状态</dt><dd>{nodeStatus(selectedActor.node)}</dd>
              {selectedActor.kind === 'player' && <><dt>公开身份</dt><dd>{(selectedActor.actor as PlayerActor).isHuman ? 'HumanInput 可用（若当前 viewer 获授权）' : 'Bot 玩家'}</dd></>}
            </DetailGrid>
            {selectedActor.latestOutput && <Notice>最近公开输出：{selectedActor.latestOutput.text}</Notice>}
            <Small>公开输出历史（{selectedActor.outputHistory.length}）</Small>
            <History>{selectedActor.outputHistory.length ? selectedActor.outputHistory.slice().reverse().map((event) => <li key={event.identity}>{event.pending ? '⌛ ' : ''}{event.text}<br /><Small>node {event.nodeId} · attempt {event.attempt}{event.timestamp ? ` · ${new Date(event.timestamp).toLocaleTimeString()}` : ''}</Small></li>) : <li>暂无公开输出。</li>}</History>
            {nodeDetailLoading && <Notice>正在加载选中节点详情…</Notice>}
            {nodeDetailError && <ErrorBox>节点详情加载失败：{nodeDetailError}<br /><Small>场景与最近公开摘要仍然保留。</Small></ErrorBox>}
            {nodeDetail && selectedNodeId === nodeDetail.node.node_id && <DetailGrid><dt>节点公开摘要</dt><dd>{selectedActor.latestOutput?.text ?? '未提供公开输出'}</dd><dt>开始时间</dt><dd>{nodeDetail.node.started_at ? new Date(nodeDetail.node.started_at).toLocaleString() : '未提供'}</dd><dt>完成时间</dt><dd>{nodeDetail.node.completed_at ? new Date(nodeDetail.node.completed_at).toLocaleString() : '未提供'}</dd><dt>尝试次数</dt><dd>{nodeDetail.node.attempt ?? '未提供'}</dd></DetailGrid>}

            {currentViewerAction && selectedActor.actor.actorId === params.currentViewerActorId && selectedActor.kind === 'player' && !displaySnapshot.terminal && (
              <Form onSubmit={submitHumanInput}>
                <strong>{voteMode ? '你的投票' : '你的发言'}</strong>
                <Small>{params.phase} 阶段 · 当前 HumanInput：{currentViewerAction.node_id}</Small>
                {voteMode ? <>
                  <CandidateList>{candidates.length ? candidates.map((candidate) => <Candidate key={candidate.actorId} $selected={voteTarget === candidate.actorId}><input type="radio" name="vote-target" value={candidate.actorId} checked={voteTarget === candidate.actorId} onChange={() => { setVoteTarget(candidate.actorId); setVoteConfirmed(false); }} disabled={submitting || staleAction} />{candidate.displayName}</Candidate>) : <Small>没有显式提供的可投候选人。</Small>}</CandidateList>
                  <Candidate $selected={voteConfirmed}><input type="checkbox" checked={voteConfirmed} onChange={(event) => setVoteConfirmed(event.target.checked)} disabled={submitting || staleAction || !voteTarget} />我确认这是我要提交的公开候选人</Candidate>
                </> : <><TextArea value={speechText} onChange={(event) => { setSpeechText(event.target.value); setActionError(null); }} placeholder="输入公开发言…" disabled={submitting || staleAction} /><Small>{byteLength(speechText)} / {params.maxResponseBytes ?? 64 * 1024} UTF-8 bytes</Small></>}
                {actionError && <ErrorBox>{actionError}</ErrorBox>}
                <ActionButton type="submit" disabled={submitting || staleAction || (voteMode && !candidates.length)}>{submitting ? '提交中…' : voteMode ? '确认并投票' : '提交发言'}</ActionButton>
              </Form>
            )}
            {displaySnapshot.pendingHumanActorId && displaySnapshot.pendingHumanActorId !== params.currentViewerActorId && <Notice>当前待处理输入属于另一位公开参与者；本面板不会替其提交操作。</Notice>}
          </Modal>
        </ModalBackdrop>
      )}
    </Container>
  );
}

export default function UndercoverGamePanel(props: UndercoverGamePanelProps) {
  const normalized = useMemo(() => {
    try { return { params: normalizePanelParams(props), error: null }; }
    catch (error) { return { params: null, error: error instanceof PanelParamsError || error instanceof Error ? error.message : 'Invalid panel parameters.' }; }
  }, [props]);
  if (normalized.error || !normalized.params) return <InvalidPanel error={normalized.error ?? 'Invalid panel parameters.'} className={props.className} style={props.style} />;
  return <GamePanel params={normalized.params} className={props.className} style={props.style} onInteraction={props.onInteraction} />;
}
