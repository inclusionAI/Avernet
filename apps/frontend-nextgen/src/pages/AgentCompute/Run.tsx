import { PageHeader } from '@/components/Common/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Segmented } from '@/components/ui/Segmented';
import { formatRunTimeOrDash } from '@/components/agentswarm/formatRunTime';
import { QuoteDisclosure } from '@/components/agentswarm/QuoteDisclosure';
import { ValueBlock } from '@/components/agentswarm/ValueBlock';
import { useRunDetail } from '@/hooks/useRunDetail';
import { useRunPolling } from '@/hooks/useRunPolling';
import { resolveAgentswarmTenant } from '@/services/agentswarm/tenant';
import {
  agentStatusLabel,
  agentStatusTone,
  isTerminalStatus,
  runStatusLabel,
  runStatusTone,
  type AgentDetailDTO,
  type RunDetailDTO,
  type RunSummaryDTO,
} from '@/services/agentswarm/types';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { isEmptyOutput } from '@/domain/agentswarm/output';
import { history, useParams } from '@umijs/max';
import { ChevronDown, ChevronRight, RotateCw } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

const BASE = '/agent-compute';

type Tab = 'execution' | 'log';

/** One agent's line in the master list: status, role and attempt count. */
function AgentListItem({
  agent,
  selected,
  onSelect,
}: {
  agent: AgentDetailDTO;
  selected: boolean;
  onSelect: () => void;
}) {
  // An agent's states are its own (`QUEUED`/`KILLED` do not exist for a run), so
  // reading them through the run map left QUEUED untranslated and untoned.
  const tone = agentStatusTone(agent.status);
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected ? 'true' : undefined}
      className={[
        'flex w-full flex-col gap-1 rounded-lg border px-3 py-2 text-left',
        selected ? 'border-primary bg-primary/5' : 'border-divider hover:bg-accent',
      ].join(' ')}
    >
      <span className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{agent.name}</span>
        <Badge tone={tone}>{agentStatusLabel(agent.status)}</Badge>
      </span>
      {agent.role ? (
        <span className="truncate text-xs text-muted-foreground">{agent.role}</span>
      ) : null}
      <span className="flex items-center gap-2 text-xs text-muted-foreground">
        {/* The catalog entry is what was submitted; the agent is what ran. */}
        {agent.catalog_name ? <span className="truncate">来自 {agent.catalog_name}</span> : null}
        <span className="shrink-0">尝试 {agent.attempts} 次</span>
      </span>
      {/* A completed agent that produced nothing must not read as a clean
          success: that is what hid an empty stage behind an all-green list. */}
      {agent.status === 'COMPLETED' && isEmptyOutput(agent.output) ? (
        <span className="text-[11px] font-medium text-warning">结果为空，未满足要求</span>
      ) : null}
    </button>
  );
}

/**
 * Spawned agents grouped by the round that dispatched them.
 *
 * A catalog entry can be dispatched once per round, so two rows can carry the
 * same name; the round is what tells them apart. Ordered by round, and stable
 * inside a round so a list does not reshuffle between polls.
 */
export function groupByRound(
  agents: readonly AgentDetailDTO[],
): Array<{ round: number; items: AgentDetailDTO[] }> {
  const byRound = new Map<number, AgentDetailDTO[]>();
  for (const agent of agents) {
    const round = agent.spawn_round ?? 0;
    const bucket = byRound.get(round);
    if (bucket) bucket.push(agent);
    else byRound.set(round, [agent]);
  }
  return [...byRound.entries()]
    .sort(([a], [b]) => a - b)
    .map(([round, items]) => ({ round, items }));
}

/** The middle pane for one agent: its stage output and attempt ledger. */
function AgentDetailPane({ agent }: { agent: AgentDetailDTO }) {
  const attempts = agent.attempt_ledger ?? [];
  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-sm font-semibold">阶段产出</h3>
        {agent.error ? (
          <p className="m-0 rounded-lg bg-destructive/10 p-3 text-sm text-destructive">
            {agent.error}
          </p>
        ) : null}
        {agent.status === 'COMPLETED' && isEmptyOutput(agent.output) ? (
          <p
            role="alert"
            className="m-0 rounded-lg bg-warning/10 px-3 py-2 text-sm font-medium text-warning"
          >
            该 Agent 已判定完成，但产出为空：这次执行没有给出可用的结果，通常需要重新派发。
          </p>
        ) : null}
        {agent.objective ? (
          <QuoteDisclosure
            label="指令"
            value={agent.objective}
            preview={agent.objective.split('\n')[0]}
            tone="muted"
          />
        ) : null}
        {agent.output === undefined || agent.output === null ? (
          <p className="m-0 text-sm text-muted-foreground">（该 Agent 还没有产出）</p>
        ) : (
          <QuoteDisclosure
            label="输出"
            value={outputAsText(agent.output)}
            preview={outputPreview(agent.output)}
            mono
          />
        )}
      </section>

      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-sm font-semibold">执行尝试</h3>
        {attempts.length ? (
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-xs">
            {attempts.map((attempt, index) => (
              <li
                key={`${attempt.executor_id}-${index}`}
                className="rounded-lg bg-muted/60 px-3 py-2"
              >
                #{attempt.slice_retry_index} · {attempt.executor_name || attempt.executor_id}
                {attempt.result_status ? ` · ${attempt.result_status}` : ''}
              </li>
            ))}
          </ul>
        ) : (
          <p className="m-0 text-xs text-muted-foreground">没有尝试记录。</p>
        )}
      </section>
    </div>
  );
}

/** Right column of 执行详情: progress plus the configuration that was submitted. */
function ProgressColumn({ detail, run }: { detail: RunDetailDTO | null; run: RunSummaryDTO }) {
  const rounds = detail?.rounds ?? [];
  const guards = detail?.guards ?? [];
  return (
    <div className="flex flex-col gap-4">
      {/* Submitted first: it is what the reader is checking the progress
          against — "was this what I asked for?" before "how far did it get?". */}
      {/* 提交时的 Agent 配置。The catalog is captured at submit, so it says what
          was asked for — the spawned agents below say what ran. Keeping both is
          what makes a run auditable after the definition changes. */}
      <section className="flex flex-col gap-2" aria-label="提交时的 Agent 配置">
        <h3 className="m-0 text-sm font-semibold">
          Agent 配置{detail?.agent_catalog?.length ? `（${detail.agent_catalog.length}）` : ''}
        </h3>
        {detail?.agent_catalog?.length ? (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {detail.agent_catalog.map((spec) => {
              // A catalog entry that came from the library can be opened where it
              // is maintained; one created inline has no stored definition to go
              // to, so it stays a plain card rather than a dead link.
              const stored = spec.definition_id
                ? `${BASE}/definitions?definition=${encodeURIComponent(spec.definition_id)}` +
                  `&name=${encodeURIComponent(spec.name)}`
                : null;
              // Every spawn of this entry, so the card can say how many ran and
              // how many of those came back empty — the two facts that explain a
              // re-dispatch.
              const spawns = (detail.agents ?? []).filter(
                (agent) => agent.catalog_name === spec.name,
              );
              const emptySpawns = spawns.filter(
                (agent) => agent.status === 'COMPLETED' && isEmptyOutput(agent.output),
              ).length;
              return (
              <li key={spec.name}>
                <div
                  role={stored ? 'link' : undefined}
                  tabIndex={stored ? 0 : undefined}
                  onClick={stored ? () => history.push(stored) : undefined}
                  onKeyDown={
                    stored
                      ? (event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            history.push(stored);
                          }
                        }
                      : undefined
                  }
                  aria-label={stored ? `打开定义 ${spec.name}` : undefined}
                  className={[
                    'flex flex-col gap-1 rounded-lg border border-divider bg-card px-3 py-2',
                    stored ? 'cursor-pointer hover:border-primary hover:bg-accent' : '',
                  ].join(' ')}
                >
                <span className="truncate text-xs font-medium text-foreground" title={spec.name}>
                  {spec.name}
                </span>
                {spec.role ? (
                  <span className="truncate text-[11px] text-muted-foreground" title={spec.role}>
                    {spec.role}
                  </span>
                ) : null}
                <span className="flex flex-wrap items-center gap-1 pt-0.5">
                  <Badge tone="neutral">{spec.kind === 'bot' ? 'Bot' : spec.kind}</Badge>
                  <Badge tone={spec.model ? 'primary' : 'outline'}>
                    {spec.model || '跟随默认'}
                  </Badge>
                  <Badge tone={stored ? 'primary' : 'outline'}>
                    {stored ? '定义库' : '空白创建'}
                  </Badge>
                  {stored ? <ChevronRight className="size-3 text-muted-foreground" aria-hidden /> : null}
                </span>
                {spawns.length ? (
                  <span className="text-[11px] text-muted-foreground">
                    已派发 {spawns.length} 次
                    {emptySpawns ? (
                      <span className="text-warning">，其中 {emptySpawns} 次结果为空</span>
                    ) : null}
                  </span>
                ) : null}
                </div>
              </li>
              );
            })}
          </ul>
        ) : (
          <p className="m-0 text-xs text-muted-foreground">这次运行没有记录 Agent 配置。</p>
        )}
      </section>

      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-sm font-semibold">任务进展</h3>
        {rounds.length ? (
          <ol className="m-0 flex list-none flex-col gap-2 p-0">
            {rounds.map((round) => (
              <li key={round.round_id} className="rounded-lg bg-muted/60 px-3 py-2 text-xs">
                <span className="font-medium">第 {round.stage_index + 1} 轮</span>
                <span className="ml-2 text-muted-foreground">
                  {formatRunTimeOrDash(round.started_at)} 起 · {round.cohort_agent_ids.length} 个 Agent
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="m-0 text-xs text-muted-foreground">还没有编排记录。</p>
        )}
        {guards.length ? (
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-xs text-muted-foreground">
            {guards.map((guard) => (
              <li key={guard.event_id}>
                保护事件 {guard.kind} · {formatRunTimeOrDash(guard.created_at)}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

    </div>
  );
}

/**
 * One line of the run log.
 *
 * The gateway exposes no event stream, so the log is assembled from the three
 * time-stamped sources it does give: stage boundaries (rounds), safety trips
 * (guards) and per-agent attempts. Merging them by time is the honest
 * approximation of "what happened, in order" — anything finer does not exist
 * to show.
 */
interface RunLogEvent {
  key: string;
  at: number | null;
  kind: 'round' | 'guard' | 'attempt';
  label: string;
  detail?: string;
  /** The round this event belongs to, or null for run-level events (guards). */
  round: number | null;
  /**
   * What the agent produced, for an attempt. A log that said only
   * "COMPLETED" could not explain its own run: the empty result that forced a
   * second dispatch looked identical to the one that worked.
   */
  output?: unknown;
  /** The instruction the agent was dispatched with, when the log has one. */
  objective?: string | null;
  /**
   * The agent an attempt belongs to, used to order siblings that share a
   * millisecond. A round's row is written just after its first attempts, and
   * those share timestamps, so time alone cannot order them.
   */
  agentName?: string;
}

/** The prose an output carries, if it carries any. */
function outputPreview(value: unknown): string | undefined {
  const text = outputAsText(value);
  const first = text.split('\n')[0]?.trim();
  return first || undefined;
}

/** An agent's output as the text to show: prose stays prose. */
function outputAsText(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function buildRunLog(detail: RunDetailDTO, runFinished: boolean): RunLogEvent[] {
  const events: RunLogEvent[] = [];

  for (const round of detail.rounds ?? []) {
    events.push({
      key: `round-${round.round_id}`,
      at: round.started_at ?? null,
      kind: 'round',
      round: round.stage_index,
      label: `第 ${round.stage_index + 1} 轮编排开始`,
      detail: [
        `${round.cohort_agent_ids.length} 个 Agent`,
        round.ended_at
          ? `结束 ${formatRunTimeOrDash(round.ended_at)}`
          : runFinished
            ? ''
            : '进行中',
      ]
        .filter(Boolean)
        .join(' · '),
    });
  }

  for (const agent of detail.agents ?? []) {
    for (const [index, attempt] of (agent.attempt_ledger ?? []).entries()) {
      events.push({
        key: `attempt-${agent.agent_id}-${index}`,
        at: attempt.started_at ?? null,
        kind: 'attempt',
        round: agent.spawn_round ?? 0,
        label: `${agent.name} 第 ${attempt.slice_retry_index} 次尝试`,
        detail: [attempt.executor_name || attempt.executor_id, attempt.result_status]
          .filter(Boolean)
          .join(' · '),
        // One agent can be dispatched more than once; a later dispatch is a
        // different agent and has its own output.
        output: agent.output,
        objective: agent.objective,
        agentName: agent.name,
      });
    }
  }

  for (const guard of detail.guards ?? []) {
    events.push({
      key: `guard-${guard.event_id}`,
      at: guard.created_at ?? null,
      kind: 'guard',
      round: null,
      label: `运行保护触发：${guard.kind}`,
      detail: '',
    });
  }

  // Ordered by round, then time, then agent name, then key.
  //
  // Timestamps alone cannot order this log: a round's row and its first
  // attempts are written within a few milliseconds of each other, in an order
  // decided by transaction timing — observed as round …903859 with attempts at
  // …903855/…903857/…903862. Sorting by round makes that read as the sequence
  // it was.
  //
  // Every comparison below ends in a total order (`key` is unique per event), so
  // the result is identical on every render. That matters because the log is
  // rebuilt from a polled payload: a comparator that returns 0 for two distinct
  // events lets their order follow the input, and the list visibly reshuffles
  // between refreshes.
  const kindRank: Record<RunLogEvent['kind'], number> = {
    round: 0,
    attempt: 1,
    guard: 2,
  };
  return events.sort((a, b) => {
    const roundA = a.round ?? Number.MAX_SAFE_INTEGER;
    const roundB = b.round ?? Number.MAX_SAFE_INTEGER;
    if (roundA !== roundB) return roundA - roundB;
    // A round opens the work it contains, whatever the millisecond says.
    if (a.kind === 'round' && b.kind !== 'round') return -1;
    if (b.kind === 'round' && a.kind !== 'round') return 1;
    if (a.at !== b.at) {
      // Events with no timestamp sink to the bottom of their round rather than
      // pretending to be epoch zero.
      if (a.at === null) return 1;
      if (b.at === null) return -1;
      return a.at - b.at;
    }
    const byAgent = (a.agentName ?? '').localeCompare(b.agentName ?? '');
    if (byAgent !== 0) return byAgent;
    if (a.kind !== b.kind) return kindRank[a.kind] - kindRank[b.kind];
    return a.key.localeCompare(b.key);
  });
}

/** A round's header, with its attempts collapsed behind it. */
function RoundDisclosure({
  round,
  count,
  defaultOpen,
  children,
}: {
  round: RunLogEvent;
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(Boolean(defaultOpen));
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 rounded-xl px-4 py-3 text-left hover:bg-muted/60"
      >
        {open ? (
          <ChevronDown className="size-3.5 shrink-0 self-center text-muted-foreground" aria-hidden />
        ) : (
          <ChevronRight className="size-3.5 shrink-0 self-center text-muted-foreground" aria-hidden />
        )}
        <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
          {formatRunTimeOrDash(round.at)}
        </span>
        <span className="min-w-0 flex-1 text-sm font-semibold text-foreground">{round.label}</span>
        <span className="shrink-0 text-xs text-muted-foreground">
          {round.detail ? `${round.detail} · ` : ''}
          {count} 条记录
        </span>
      </button>
      {open && count ? (
        <ol className="m-0 flex list-none flex-col gap-2 px-3 pb-3 p-0">{children}</ol>
      ) : null}
    </>
  );
}

/**
 * One log event's body: time, label, verdict and the collapsible payloads.
 *
 * Shared by a round's attempts and by loose events, so a guard and an attempt
 * cannot drift into two different-looking rows.
 */
function RunLogEntry({ event }: { event: RunLogEvent }) {
  return (
    <>
      {/* Timestamp and verdict form one fixed row; the label never wraps away
          from them. Letting the whole line wrap put the verdict on its own row
          and pushed the agent name down, so rows stopped lining up. */}
      <div className="flex items-baseline gap-x-3 gap-y-1">
        <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
          {formatRunTimeOrDash(event.at)}
        </span>
        {/* The name takes the slack, pushing its verdict and executor line to
            the right edge so those columns line up down the list. */}
        <span className="min-w-0 flex-1 truncate text-sm" title={event.label}>
          {event.label}
        </span>
        {/* The verdict sits between the agent and its executor line, so a row
            with an empty result reads "brave_writer 结果为空 · COMPLETED". */}
        {event.output !== undefined && isEmptyOutput(event.output) ? (
          <span className="shrink-0 rounded-full bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning">
            结果为空
          </span>
        ) : null}
        {event.detail ? (
          <span className="min-w-0 truncate text-xs text-muted-foreground">{event.detail}</span>
        ) : null}
      </div>
      {event.objective || (event.output !== undefined && event.output !== null) ? (
        <div className="mt-2 flex flex-col gap-1.5">
          {event.objective ? (
            <QuoteDisclosure
              label="指令"
              value={event.objective}
              preview={event.objective.split('\n')[0]}
              tone="muted"
            />
          ) : null}
          {event.output !== undefined && event.output !== null ? (
            <QuoteDisclosure
              label="输出"
              value={outputAsText(event.output)}
              preview={outputPreview(event.output)}
              mono
            />
          ) : null}
        </div>
      ) : null}
    </>
  );
}

/**
 * The log as collapsible rounds.
 *
 * Rounds are the log's own structure, so they are its headers: collapsing one
 * leaves the sequence of rounds visible and hides the dispatch detail inside,
 * which is what a reader scanning a long run wants. Events belonging to no
 * round (guards) stay loose at the end, always visible — they are the run's
 * outcome, not one round's work.
 */
interface RunLogGroup {
  key: string;
  /** Present for a collapsible round; absent for a loose trailing event. */
  round: RunLogEvent | null;
  items: RunLogEvent[];
  loose: RunLogEvent[];
}

export function groupRunLog(events: readonly RunLogEvent[]): RunLogGroup[] {
  const groups: RunLogGroup[] = [];
  let byRound: Map<number, RunLogGroup> | null = null;
  for (const event of events) {
    if (event.kind === 'round' && event.round !== null) {
      const group: RunLogGroup = { key: event.key, round: event, items: [], loose: [] };
      groups.push(group);
      byRound ??= new Map();
      byRound.set(event.round, group);
      continue;
    }
    const owner = event.round !== null ? byRound?.get(event.round) : undefined;
    if (owner) owner.items.push(event);
    else groups.push({ key: event.key, round: null, items: [], loose: [event] });
  }
  return groups;
}

/**
 * Agent Compute 运行详情（PRD v1.7 §7）。
 *
 * 顶部状态条（状态/目标/执行数/完成数/起止时间）→ 执行详情 / 运行记录 两个 Tab。
 * 执行详情：最终结果优先展示，左侧 Agent 列表切换查看状态/职责/尝试/阶段产出，
 * 右侧常驻提交时的 Agent 配置与任务进展。
 *
 * 这是一个整页布局，不再复用对话里的 440px 副屏——副屏的宽度、拖拽把手与
 * 「收起」语义都属于会话场景，塞进页面只会挤成一列。
 */
export default function AgentComputeRunPage() {
  const { runId = '' } = useParams<{ runId: string }>();
  const tenant = useMemo(() => resolveAgentswarmTenant(), []);

  // Identity is read synchronously from the stores, so a direct visit must
  // subscribe to them or the first render's empty snapshot sticks.
  useExternalAuthStore((state) => state.user);
  useWorkspaceStore((state) => state.identities);
  // (The subscriptions above are what matter here; this page reads its data
  // from the run API, not from the identity.)

  // The detail response carries the summary fields, so it seeds the header —
  // including on a cold visit where the list never ran.
  const [summary, setSummary] = useState<RunSummaryDTO | null>(null);

  // A run that is still moving is polled every few seconds so the status bar and
  // counts stay live; the hook stops itself once the run is terminal, so a
  // finished run costs nothing.
  const polling = useRunPolling({ tenant, runId, enabled: Boolean(runId) });
  useEffect(() => {
    if (polling.run) setSummary(polling.run);
  }, [polling.run]);

  const terminal = summary ? isTerminalStatus(summary.status) : false;
  const detail = useRunDetail({ tenant, runId, opened: Boolean(runId), terminal });

  // The detail response also carries the summary fields, so it seeds the header
  // on a cold visit before the first poll lands.
  useEffect(() => {
    if (detail.detail) setSummary(detail.detail);
  }, [detail.detail]);

  const [tab, setTab] = useState<Tab>('execution');
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  // Switching tabs re-reads the detail: each tab shows a different projection of
  // the same growing payload (agents/results vs rounds/attempts/guards), and a
  // user who moves tabs is asking to see the current state, not the snapshot
  // taken when the page opened. The first tab needs no read — the hook's own
  // open trigger already fetched it.
  const switchedTabs = useRef(false);
  useEffect(() => {
    if (!switchedTabs.current) {
      switchedTabs.current = true;
      return;
    }
    detail.reload();
    // reload is stable (useCallback with no deps); depending on `tab` alone keeps
    // this to one read per switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const agents = detail.detail?.agents ?? [];
  const groups = groupByRound(agents);
  const selectedAgent =
    agents.find((agent) => agent.agent_id === selectedAgentId) ?? agents[0] ?? null;

  const counts = useMemo(() => {
    const total = agents.length;
    const done = agents.filter((agent) => agent.status === 'COMPLETED').length;
    const running = agents.filter((agent) => agent.status === 'RUNNING').length;
    return { total, done, running };
  }, [agents]);

  const status = summary?.status;
  // Built from rounds + attempts + guards, ordered by time; see buildRunLog.
  const runLog = useMemo(
    () => (detail.detail ? buildRunLog(detail.detail, isTerminalStatus(status ?? '')) : []),
    [detail.detail, status],
  );
  const goal = detail.detail?.goal ?? summary?.goal_excerpt ?? '';

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="shrink-0 px-5 pt-6">
        <PageHeader
          title="运行详情"
          onBack={() => history.push(BASE)}
          actions={
            <Button variant="outline" size="sm" onClick={detail.reload} disabled={detail.phase === 'loading'}>
              <RotateCw className="mr-1 size-3.5" aria-hidden />
              刷新
            </Button>
          }
        />

        {/* 状态条：状态、目标、执行/完成数、起止时间——一屏内交代这次运行的全部要点。 */}
        <section
          aria-label="运行状态"
          className="mt-4 flex flex-col gap-2 rounded-xl border border-divider bg-card p-4"
        >
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <Badge tone={runStatusTone(status)}>
              {status ? runStatusLabel(status) : '读取中'}
            </Badge>
            <span className="text-muted-foreground">
              {`执行 ${counts.total} · 完成 ${counts.done} · 进行中 ${counts.running}`}
            </span>
            <span className="text-muted-foreground">
              开始 {formatRunTimeOrDash(summary?.started_at)}
            </span>
            <span className="text-muted-foreground">结束 {formatRunTimeOrDash(summary?.ended_at)}</span>
            <span className="font-mono text-xs text-muted-foreground">{runId}</span>
          </div>
          {goal ? (
            <p className="m-0 max-h-24 overflow-auto whitespace-pre-wrap break-words text-sm">
              {goal}
            </p>
          ) : null}
        </section>

        <div className="mt-4">
          <Segmented
            aria-label="运行详情分区"
            value={tab}
            onChange={(next) => setTab(next as Tab)}
            options={[
              { label: '执行详情', value: 'execution' },
              { label: '运行记录', value: 'log' },
            ]}
          />
        </div>
      </div>

      <main className="app-scrollbar mt-4 min-h-0 flex-1 overflow-y-auto px-5 pb-6">
        {detail.error ? (
          <p role="alert" className="m-0 text-sm text-destructive">
            {detail.error}
          </p>
        ) : !detail.detail && detail.phase === 'loading' ? (
          <p role="status" className="m-0 text-sm text-muted-foreground">
            正在读取运行详情…
          </p>
        ) : !detail.detail ? (
          <Empty title="没有找到这次运行" description="它可能已被清理，或不属于当前用户。" />
        ) : tab === 'execution' ? (
          <div className="flex flex-col gap-5">
            {/* 最终结果优先：它是大多数人次打开详情想先看到的东西。 */}
            <section className="flex flex-col gap-2">
              <h3 className="m-0 text-sm font-semibold">最终结果</h3>
              <div className="rounded-xl border border-divider bg-card p-4">
                <ValueBlock
                  value={detail.detail.final_answer}
                  label="结果"
                  empty="（还没有最终结果）"
                />
              </div>
            </section>

            {/* 主从布局：左选 Agent，右看它的产出；xl 以下折成单列。 */}
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-[300px_minmax(0,1fr)_320px]">
              <section className="flex flex-col gap-2" aria-label="执行 Agent 列表">
                <h3 className="m-0 text-sm font-semibold">执行 Agent</h3>
                {agents.length ? (
                  <div className="flex flex-col gap-3">
                    {/* Grouped by round: one entry can be dispatched again in a
                        later round, and two rows with the same catalog name read
                        as a duplicate until the round says otherwise. */}
                    {groups.map(({ round, items }) => (
                      <div key={round} className="flex flex-col gap-2">
                        {/* The label only earns its space when there is more than
                            one round to tell apart. */}
                        {groups.length > 1 ? (
                          <h4 className="m-0 text-[11px] font-medium text-muted-foreground">
                            第 {round + 1} 轮
                          </h4>
                        ) : null}
                        {items.map((agent) => (
                          <AgentListItem
                            key={agent.agent_id}
                            agent={agent}
                            selected={agent.agent_id === selectedAgent?.agent_id}
                            onSelect={() => setSelectedAgentId(agent.agent_id)}
                          />
                        ))}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="m-0 text-xs text-muted-foreground">这次运行还没有分配 Agent。</p>
                )}
              </section>

              <section aria-label="Agent 详情">
                {selectedAgent ? (
                  <AgentDetailPane agent={selectedAgent} />
                ) : (
                  <p className="m-0 text-xs text-muted-foreground">选择一个 Agent 查看其产出。</p>
                )}
              </section>

              <ProgressColumn detail={detail.detail} run={summary ?? detail.detail} />
            </div>
          </div>
        ) : (
          <section className="flex flex-col gap-4" aria-label="运行记录">
            <h3 className="m-0 text-sm font-semibold">运行记录</h3>
            {runLog.length ? (
              <ol className="m-0 flex list-none flex-col gap-3 p-0">
                {groupRunLog(runLog).map((group) =>
                  group.round ? (
                    <li key={group.key} className="rounded-xl border border-border bg-muted/40">
                      <RoundDisclosure
                        round={group.round}
                        count={group.items.length}
                        /*
                         * A round starts open: the reader came to see what happened,
                         * and defaulting to closed would hide the whole run behind
                         * clicks. Collapsing is for getting a long run out of the way.
                         */
                        defaultOpen
                      >
                        {group.items.map((event) => (
                          <li
                            key={event.key}
                            className="rounded-lg border border-divider bg-card px-4 py-3"
                          >
                            <RunLogEntry event={event} />
                          </li>
                        ))}
                      </RoundDisclosure>
                    </li>
                  ) : (
                    group.loose.map((event) => (
                      <li
                        key={event.key}
                        className="rounded-xl border border-warning/40 bg-warning/5 px-4 py-3"
                      >
                        <RunLogEntry event={event} />
                      </li>
                    ))
                  ),
                )}
              </ol>
            ) : (
              <p className="m-0 text-sm text-muted-foreground">还没有运行记录。</p>
            )}
          </section>
        )}
      </main>
    </div>
  );
}