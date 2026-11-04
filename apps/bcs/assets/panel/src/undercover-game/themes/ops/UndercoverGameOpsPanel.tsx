import React, { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import styled from 'styled-components';
import { ApiRequestError, fetchNodeDetail, fetchPendingHumanNodes, fetchRunGraph, respondToHumanNode } from '../../api';
import { normalizePanelParams } from '../../contracts';
import { parsePrivateActionContext, serializeVoteAbstain, serializeVoteTarget, unicodeLength, validateSpeech } from '../../actionContext';
import { normalizeCurrentRoundUpstream, publicEventFromNodeDetail, completedMappedNodes } from '../../currentRound';
import { eligibleVoteCandidates, normalizeUndercoverGameViewModel } from '../../viewModel';
import { publicGameResultView, fetchPublicGameResult, type PublicGameResult } from '../../publicResult';
import { gameResult } from '../../gameResult';
import type {
  PanelAction,
  PlayerState,
  PublicOutputEvent,
  StateMachineNodeDetailResponse,
  UndercoverGamePanelParams,
  UndercoverGamePanelProps,
  UndercoverGameViewModel,
} from '../../types';
import { OPS_PALETTE } from './palette';
import type { FlavorPalette } from './themeTypes';
import { buildRunnerGraph, type RunnerGraphLayout } from './workflowLayout';

/* ------------------------------------------------------------------ *
 *  Ops-theme primitives (terminal / mission-command aesthetic)
 *  Colors are exposed as CSS custom props set on the root shell so every
 *  child component reads the active flavor palette without a ThemeProvider.
 * ------------------------------------------------------------------ */
interface ThemeTone {
  $left?: string;
}


const RootShell = styled.section<{ $palette: FlavorPalette }>`
  --pad: 12px;
  --bg: ${(p) => p.$palette.bg};
  --panel: ${(p) => p.$palette.panel};
  --panel-alt: ${(p) => p.$palette.panelAlt};
  --bd: ${(p) => p.$palette.border};
  --bd-strong: ${(p) => p.$palette.borderStrong};
  --text: ${(p) => p.$palette.text};
  --text-inv: ${(p) => p.$palette.textInverse};
  --ops-muted-text: ${(p) => p.$palette.muted};
  --accent: ${(p) => p.$palette.accent};
  --accent-soft: ${(p) => p.$palette.accentSoft};
  --accent-text: ${(p) => p.$palette.accentText};
  --good: ${(p) => p.$palette.good};
  --good-soft: ${(p) => p.$palette.goodSoft};
  --bad: ${(p) => p.$palette.bad};
  --warn: ${(p) => p.$palette.warn};
  --run: ${(p) => p.$palette.running};
  --pending: ${(p) => p.$palette.pending};
  --done: ${(p) => p.$palette.done};
  --human: ${(p) => p.$palette.human};
  position: relative; isolation: isolate; box-sizing: border-box; display: flex; flex-direction: column;
  height: 100%; min-height: 0; width: 100%; overflow: hidden;
  background: var(--bg); color: var(--text);
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 12px; line-height: 1.5;
  *{box-sizing:border-box} button,textarea,input{font-family:inherit}
  ::-webkit-scrollbar{width:4px;height:4px} ::-webkit-scrollbar-thumb{background:var(--bd-strong);border-radius:8px}
  @media (prefers-reduced-motion: reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
`;

const TopBar = styled.header`
  flex: 0 0 auto; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: var(--pad) var(--pad) 8px; border-bottom: 1px solid var(--bd);
`;

const Brand = styled.div``;
const BrandEyebrow = styled.div`color:var(--ops-muted-text);font-size:9px;letter-spacing:.22em;opacity:.9`;
const BrandTitle = styled.h1`margin:1px 0 0;font-size:17px;font-weight:700;letter-spacing:.05em;color:var(--accent);`;

const StatusStrip = styled.div`
  flex:1 1 auto; min-width: 140px; display:flex; flex-wrap:wrap; gap:4px 10px; justify-content:flex-end;
  font-size:11px; color:var(--ops-muted-text);
  > span{white-space:nowrap}
  > .dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle}
`;

const Scene = styled.div`
  flex: 1 1 auto; min-height: 0; overflow: auto; overscroll-behavior: contain;
  padding: var(--pad) var(--pad) 4px; display: flex; flex-direction: column; gap: 10px;
`;

const toneColor = (tone: string | undefined, fallback: string) => {
  switch (tone) {
    case 'good': return 'var(--good)';
    case 'warn': return 'var(--warn)';
    case 'bad': return 'var(--bad)';
    case 'running': return 'var(--run)';
    case 'accent': return 'var(--accent)';
    default: return fallback;
  }
};

const Notice = styled.div<ThemeTone>`
  padding: 8px 10px; border: 1px solid var(--bd);
  border-left: 3px solid ${(p) => toneColor(p.$left, 'var(--run)')};
  background: var(--panel); color: var(--ops-muted-text);
  font-size: 11px; line-height: 1.6; overflow-wrap: anywhere;
`;

const Panel = styled.div<ThemeTone>`
  flex-shrink: 0;
  border: 1px solid var(--bd);
  border-left: 3px solid ${(p) => toneColor(p.$left, 'var(--bd)')};
  background: var(--panel); border-radius: 6px;
  padding: var(--pad); overflow: hidden;
`;

const PanelTitle = styled.div`
  display:flex;align-items:center;gap:8px;font-size:11px;font-weight:700;letter-spacing:.06em;color:var(--ops-muted-text);
  margin-bottom:8px; text-transform:uppercase;
  &::before{content:'';width:6px;height:6px;background:var(--run)}
`;

const SectLabel = styled.div`
  font-size:10px; letter-spacing:.14em; color:var(--ops-muted-text);
  margin-bottom:6px; text-transform:uppercase;
`;

const MonoBtn = styled.button`
  flex-shrink:0; cursor:pointer; border:1px solid var(--bd-strong);
  border-radius:5px; min-height:30px; padding:4px 10px; background:transparent; color:var(--text);
  font-size:11px; &:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
  &:disabled{opacity:.45;cursor:not-allowed}
  &[data-primary]{background:var(--accent);color:var(--text-inv);border-color:transparent;font-weight:700}
  &:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
`;

const TextBox = styled.textarea`
  width:100%; min-height:76px; resize:vertical; border:1px solid var(--bd-strong);
  border-radius:6px; padding:9px 10px; background:var(--panel-alt);
  color:var(--text); font-size:12px; line-height:1.7;
  &::placeholder{color:var(--ops-muted-text)}
`;

const WordChip = styled.div`
  display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 10px;
  border:1px dashed var(--accent);color:var(--accent);
  background:var(--accent-soft); font-weight:700;
  > .reveal{font-weight:400;font-size:11px}
`;

const CandidateGrid = styled.div`
  display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:6px;
`;

const Candidate = styled.button<{ $selected: boolean }>`
  display:flex; gap:7px; align-items:center; justify-content:flex-start; text-align:left; min-height:34px;
  border:1px solid ${(p) => (p.$selected ? 'var(--accent)' : 'var(--bd-strong)')};
  background:${(p) => (p.$selected ? 'var(--accent-soft)' : 'transparent')};
  color:${(p) => (p.$selected ? 'var(--accent)' : 'var(--text)')};
  border-radius:5px; padding:6px 9px; font-size:12px; cursor:pointer;
  &[data-abstain]{color:var(--ops-muted-text);border-style:dashed}
  &:hover:not(:disabled){border-color:var(--accent)}
  &:disabled{opacity:.45;cursor:not-allowed}
`;

const ConfirmRow = styled.label`
  display:flex; align-items:center; gap:8px; margin-top:8px; font-size:11px; color:var(--ops-muted-text); cursor:pointer;
  input{accent-color:var(--accent)}
`;

const Footer = styled.footer`
  flex:0 0 auto; display:flex; flex-wrap:wrap; align-items:center; gap:8px;
  padding: 8px var(--pad) var(--pad); border-top:1px solid var(--bd);
`;

/* ------------------------------------------------------------------ *
 *  Workflow graph card
 * ------------------------------------------------------------------ */
const statusColor = (status: string, c: FlavorPalette): string => {
  const s = (status ?? '').toLowerCase();
  if (s === 'running' || s === 'ready') return c.running;
  if (s === 'completed' || s === 'done') return c.done;
  if (s === 'failed' || s === 'aborted') return c.bad;
  if (s === 'retry_scheduled') return c.warn;
  return c.pending;
};
const statusSoft = (status: string, c: FlavorPalette): string => {
  const s = (status ?? '').toLowerCase();
  if (s === 'running' || s === 'ready') return c.runningSoft;
  if (s === 'completed' || s === 'done') return c.doneSoft;
  if (s === 'failed' || s === 'aborted') return c.badSoft;
  if (s === 'retry_scheduled') return c.warnSoft;
  return c.pendingSoft;
};
const statusLabel = (status: string): string => {
  const s = (status ?? '').toLowerCase();
  if (s === 'running') return '运行中';
  if (s === 'ready') return '就绪';
  if (s === 'completed') return '完成';
  if (s === 'failed') return '失败';
  if (s === 'aborted') return '中止';
  if (s === 'retry_scheduled') return '重试';
  if (s === 'skipped') return '跳过';
  return '等待';
};

function WorkflowGraph({ layout, palette, compact }: { layout: RunnerGraphLayout; palette: FlavorPalette; compact?: boolean }) {
  const cellW = compact ? 96 : 118;
  const cellH = compact ? 30 : 34;
  const gapX = compact ? 20 : 30;
  const gapY = compact ? 8 : 10;
  const padX = 6;
  // Map node id -> [column index, row index]
  const pos = new Map<string, [number, number]>();
  layout.columns.forEach((col, cx) => col.nodes.forEach((node, ny) => pos.set(node.id, [cx, ny])));
  const totalW = Math.max(1, layout.columns.length) * cellW + Math.max(0, layout.columns.length - 1) * gapX;
  const maxRows = layout.columns.reduce((m, c) => Math.max(m, c.nodes.length), 1);
  const totalH = maxRows * cellH + Math.max(0, maxRows - 1) * gapY;

  return (
    <div data-region="ops-workflow" role="group" aria-label="实际运行的 workflow">
      <div style={{ position: 'relative', overflowX: 'auto', padding: '2px 2px 6px' }}>
        {/* edges */}
        <svg
          style={{ position: 'absolute', left: 0, top: 0, width: totalW, height: totalH, pointerEvents: 'none', overflow: 'visible' }}
          aria-hidden="true"
        >
          {layout.edges.map((edge, i) => {
            const s = pos.get(edge.source.id);
            const t = pos.get(edge.target.id);
            if (!s || !t) return null;
            const x1 = s[0] * (cellW + gapX) + cellW;
            const y1 = s[1] * (cellH + gapY) + cellH / 2;
            const x2 = t[0] * (cellW + gapX);
            const y2 = t[1] * (cellH + gapY) + cellH / 2;
            const midX = (x1 + x2) / 2;
            return (
              <g key={`e${i}`}>
                <line x1={x1} y1={y1} x2={midX} y2={y1} stroke={palette.borderStrong} strokeWidth="1" />
                <line x1={midX} y1={y1} x2={midX} y2={y2} stroke={palette.borderStrong} strokeWidth="1" />
                <line x1={midX} y1={y2} x2={x2} y2={y2} stroke={palette.borderStrong} strokeWidth="1" />
                <polygon
                  points={`${x2 - 2},${y2 - 3.5} ${x2 - 2},${y2 + 3.5} ${x2 + 3},${y2}`}
                  fill={edge.target.status === 'completed' ? palette.done : edge.target.status === 'running' ? palette.running : palette.pending}
                />
              </g>
            );
          })}
        </svg>
        {/* nodes */}
        <div style={{ position: 'relative', width: totalW, height: totalH }}>
          {layout.columns.map((col, cx) => col.nodes.map((node, ny) => {
            const left = cx * (cellW + gapX);
            const top = ny * (cellH + gapY);
            const color = statusColor(node.status, palette);
            return (
              <div
                key={node.id}
                data-workflow-node={node.id}
                data-status={node.status}
                data-kind={node.kind}
                role="listitem"
                aria-label={`${node.label} · ${statusLabel(node.status)}`}
                style={{
                  position: 'absolute', left, top,
                  width: cellW, height: cellH,
                  border: `1px solid ${color}`, borderRadius: 4,
                  background: statusSoft(node.status, palette),
                  color: palette.text,
                  fontSize: compact ? 9.5 : 10.5,
                  padding: '3px 5px',
                  overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
                  display: 'flex', flexDirection: 'column', justifyContent: 'center',
                }}
              >
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', fontWeight: 600 }}>{node.label}</span>
                <span style={{ color, fontSize: compact ? 8.5 : 9.5, letterSpacing: '.04em' }}>
                  {node.actorId ? (node.kind === 'host' ? '◈ 主持人' : node.kind === 'human' ? '◈ 你' : '▣ Bot') : node.kind === 'entry' ? '▶ 入口' : '· 节点'}
                  {' '}· {statusLabel(node.status)}
                </span>
              </div>
            );
          }))}
        </div>
      </div>
      {/* legend */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 14px', marginTop: 8, fontSize: 10 }}>
        {([
          ['运行中', palette.running], ['完成', palette.done],
          ['失败', palette.bad], ['等待', palette.pending],
        ] as const).map(([label, color]) => (
          <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 4, color: palette.muted }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: color, display: 'inline-block' }} />
            {label}
          </span>
        ))}
        <span style={{ color: palette.muted }}>—— 计票/汇总为终列末节点</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 *  Roster / player state table
 * ------------------------------------------------------------------ */
const PLAYER_STATE: Record<string, string> = {
  waiting: '等待',
  action_required: '待你行动',
  active_speech: '发言中',
  completed_speech: '已发言',
  waiting_for_vote: '等待投票',
  voted: '已投票',
  eliminated: '出局',
  retrying: '重试中',
  error: '异常',
};

function Roster({ model, palette, currentViewer }: { model: UndercoverGameViewModel; palette: FlavorPalette; currentViewer?: string }) {
  const players = model.actors.filter((a) => a.kind === 'player');
  return (
    <Panel>
      <PanelTitle>玩家状态</PanelTitle>
      <div role="grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 4 }}>
        {players.map((a) => {
          const st = a.state as PlayerState | 'host';
          const isSelf = a.actor.actorId === currentViewer;
          const elim = st === 'eliminated';
          return (
            <div
              key={a.actor.actorId}
              data-actor-id={a.actor.actorId}
              data-state={st}
              role="gridcell"
              style={{
                border: `1px solid ${isSelf ? palette.human : palette.border}`,
                borderLeft: `3px solid ${elim ? palette.bad : st === 'action_required' ? palette.human : st === 'active_speech' ? palette.running : st === 'voted' || st === 'completed_speech' ? palette.done : palette.pending}`,
                background: elim ? palette.badSoft : isSelf ? palette.accentSoft : 'transparent',
                padding: '5px 8px', borderRadius: 4,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
                <span style={{ fontWeight: 700, textDecoration: elim ? 'line-through' : undefined, opacity: elim ? 0.55 : 1 }}>
                  {(a.actor as { seatNumber?: number }).seatNumber != null ? `${(a.actor as { seatNumber?: number }).seatNumber}号 ` : ''}{a.actor.displayName}{isSelf ? '（你）' : ''}
                </span>
                <span style={{ color: elim ? palette.bad : st === 'action_required' ? palette.human : palette.muted, whiteSpace: 'nowrap' }}>{PLAYER_STATE[st] ?? st}</span>
              </div>
              {a.latestOutput?.text ? (
                <div style={{ color: palette.muted, fontSize: 10, marginTop: 3, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 1.6 }}>{a.latestOutput.text}</div>
              ) : (
                a.publicHistory.length ? (
                  <div style={{ color: palette.muted, fontSize: 10, marginTop: 3, overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>历史：{a.publicHistory[a.publicHistory.length - 1].text}</div>
                ) : null
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ *
 *  Vote tally history
 * ------------------------------------------------------------------ */
function VoteTally({ model, palette }: { model: UndercoverGameViewModel; palette: FlavorPalette }) {
  const history = model.params.voteHistory;
  if (!history?.length) return null;
  return (
    <Panel $left="accent">
      <PanelTitle>已公布票面</PanelTitle>
      {history.map((r) => (
        <div key={`${r.round}-${r.stage}`} style={{ marginBottom: 8 }}>
          <SectLabel>第 {r.round} 轮 · {r.stage === 'pk' ? 'PK 投票' : '常规投票'}</SectLabel>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', fontSize: 11 }}>
            {r.counts.map((c) => (
              <span key={c.seatNumber} style={{ color: palette.text }}>
                <b style={{ color: palette.done }}>{c.seatNumber}号</b> {c.votes} 票
              </span>
            ))}
          </div>
          <div style={{ color: palette.muted, fontSize: 10, marginTop: 3 }}>
            {r.votes.map((v) => `${v.displayName}：${v.text}`).join('；')}
          </div>
        </div>
      ))}
    </Panel>
  );
}

/* ------------------------------------------------------------------ *
 *  Host public broadcasts
 * ------------------------------------------------------------------ */
function HostBroadcast({ model, palette, showHostOutput }: { model: UndercoverGameViewModel; palette: FlavorPalette; showHostOutput: boolean }) {
  if (!showHostOutput) return null;
  const host = model.actors.find((actor) => actor.kind === 'host');
  const history = (host?.outputHistory ?? []).filter((event) => Boolean(event.text?.trim()) && !event.pending);
  if (!history.length) return null;
  const latest = history[history.length - 1];
  const earlier = history.slice(0, -1).reverse();
  return (
    <Panel $left="accent" data-region="ops-host-broadcast">
      <PanelTitle>主持人播报</PanelTitle>
      <div style={{ whiteSpace: 'pre-wrap', fontSize: 11.5, lineHeight: 1.7, color: palette.text }}>{latest.text}</div>
      {earlier.length > 0 && (
        <details style={{ marginTop: 6 }}>
          <summary style={{ cursor: 'pointer', color: palette.muted, fontSize: 10.5 }}>更早播报（{earlier.length}）</summary>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
            {earlier.map((event) => (
              <div key={event.identity} style={{ whiteSpace: 'pre-wrap', fontSize: 10.5, lineHeight: 1.6, color: palette.muted }}>{event.text}</div>
            ))}
          </div>
        </details>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ *
 *  Accumulated public speech history
 * ------------------------------------------------------------------ */
function PublicHistory({ model, palette }: { model: UndercoverGameViewModel; palette: FlavorPalette }) {
  const rounds = model.params.publicHistory.filter((round) => round.speeches.length > 0);
  if (!rounds.length) return null;
  return (
    <Panel data-region="ops-public-history">
      <PanelTitle>历轮公开发言</PanelTitle>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {rounds.map((round, index) => (
          <details key={`${round.round}-${round.stage ?? 'regular'}`} open={index === rounds.length - 1}>
            <summary style={{ cursor: 'pointer', color: palette.muted, fontSize: 10.5 }}>
              第 {round.round} 轮{round.stage === 'pk' ? ' · PK 发言' : ' · 常规发言'}（{round.speeches.length} 条）
            </summary>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginTop: 6, fontSize: 10.5, lineHeight: 1.7 }}>
              {round.speeches.map((speech, speechIndex) => (
                <div key={`${speech.actorId}-${speechIndex}`} style={{ color: palette.muted }}>
                  <b style={{ color: palette.text }}>{speech.seatNumber}号 {speech.displayName}</b>：{speech.text}
                </div>
              ))}
            </div>
          </details>
        ))}
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ *
 *  Main panel
 * ------------------------------------------------------------------ */
const fallbackApi = '/bcnproxy';

function deadlineText(value?: number): string {
  if (!value) return '从容推敲';
  const s = Math.max(0, Math.ceil((value - Date.now()) / 1000));
  return s <= 0 ? '已到期' : `剩余 ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
function byteLength(value: string): number {
  return typeof TextEncoder !== 'undefined' ? new TextEncoder().encode(value).length : unescape(encodeURIComponent(value)).length;
}
function errorText(e: unknown): string { return e instanceof Error ? e.message : '请求失败。'; }

function InvalidParams({ message, className, style }: { message: string; className?: string; style?: CSSProperties }) {
  return (
    <RootShell $palette={OPS_PALETTE.obsidian} className={className} style={style} data-ops-panel="true">
      <Scene><Notice $left="bad"><b>面板参数无效</b><br />{message}</Notice></Scene>
    </RootShell>
  );
}

function OpsPanel({
  params, className, style, onInteraction, onAction,
}: {
  params: UndercoverGamePanelParams;
  className?: string; style?: CSSProperties;
  onInteraction?: UndercoverGamePanelProps['onInteraction'];
  onAction?: UndercoverGamePanelProps['onAction'];
}) {
  const palette = OPS_PALETTE.obsidian;
  const [graph, setGraph] = useState<Awaited<ReturnType<typeof fetchRunGraph>>>();
  const [pending, setPending] = useState<Awaited<ReturnType<typeof fetchPendingHumanNodes>>>([]);
  const [resolvedEvents, setResolvedEvents] = useState<PublicOutputEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [speech, setSpeech] = useState('');
  const [vote, setVote] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [recoverySending, setRecoverySending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [showWord, setShowWord] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const rootRef = useRef<HTMLElement | null>(null);
  const focusedActionRef = useRef('');
  const requestRef = useRef<AbortController | null>(null);
  const recoverySendingRef = useRef(false);
  const snapshotRef = useRef<{ runId: string; model: UndercoverGameViewModel } | null>(null);
  const outputCacheRef = useRef(new Map<string, PublicOutputEvent>());
  const outputRequestsRef = useRef(new Map<string, Promise<PublicOutputEvent | undefined>>());
  const pollTimerRef = useRef<number | null>(null);
  const pollGenerationRef = useRef(0);
  const [publishedResult, setPublishedResult] = useState<{ runId: string; value: PublicGameResult } | null>(null);
  const [resultError, setResultError] = useState<{ message: string; blocking: boolean } | null>(null);
  const publicResultRef = useRef<PublicGameResult | undefined>(undefined);
  const terminalResultPolls = useRef(0);

  const resolveOutputs = useCallback(async (g: Awaited<ReturnType<typeof fetchRunGraph>>, c: AbortController) => {
    await Promise.all(completedMappedNodes(g.nodes ?? [], params).map(async (node) => {
      const attempt = typeof node.attempt === 'number' && node.attempt > 0 ? node.attempt : 1;
      const key = `${params.runId}:${node.node_id}:${attempt}`;
      if (outputCacheRef.current.has(key)) return;
      let request = outputRequestsRef.current.get(key);
      if (!request) {
        request = fetchNodeDetail(params.apiBaseUrl ?? fallbackApi, params.runId, node.node_id, c.signal)
          .then((d) => publicEventFromNodeDetail(d, params))
          .then((event) => { if (event && !c.signal.aborted) outputCacheRef.current.set(key, event); return event; })
          .catch((e) => { if (e instanceof Error && e.name === 'AbortError') throw e; throw new Error(`发言内容加载失败：${errorText(e)}`); })
          .finally(() => outputRequestsRef.current.delete(key));
        outputRequestsRef.current.set(key, request);
      }
      await request;
    }));
    return [...outputCacheRef.current.values()].filter((event) => event.runId === params.runId);
  }, [params]);

  const refresh = useCallback(async (initial = false): Promise<boolean> => {
    requestRef.current?.abort();
    const c = new AbortController();
    requestRef.current = c;
    setError(null);
    if (initial) setLoading(true); else setRefreshing(true);
    try {
      const [g, p] = await Promise.all([
        fetchRunGraph(params.apiBaseUrl ?? fallbackApi, params.runId, c.signal),
        fetchPendingHumanNodes(params.apiBaseUrl ?? fallbackApi, params.runId, c.signal),
      ]);
      const outputs = await resolveOutputs(g, c);
      let finale = publicResultRef.current;
      let finaleError: { message: string; blocking: boolean } | null = null;
      if (params.resultFile && !finale) {
        try { finale = await fetchPublicGameResult(params, c.signal); }
        catch (e) {
          if (c.signal.aborted) return false;
          finaleError = { message: `结算结果加载失败：${errorText(e)}`, blocking: !(e instanceof ApiRequestError && e.retryable) };
        }
      }
      if (requestRef.current !== c || c.signal.aborted) return false;
      publicResultRef.current = finale;
      setPublishedResult(finale ? { runId: params.runId, value: finale } : null);
      setResultError(finaleError);
      const model = normalizeUndercoverGameViewModel(params, g, p, [], outputs);
      snapshotRef.current = { runId: params.runId, model };
      setGraph(g);
      setPending(p);
      setResolvedEvents(outputs);
      setStale(false);
      if (!model.terminal) terminalResultPolls.current = 0;
      return model.terminal && (!params.resultFile || Boolean(finale) || ++terminalResultPolls.current >= 5);
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return false;
      if (requestRef.current === c) setError(errorText(e));
      return false;
    } finally {
      if (requestRef.current === c) { setLoading(false); setRefreshing(false); }
    }
  }, [params, resolveOutputs]);

  useEffect(() => {
    const generation = ++pollGenerationRef.current;
    if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
    requestRef.current?.abort();
    outputCacheRef.current.clear();
    outputRequestsRef.current.clear();
    publicResultRef.current = undefined;
    terminalResultPolls.current = 0;
    setPublishedResult(null); setResultError(null); setResolvedEvents([]);
    setSpeech(''); setVote(''); setConfirmed(false); setSubmitted(false); setStale(false);
    setShowWord(false); setActionError(null);
    focusedActionRef.current = '';
    const schedule = () => {
      if (!params.autoRefresh || generation !== pollGenerationRef.current) return;
      pollTimerRef.current = window.setTimeout(async () => {
        const terminal = await refresh(false);
        if (!terminal && generation === pollGenerationRef.current) schedule();
      }, params.pollingInterval ?? 3000);
    };
    void refresh(true).then((terminal) => { if (!terminal && generation === pollGenerationRef.current) schedule(); });
    return () => {
      pollGenerationRef.current += 1;
      if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
      requestRef.current?.abort();
    };
  }, [params.runId, params.phase, params.attempt, params.autoRefresh, params.pollingInterval, params.sessionId, params.resultFile, params.host.actorId, params.apiBaseUrl]);

  const model = useMemo(
    () => normalizeUndercoverGameViewModel(params, graph, pending, [], resolvedEvents),
    [params, graph, pending, resolvedEvents],
  );
  const fallback = snapshotRef.current?.runId === params.runId ? snapshotRef.current.model : null;
  const shown = error ? (fallback ?? model) : model;
  const currentResult = publishedResult?.runId === params.runId
    && publishedResult.value.gameSessionId === (params.gameSessionId ?? params.sessionId)
    && publishedResult.value.hostActorId === params.host.actorId
    && publishedResult.value.round === params.round
    && publishedResult.value.attempt === params.attempt
    ? publishedResult.value : undefined;
  const result = useMemo(
    () => (currentResult ? publicGameResultView(currentResult, params) : resultError?.blocking ? undefined : gameResult(shown)),
    [currentResult, params, resultError, shown],
  );

  // Width classification for ≤360px support.
  useEffect(() => {
    const node = rootRef.current;
    if (!node) return;
    const classify = (w: number) => setNarrow(w <= 360);
    classify(node.getBoundingClientRect().width || 800);
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver((entries) => classify(entries[0]?.contentRect.width ?? 800));
      observer.observe(node);
      return () => observer.disconnect();
    }
    const handler = () => classify(node.getBoundingClientRect().width || window.innerWidth);
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, [loading]);

  const workflow = useMemo(
    () => buildRunnerGraph(graph?.nodes ?? [], graph?.edges, params.nodeActorMap, params.host.actorId, params.players),
    [graph, params.nodeActorMap, params.host.actorId, params.players],
  );

  const pendingHumanNode = shown.pendingHumanActorId === params.currentViewerActorId ? shown.pendingHumanNode : undefined;
  const parsed = useMemo(() => parsePrivateActionContext(pendingHumanNode?.instruction), [pendingHumanNode?.instruction]);
  const actionKind = parsed.context?.action ?? params.currentAction?.type ?? (params.phase.toLowerCase().includes('vot') ? 'vote' : 'speech');
  const actionKey = pendingHumanNode ? `${params.runId}:${pendingHumanNode.node_id}:${pendingHumanNode.attempt ?? params.attempt}:${actionKind}` : '';
  const isPk = params.phase.startsWith('pk_');
  const allowAbstain = !isPk && (parsed.context?.action !== 'vote' || parsed.context.allowAbstain);
  const canAct = Boolean(!result && pendingHumanNode && !submitted && !stale && !parsed.error);

  const upstream = useMemo(
    () => normalizeCurrentRoundUpstream(pendingHumanNode?.upstream_artifacts, params),
    [pendingHumanNode?.upstream_artifacts, params],
  );

  useEffect(() => {
    if (!actionKey || submitted || stale || focusedActionRef.current === actionKey) return;
    focusedActionRef.current = actionKey;
    if (actionKind === 'speech') inputRef.current?.focus();
    else (rootRef.current?.querySelector('[data-ops-vote-choice]') as HTMLElement | null)?.focus();
  }, [actionKey, actionKind, stale, submitted]);

  const candidates = useMemo(
    () => eligibleVoteCandidates(params.voteCandidates).filter(
      (c) => c.actorId !== params.currentViewerActorId && (!isPk || params.pkCandidates?.includes(c.actorId)),
    ),
    [params.voteCandidates, params.currentViewerActorId, isPk, params.pkCandidates],
  );

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (result || !pendingHumanNode || submitting || submitted || stale || parsed.error) return;
    let content: string;
    let interactionAction: 'speech' | 'vote' | 'abstain';
    if (actionKind === 'vote') {
      if (!confirmed || !vote) {
        setActionError(allowAbstain ? '请选择一位可投玩家或弃权，并勾选确认。' : '请选择一位 PK 玩家，并勾选确认。');
        return;
      }
      const valid = vote === '__abstain__' ? allowAbstain : candidates.some((c) => c.actorId === vote);
      if (!valid) { setActionError('请选择合法投票对象。'); return; }
      content = vote === '__abstain__' ? serializeVoteAbstain() : serializeVoteTarget(vote);
      interactionAction = vote === '__abstain__' ? 'abstain' : 'vote';
    } else {
      const ctx = parsed.context?.action === 'speech' ? parsed.context : undefined;
      const max = ctx?.maxChars ?? params.rules.speechMaxChars ?? 25;
      const valid = validateSpeech(speech, max, ctx?.word, ctx?.forbidOwnWord ?? params.rules.forbidOwnWord);
      if (!valid.valid) {
        setActionError(valid.error === 'empty' ? '发言不能为空。' : valid.error === 'too_long' ? `发言不能超过 ${max} 个字。` : '发言不能包含你自己的词。');
        return;
      }
      if (byteLength(speech.trim()) > (params.maxResponseBytes ?? 65536)) { setActionError('发言超过传输大小限制。'); return; }
      content = speech.trim();
      interactionAction = 'speech';
    }
    setSubmitting(true); setActionError(null);
    try {
      await respondToHumanNode(params.apiBaseUrl ?? fallbackApi, params.runId, pendingHumanNode.node_id, content);
      setSubmitted(true); setSpeech(''); setVote(''); setConfirmed(false);
      onInteraction?.({ type: 'submit-human-input', actorId: params.currentViewerActorId, nodeId: pendingHumanNode.node_id, runId: params.runId, action: interactionAction });
      await refresh(false);
    } catch (e) {
      if (e instanceof ApiRequestError && e.conflict) {
        setStale(true); setActionError('这个操作已经过期或被处理，请等待最新状态。'); void refresh(false);
      } else setActionError(errorText(e));
    } finally { setSubmitting(false); }
  };

  const recover = async () => {
    if (recoverySendingRef.current) return;
    if (!onAction) return;
    if (typeof window.confirm === 'function' && !window.confirm('确认告诉主持人“卡住了”？')) return;
    recoverySendingRef.current = true;
    setRecoverySending(true);
    try {
      const actionPayload: PanelAction = { type: 'send_message', content: '卡住了' };
      await onAction(actionPayload);
      onInteraction?.({ type: 'recovery', runId: params.runId });
    } catch { setError('通知主持人失败，请重试。'); }
    finally { recoverySendingRef.current = false; setRecoverySending(false); }
  };

  if (loading && !fallback) {
    return (
      <RootShell $palette={palette} className={className} style={style} data-ops-panel="true" data-layout={narrow ? 'narrow' : 'wide'}>
        <Scene><Notice $left="running">正在加载游戏房间…</Notice></Scene>
      </RootShell>
    );
  }

  const phaseLabel = (isPk ? 'PK ' : '') + (params.phase.toLowerCase().includes('vot') ? '投票' : '发言');
  const deadline = pendingHumanNode?.timeout_deadline_ms ?? params.currentAction?.deadlineAt ?? params.deadlineAt;
  const terminalCopy = result ? '本局已结束。' : shown.terminal ? (shown.status === 'failed' || shown.status === 'aborted' ? '本阶段未正常完成。' : '本阶段已完成。') : null;
  const voteOpening = params.phase.toLowerCase().includes('vot') && params.openingAnnouncement && graph?.run.run_id === params.runId && shown.status === 'running'
    ? (graph.nodes.some((node) => node.node_id === 'vote_start' && node.status === 'completed') ? params.openingAnnouncement : '主持人正在开启投票，请稍候。')
    : null;

  return (
    <RootShell
      ref={rootRef}
      $palette={palette}
      className={className}
      style={style}
      data-ops-panel="true"
      data-layout={narrow ? 'narrow' : 'wide'}
      data-action-active={String(Boolean(canAct))}
      aria-live="polite"
    >
      <TopBar>
        <Brand>
          <BrandEyebrow color={palette.muted}>UNDERCOVER · OPS/GAME</BrandEyebrow>
          <BrandTitle color={palette.accent}>{terminalCopy?.includes('已结束') ? '谁是卧底 · 终局' : `谁是卧底 · 第 ${shown.round} 轮`}</BrandTitle>
        </Brand>
        <StatusStrip color={palette.muted}>
          <span><span className="dot" style={{ background: shown.terminal ? palette.done : palette.running }} />{phaseLabel}</span>
          <span>{shown.completedTurns}/{shown.totalTurns} 完成</span>
          {deadline && <span>{deadlineText(deadline)}</span>}
        </StatusStrip>
        <MonoBtn type="button" aria-label="刷新游戏状态" onClick={() => { onInteraction?.({ type: 'refresh', runId: params.runId }); void refresh(false); }} disabled={refreshing}>
          {refreshing ? '正在同步…' : '↻ 刷新'}
        </MonoBtn>
      </TopBar>

      <Scene>
        {error && <Notice $left="bad" role="alert">刷新失败：{error}。已保留最近一次成功的房间状态。</Notice>}
        {resultError && <Notice $left="bad" role="alert">{resultError.message}。请刷新重试。</Notice>}
        {terminalCopy && <Notice $left="running">{terminalCopy}</Notice>}
        {voteOpening && <Notice $left="running" role="status" data-region="ops-vote-opening">{voteOpening}</Notice>}

        <Panel $left="running">
          <PanelTitle>本轮实际运行的 workflow</PanelTitle>
          {graph ? (
            <WorkflowGraph layout={workflow} palette={palette} compact={narrow} />
          ) : (
            <div style={{ color: palette.muted }}>尚未取得 graph 快照。</div>
          )}
          <div style={{ marginTop: 8, fontSize: 10.5, color: palette.muted }}>
            {params.phase.toLowerCase().includes('vot') || isPk
              ? '投票 run：入口 → 各玩家并行投票（无依赖边） → 主持人计票（终列末节点）。'
              : '发言 run：按回合顺序传递闭包链，后手可读齐前序原话；collect 汇总后结束。'}
          </div>
        </Panel>

        <Roster model={shown} palette={palette} currentViewer={params.currentViewerActorId} />
        <VoteTally model={shown} palette={palette} />
        <HostBroadcast model={shown} palette={palette} showHostOutput={params.display?.showHostOutput !== false} />
        <PublicHistory model={shown} palette={palette} />

        {!result && pendingHumanNode && (
          <Panel $left={actionKind === 'vote' ? 'good' : 'accent'}>
            <PanelTitle>{actionKind === 'vote' ? (isPk ? 'PK 投票' : '投票') : '你的发言'} · 请求输入</PanelTitle>
            {params.openingAnnouncement && graph?.run?.run_id === params.runId && shown.status === 'running' && graph.nodes.some(node => node.node_id === 'vote_start' && node.status === 'completed') && (
              <Notice $left="accent" role="status">{params.openingAnnouncement}</Notice>
            )}
            {parsed.error && <Notice $left="bad" role="alert">{parsed.error}</Notice>}
            {!parsed.error && canAct && (
              <form onSubmit={submit}>
                {actionKind === 'speech' ? (
                  <>
                    {parsed.context?.action === 'speech' && parsed.context.word && (
                      <WordChip>
                        你的词：
                        <span>{showWord ? parsed.context.word : '••••'}</span>
                        <MonoBtn type="button" aria-pressed={showWord} onClick={() => setShowWord((v) => !v)}>{showWord ? '隐藏' : '显示'}</MonoBtn>
                      </WordChip>
                    )}
                    <div style={{ margin: '7px 0 6px', fontSize: 10.5, color: palette.muted }}>
                      不超过 {parsed.context?.action === 'speech' ? parsed.context.maxChars : params.rules.speechMaxChars ?? 25} 个字{params.rules.forbidOwnWord ? ' · 不要说出自己的词' : ''}
                      {params.rules.bluntness ? ` · 本轮钝度 ${params.rules.bluntness}` : ''}
                    </div>
                    {upstream.length > 0 && (
                      <div style={{ marginBottom: 6 }}>
                        <SectLabel>本轮此前发言</SectLabel>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 10.5 }}>
                          {upstream.map((u) => (
                            <div key={u.nodeId} style={{ color: palette.muted }}><b style={{ color: palette.text }}>{u.seatNumber}号 {u.displayName}</b>：{u.text}</div>
                          ))}
                        </div>
                      </div>
                    )}
                    <TextBox
                      ref={inputRef}
                      aria-label="你的公开发言"
                      placeholder="描述它的特点，但别暴露你的词…"
                      value={speech}
                      onChange={(e) => { setSpeech(e.target.value); setActionError(null); }}
                      disabled={submitting}
                    />
                    <div style={{ marginTop: 4, fontSize: 10.5, color: palette.muted }}>
                      {unicodeLength(speech)} / {parsed.context?.action === 'speech' ? parsed.context.maxChars : params.rules.speechMaxChars ?? 25} 字
                    </div>
                  </>
                ) : (
                  <>
                    <CandidateGrid role="radiogroup" aria-label="选择投票对象">
                      {candidates.map((c) => (
                        <Candidate
                          key={c.actorId}
                          type="button"
                          role="radio"
                          disabled={submitting}
                          aria-checked={vote === c.actorId}
                          data-ops-vote-choice="true"
                          data-pk-candidate={isPk ? 'true' : undefined}
                          $selected={vote === c.actorId}
                          onClick={() => { setVote(c.actorId); setConfirmed(false); setActionError(null); }}
                        >
                          {c.seatNumber}号 {c.displayName}
                        </Candidate>
                      ))}
                      {allowAbstain && (
                        <Candidate type="button" role="radio" disabled={submitting} aria-checked={vote === '__abstain__'} data-ops-vote-choice="true" $selected={vote === '__abstain__'} onClick={() => { setVote('__abstain__'); setConfirmed(false); setActionError(null); }}>
                          弃权
                        </Candidate>
                      )}
                    </CandidateGrid>
                    <ConfirmRow>
                      <input type="checkbox" checked={confirmed} disabled={submitting} onChange={(e) => setConfirmed(e.target.checked)} aria-label="确认投票" />
                      我已确认，投出这一票
                    </ConfirmRow>
                  </>
                )}
                <div style={{ marginTop: 10 }}>
                  <MonoBtn data-primary type="submit" disabled={submitting}>
                    {submitting ? '提交中…' : actionKind === 'vote' ? '确认投票' : '提交发言'}
                  </MonoBtn>
                  {actionError && <span role="alert" style={{ color: palette.bad, marginLeft: 8, fontSize: 10.5 }}>{actionError}</span>}
                </div>
              </form>
            )}
            {!canAct && !parsed.error && (
              <div style={{ color: palette.muted, fontSize: 11 }}>
                {submitted ? '✓ 已提交，等待其他玩家。' : stale ? '正在同步最新状态…' : '等待新一轮任务下发。'}
              </div>
            )}
          </Panel>
        )}

        {result && (
          <Panel $left="accent">
            <PanelTitle>终局</PanelTitle>
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 11.5, lineHeight: 1.7 }}>{result.title}</div>
            {result.summary && (
              <div data-region="ops-finale-summary" style={{ marginTop: 6, whiteSpace: 'pre-wrap', fontSize: 11, lineHeight: 1.7, color: palette.muted }}>{result.summary}</div>
            )}
            <div style={{ marginTop: 8 }}>
              <MonoBtn data-primary type="button" onClick={() => onInteraction?.({ type: 'refresh', runId: params.runId })}>【重复】查看终局</MonoBtn>
            </div>
          </Panel>
        )}
      </Scene>

      <Footer>
        <MonoBtn type="button" data-primary={Boolean(canAct)} onClick={() => { if (canAct) void (rootRef.current?.querySelector('[data-ops-vote-choice], textarea') as HTMLElement | null)?.focus(); }}>
          我的回合
        </MonoBtn>
        <MonoBtn type="button" onClick={() => void recover()} disabled={recoverySending}>{recoverySending ? '正在通知…' : '卡住了？告诉主持人'}</MonoBtn>
        {error && <span role="alert" style={{ color: palette.bad }}>{error}</span>}
      </Footer>
    </RootShell>
  );
}

export default function UndercoverGameOpsPanel(props: UndercoverGamePanelProps) {
  const normalized = useMemo(() => {
    try { return { params: normalizePanelParams(props), error: null }; }
    catch (e) { return { params: null, error: e instanceof Error ? e.message : 'Invalid panel parameters.' }; }
  }, [props]);
  if (!normalized.params) return <InvalidParams message={normalized.error ?? 'Invalid panel parameters.'} className={props.className} style={props.style} />;
  return <OpsPanel params={normalized.params} className={props.className} style={props.style} onInteraction={props.onInteraction} onAction={props.onAction} />;
}