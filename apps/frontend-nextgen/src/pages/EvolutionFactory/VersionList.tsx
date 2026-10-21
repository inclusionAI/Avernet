import { Button } from '@/components/ui/Button';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { Spin } from '@/components/ui/Spin';
import type { EvolutionVersion } from '@/domain/evolution/types';
import { activeStatus, formatTime, type Task } from '@/domain/evolution/types';
import { history, useLocation } from '@umijs/max';
import { Package, RefreshCw, Search } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ErrorBox, FilterSelect, ListSkeleton, PlaceholderPanel, TablePanel, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';
import { Comparison, stableVersion, VersionActions, versionLabel, versionStatus } from './VersionActions';
import { buildVersionColumns, COLUMN_WIDTHS } from './VersionListColumns';
import { VersionReview } from './VersionReview';
export { stableVersion, versionStatus } from './VersionActions';

export default function VersionList({ task, revision = 0 }: { task?: Task; revision?: number }) {
  const context = useEvolutionContext();
  const evolutionApi = context.api;
  const { search } = useLocation();
  const result = useResource(
    useCallback(
      () => evolutionApi.versions(task?.task_id),
      [context.key, task?.task_id, revision, task?.status, task?.steps?.length],
    ),
    context.ready,
  );
  const [query, setQuery] = useState(''),
    [filter, setFilter] = useState('all'),
    [botFilter, setBotFilter] = useState('all'),
    [page, setPage] = useState(1),
    [pageSize, setPageSize] = useState(20);
  const [review, setReview] = useState<EvolutionVersion>(),
    [diff, setDiff] = useState(''),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(false);
  const diffRevision = useRef(0);
  useEffect(() => {
    diffRevision.current += 1;
    const params = new URLSearchParams(search);
    const owner = params.get('botOwner'),
      bot = params.get('botId');
    setBotFilter(
      !task && params.get('space_id') === String(context.spaceId) && owner && bot
        ? JSON.stringify([owner, bot])
        : 'all',
    );
    setQuery('');
    setFilter('all');
    setPage(1);
    setReview(undefined);
    setDiff('');
  }, [context.key, task?.task_id, search]);
  const viewDiff = async (v: EvolutionVersion) => {
    const request = ++diffRevision.current;
    setReview(v);
    setDiff('');
    setError('');
    setLoading(true);
    if (!v.diff?.artifactAvailable) {
      setLoading(false);
      return;
    }
    try {
      const text = await evolutionApi.diff(v.taskId, v.stepId);
      if (request === diffRevision.current) setDiff(text);
    } catch (e) {
      if (request === diffRevision.current) setError(e instanceof Error ? e.message : 'Diff 加载失败');
    } finally {
      if (request === diffRevision.current) setLoading(false);
    }
  };
  const items = (result.data?.items ?? []).filter(
    (v) =>
      (botFilter === 'all' || JSON.stringify([v.userId, v.botId]) === botFilter) &&
      (filter === 'all' || v.kind === filter || v.acceptanceStatus === filter) &&
      [v.versionId, v.botId, v.taskId, v.userId, String(v.pack?.artifact?.sha256 ?? '')].some((value) =>
        value.toLowerCase().includes(query.toLowerCase()),
      ),
  );
  const stable = stableVersion(result.data?.items ?? []);
  const candidate = task?.steps?.filter((s) => s.stepType === 'optimize' && activeStatus(s.status)).at(-1);
  const reviewModal = (
    <VersionReview
      review={review}
      onClose={() => {
        diffRevision.current += 1;
        setReview(undefined);
      }}
      diff={diff}
      error={error}
      loading={loading}
    />
  );
  if (task)
    return (
      <aside className="min-w-0 space-y-3 rounded-xl border border-border bg-card p-4" aria-label="当前任务进化版本">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-semibold">进化版本</h3>
          <Button size="sm" variant="ghost" onClick={result.reload}>
            刷新
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">本任务稳定结果，不代表 Bot 当前已部署版本。</p>
        <ErrorBox message={result.error} retry={result.reload} />
        {result.loading ? (
          <Spin tip="加载版本…" />
        ) : (
          !result.error && (
            <>
              <div className="rounded-lg bg-muted/40 p-3 text-sm">
                稳定版本：<strong>{stable ? versionLabel(stable) : '尚未建立'}</strong>
              </div>
              {candidate && (
                <p className="text-xs text-muted-foreground">候选 R{candidate.roundNo} 正在执行，尚未形成稳定版本。</p>
              )}
              {!result.data?.items.length && (
                <p className="text-sm text-muted-foreground">当前任务尚无已登记进化版本。</p>
              )}
              {items.map((v) => (
                <div key={v.versionId} className="space-y-2 rounded-lg border border-border p-3">
                  <div className="flex flex-wrap justify-between gap-2 text-sm">
                    <strong>{versionLabel(v)}</strong>
                    <span>{versionStatus(v)}</span>
                  </div>
                  <Comparison version={v} />
                  <p className="text-xs text-muted-foreground">生成时间：{formatTime(v.createdAt)}</p>
                  <p className="text-xs text-muted-foreground">{v.pack ? 'Pack 已登记' : 'Pack 未登记'}</p>
                  <VersionActions version={v} onDiff={(v) => void viewDiff(v)} />
                </div>
              ))}
            </>
          )
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            const params = new URLSearchParams({
              tab: 'versions',
              space_id: String(context.spaceId),
              botOwner: task.user_id,
              botId: task.bot_id,
            });
            history.push(`/evolution-factory?${params}`);
          }}
        >
          查看全部进化版本
        </Button>
        {reviewModal}
      </aside>
    );
  const hasActiveFilter = Boolean(query) || filter !== 'all' || botFilter !== 'all';
  // 位于 task 早退分支之后，不能用 hook —— 直接随渲染推导。
  const botOptions = [
    { value: 'all', label: '全部' },
    ...[...new Map(
      (result.data?.items ?? []).map((v) => [
        JSON.stringify([v.userId, v.botId]),
        { value: JSON.stringify([v.userId, v.botId]), label: `${v.botId} / ${v.userId}` },
      ]),
    ).values()] as { value: string; label: string }[],
  ];
  // viewDiff 每渲染重建（依赖 refs 与 setState），故列定义不做 memo，直接随渲染生成。
  const columns: DataTableColumn<EvolutionVersion>[] = buildVersionColumns((v) => void viewDiff(v));
  return (
    <div className="min-w-0">
      {/* 筛选行：搜索 + 带 Bot / 版本类型下拉 + 右侧刷新与创建 Pack，与 Bot 管理工具行同 idiom。 */}
      <div className="mb-4 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-3">
        <div className="relative w-full sm:w-[240px] sm:shrink-0">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            aria-label="搜索进化版本"
            placeholder="版本、Bot、任务或 SHA"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <FilterSelect
          label="筛选 Bot"
          value={botFilter}
          onChange={(v) => {
            setBotFilter(v);
            setPage(1);
          }}
          options={botOptions}
          triggerClassName="w-full sm:w-[180px]"
        />
        <FilterSelect
          label="版本类型"
          value={filter}
          onChange={(value) => {
            setFilter(value);
            setPage(1);
          }}
          options={[
            { value: 'all', label: '全部' },
            { value: 'initial', label: '初始版本' },
            { value: 'accepted', label: '已接受' },
            { value: 'rejected', label: '未接受' },
            { value: 'snapshot', label: '主动快照' },
          ]}
        />
        <div className="ml-auto flex w-full items-center justify-end gap-2.5 sm:w-auto">
          <Button type="button" variant="outline" size="sm" leftIcon={<RefreshCw size={14} />} onClick={result.reload}>
            刷新
          </Button>
          <Button
            type="button"
            leftIcon={<Package size={14} />}
            onClick={() => history.push('/evolution-factory/new?type=pack')}
          >
            创建 Pack
          </Button>
        </div>
      </div>
      <ErrorBox message={context.error || result.error} retry={result.reload} />
      {result.loading || context.loading ? (
        <TablePanel>
          <ListSkeleton widths={COLUMN_WIDTHS} ariaLabel="进化版本加载中" />
        </TablePanel>
      ) : result.error ? (
        <PlaceholderPanel>
          <Empty
            title="进化版本加载失败"
            description={result.error}
            action={
              <Button type="button" variant="secondary" onClick={result.reload}>
                重试
              </Button>
            }
          />
        </PlaceholderPanel>
      ) : items.length ? (
        <>
          <TablePanel>
            <DataTable
              columns={columns}
              rows={items.slice((page - 1) * pageSize, page * pageSize)}
              getRowKey={(v) => v.versionId}
              rowHeight="h-18"
              ariaLabel="进化版本列表"
              minWidth="min-w-[1370px]"
            />
          </TablePanel>
          <Pagination
            current={page}
            pageSize={pageSize}
            total={items.length}
            onChange={setPage}
            onPageSizeChange={(value) => {
              setPageSize(value);
              setPage(1);
            }}
            layout="between"
            className="mt-4"
          />
        </>
      ) : (
        <PlaceholderPanel>
          <Empty
            title={hasActiveFilter ? '没有符合条件的进化版本' : '暂无进化版本'}
            description={hasActiveFilter ? '尝试清除搜索词或调整筛选条件。' : '发起一次进化任务，验证通过后即可沉淀稳定版本。'}
          />
        </PlaceholderPanel>
      )}
      {reviewModal}
    </div>
  );
}