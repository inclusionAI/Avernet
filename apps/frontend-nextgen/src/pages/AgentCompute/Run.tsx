import { PageHeader } from '@/components/Common/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Segmented } from '@/components/ui/Segmented';
import { formatRunTimeOrDash } from '@/components/agentswarm/formatRunTime';
import { ValueBlock } from '@/components/agentswarm/ValueBlock';
import { useRunDetail } from '@/hooks/useRunDetail';
import { useRunPolling } from '@/hooks/useRunPolling';
import { resolveAgentswarmTenant } from '@/services/agentswarm/tenant';
import {
  isTerminalStatus,
  runStatusLabel,
  runStatusTone,
  type AgentDetailDTO,
  type RunDetailDTO,
  type RunSummaryDTO,
} from '@/services/agentswarm/types';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useParams } from '@umijs/max';
import { RotateCw } from 'lucide-react';
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
  const tone = runStatusTone(agent.status);
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
        <Badge tone={tone}>{runStatusLabel(agent.status)}</Badge>
      </span>
      {agent.role ? (
        <span className="truncate text-xs text-muted-foreground">{agent.role}</span>
      ) : null}
      <span className="text-xs text-muted-foreground">尝试 {agent.attempts} 次</span>
    </button>
  );
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
        <ValueBlock value={agent.output} label="产出" empty="（该 Agent 还没有产出）" />
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

      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-sm font-semibold">已提交配置</h3>
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">目标</dt>
          <dd className="m-0 whitespace-pre-wrap break-words">{detail?.goal ?? run.goal_excerpt}</dd>
          <dt className="text-muted-foreground">服务</dt>
          <dd className="m-0">{detail?.provider || '—'}</dd>
          {(detail?.agent_catalog ?? []).map((spec) => (
            <span key={spec.name} className="contents">
              <dt className="text-muted-foreground">Agent</dt>
              <dd className="m-0">
                {spec.name}
                {spec.role ? ` · ${spec.role}` : ''}
                {spec.model ? ` · ${spec.model}` : ''}
              </dd>
            </span>
          ))}
        </dl>
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
}

function buildRunLog(detail: RunDetailDTO, runFinished: boolean): RunLogEvent[] {
  const events: RunLogEvent[] = [];

  for (const round of detail.rounds ?? []) {
    events.push({
      key: `round-${round.round_id}`,
      at: round.started_at ?? null,
      kind: 'round',
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
        label: `${agent.name} 第 ${attempt.slice_retry_index} 次尝试`,
        detail: [attempt.executor_name || attempt.executor_id, attempt.result_status]
          .filter(Boolean)
          .join(' · '),
      });
    }
  }

  for (const guard of detail.guards ?? []) {
    events.push({
      key: `guard-${guard.event_id}`,
      at: guard.created_at ?? null,
      kind: 'guard',
      label: `运行保护触发：${guard.kind}`,
      detail: '',
    });
  }

  // Oldest first; entries with no timestamp sink to the bottom rather than
  // pretending to be epoch zero.
  //
  // A round opens the work it contains, but its row is written just after the
  // first agent's attempt row, so strict time ordering put 第 2 次尝试 above
  // 第 1 轮编排开始. Ordering by kind first — round, then attempts, then guards
  // — gives the sequence a reader expects while staying chronological inside
  // each kind.
  const kindRank: Record<RunLogEvent['kind'], number> = {
    round: 0,
    attempt: 1,
    guard: 2,
  };
  return events.sort((a, b) => {
    if (a.kind !== b.kind) return kindRank[a.kind] - kindRank[b.kind];
    if (a.at === null && b.at === null) return 0;
    if (a.at === null) return 1;
    if (b.at === null) return -1;
    return a.at - b.at;
  });
}

/**
 * Agent Compute 运行详情（PRD v1.7 §7）。
 *
 * 顶部状态条（状态/目标/执行数/完成数/起止时间）→ 执行详情 / 运行记录 两个 Tab。
 * 执行详情：最终结果优先展示，左侧 Agent 列表切换查看状态/职责/尝试/阶段产出，
 * 右侧常驻任务进展与已提交配置。
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
                  <div className="flex flex-col gap-2">
                    {agents.map((agent) => (
                      <AgentListItem
                        key={agent.agent_id}
                        agent={agent}
                        selected={agent.agent_id === selectedAgent?.agent_id}
                        onSelect={() => setSelectedAgentId(agent.agent_id)}
                      />
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
          <section className="flex flex-col gap-3" aria-label="运行记录">
            <h3 className="m-0 text-sm font-semibold">运行记录</h3>
            {runLog.length ? (
              <ol className="m-0 flex list-none flex-col p-0">
                {runLog.map((event) => (
                  <li
                    key={event.key}
                    className="flex gap-3 border-l-2 border-divider py-2 pl-3 last:border-transparent"
                  >
                    {/* Width fits yyyy-MM-dd HH:mm:ss (19 chars) at text-xs
                        mono; `w-32` was sized for the minute-precision form and
                        let the label collide with the timestamp. */}
                    <span className="w-44 shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                      {formatRunTimeOrDash(event.at)}
                    </span>
                    <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2">
                      <span className="text-sm">{event.label}</span>
                      {event.detail ? (
                        <span className="text-xs text-muted-foreground">{event.detail}</span>
                      ) : null}
                    </span>
                  </li>
                ))}
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