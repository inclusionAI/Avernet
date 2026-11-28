import { Skeleton } from '@/components/ui/Skeleton';
import { overviewListQuery, readOverviewFilters } from '@/domain/taskEscortParity/overview';
import type { OverviewDays } from '@/domain/taskEscortParity/overviewTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortOverview } from '@/hooks/taskEscortParity/useTaskEscortOverview';
import { useEffect, useRef, useState } from 'react';
import { NodeAnalysisPanel } from './NodeAnalysisPanel';
import { OverviewMetrics, OverviewRisk } from './OverviewMetrics';
import { OverviewRuns } from './OverviewRuns';
import { Button, Empty, OverviewTabs } from './OverviewShared';
import { OverviewTrends } from './OverviewTrends';
import { RunDetail } from './RunDetail';
import { CallbackCompensation } from './callbackCompensation/CallbackCompensation';
import './overview.css';

function OverviewContent({ context }: { context: TaskEscortParityContext }) {
  const storageKey = `task-escort:overview:${context.scopeKey}:${context.userId ?? ''}:${context.workflowId}`;
  const [initial] = useState(() => {
    let saved: string | null = null;
    try {
      saved = sessionStorage.getItem(storageKey);
    } catch {
      /* Storage is optional. */
    }
    return readOverviewFilters(context.query, saved);
  });
  const [filters, setFilters] = useState(initial.filters),
    [search, setSearch] = useState(initial.search);
  const committedQueryKey = JSON.stringify([
    context.query.runQuery,
    context.query.runStatus,
    context.query.runRange ?? context.query.runTime,
    context.query.runPage,
  ]);
  const [previousQueryKey, setPreviousQueryKey] = useState(committedQueryKey);
  // History navigation is authoritative, but runId-only transitions must retain the local list/draft.
  // Synchronize before committing so no request or frame can use the previous history entry's filters.
  if (previousQueryKey !== committedQueryKey) {
    const restored = readOverviewFilters(context.query);
    setPreviousQueryKey(committedQueryKey);
    setFilters(restored.filters);
    setSearch(restored.search);
  }
  const [days, setDays] = useState<OverviewDays>('7'),
    [tab, setTab] = useState<'runs' | 'nodes'>('runs'),
    [nodeFilter, setNodeFilter] = useState('');
  const state = useTaskEscortOverview(context, days, filters);
  const runRefs = useRef(new Map<string, HTMLButtonElement>()),
    lastRun = useRef<string | null>(null);
  const runId = context.query.runId;
  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(overviewListQuery(filters, search)));
    } catch {
      /* Read UI also works without storage. */
    }
  }, [storageKey, filters, search]);
  useEffect(() => {
    if (runId) {
      lastRun.current = runId;
      return;
    }
    if (lastRun.current && runRefs.current.has(lastRun.current)) {
      runRefs.current.get(lastRun.current)?.focus();
      lastRun.current = null;
    }
  }, [runId, state.runs.data]);
  useEffect(() => {
    if (!state.runs.data || state.runs.loading || state.runs.error) return;
    const lastPage = Math.max(0, Math.ceil(state.runs.data.total / 20) - 1);
    if (filters.page > lastPage) setFilters((current) => ({ ...current, page: lastPage }));
  }, [state.runs.data, state.runs.loading, state.runs.error, filters.page]);
  const navigate = (runId?: string) =>
    context.navigate('runs', {
      ...context.query,
      ...overviewListQuery(filters, search),
      workflowId: context.workflowId,
      runId,
    });
  return (
    <div className="tcov min-w-0">
      {runId ? (
        <RunDetail key={runId} context={context} runId={runId} onBack={() => navigate()} />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap justify-end gap-1" aria-label="概览时间范围">
            {(
              [
                ['today', '今天'],
                ['yesterday', '昨天'],
                ['7', '7天'],
                ['30', '30天'],
              ] as const
            ).map(([value, label]) => (
              <Button
                key={value}
                size="sm"
                variant={days === value ? 'default' : 'outline'}
                aria-pressed={days === value}
                onClick={() => setDays(value)}
              >
                {label}
              </Button>
            ))}
          </div>
          <OverviewMetrics metrics={state.metrics} health={state.health} days={days} />
          <div className="tcov-trends">
            <OverviewTrends state={state.trend} days={days} />
            <OverviewRisk
              state={state.health}
              onNode={(id) => {
                setNodeFilter(id);
                setTab('nodes');
              }}
            />
          </div>
          <section className="tcov-card">
            <div className="flex flex-wrap items-center justify-between border-b border-border pr-4">
              <OverviewTabs
                label="概览内容"
                value={tab}
                options={[
                  ['runs', '运行列表'],
                  ['nodes', '节点分析'],
                ]}
                onChange={setTab}
              />
              <div className="flex flex-wrap items-center gap-2">
                <CallbackCompensation context={context} visible={tab === 'runs'} />
                <Button
                  variant="outline"
                  size="sm"
                  disabled={state.runs.loading || state.metrics.loading}
                  onClick={state.refresh}
                >
                  刷新
                </Button>
              </div>
            </div>
            {tab === 'runs' ? (
              <OverviewRuns
                state={state.runs}
                filters={filters}
                search={search}
                onSearch={setSearch}
                onFilters={setFilters}
                onSubmit={() => setFilters((current) => ({ ...current, query: search.trim(), page: 0 }))}
                onOpen={(id) => {
                  lastRun.current = id;
                  navigate(id);
                }}
                runRefs={runRefs}
              />
            ) : (
              <NodeAnalysisPanel state={state.nodes} filter={nodeFilter} onFilter={setNodeFilter} />
            )}
          </section>
        </div>
      )}
    </div>
  );
}

/** Shell owns the page header and workflow selector. This leaf owns only workflow-scoped content. */
export function OverviewPage({ context }: { context: TaskEscortParityContext }) {
  if (context.error)
    return (
      <div role="alert" className="space-y-3 rounded-lg border border-destructive/20 p-4 text-destructive">
        <p>{context.error}</p>
        <Button variant="outline" onClick={context.refreshWorkflows}>
          重试工作流
        </Button>
      </div>
    );
  if (context.loading)
    return (
      <div role="status" aria-label="工作流加载中">
        <Skeleton.Block className="h-40 w-full" />
      </div>
    );
  if (!context.ready || !context.workflowId)
    return <Empty title="请选择真实工作流" description="运行概览与详情始终限定在当前工作流。" />;
  return (
    <OverviewContent key={`${context.scopeKey}:${context.userId ?? ''}:${context.workflowId}`} context={context} />
  );
}
export default OverviewPage;
