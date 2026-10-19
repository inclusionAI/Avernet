import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Spin } from '@/components/ui/Spin';
import type { EvolutionVersion } from '@/domain/evolution/types';
import { activeStatus, formatTime, type Task } from '@/domain/evolution/types';
import { history, useLocation } from '@umijs/max';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Choice, ErrorBox, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';
import { Comparison, stableVersion, VersionActions, versionLabel, versionStatus } from './VersionActions';
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
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <Input
          className="max-w-[440px]"
          value={query}
          placeholder="版本、Bot、任务或 SHA"
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(1);
          }}
        />
        <div className="w-full sm:w-[240px]">
          <Choice
            label="筛选 Bot"
            value={botFilter}
            onChange={(value) => {
              setBotFilter(value);
              setPage(1);
            }}
            options={[
              { value: 'all', label: '全部 Bot' },
              ...[
                ...new Map(
                  (result.data?.items ?? []).map((v) => [
                    JSON.stringify([v.userId, v.botId]),
                    { value: JSON.stringify([v.userId, v.botId]), label: `${v.botId} / ${v.userId}` },
                  ]),
                ).values(),
              ],
            ]}
          />
        </div>
        <div className="w-full sm:w-[160px]">
          <Choice
            label="版本类型"
            value={filter}
            onChange={(value) => {
              setFilter(value);
              setPage(1);
            }}
            options={[
              { value: 'all', label: '全部版本' },
              { value: 'initial', label: '初始版本' },
              { value: 'accepted', label: '已接受' },
              { value: 'rejected', label: '未接受' },
              { value: 'snapshot', label: '主动快照' },
            ]}
          />
        </div>
        <Button className="ml-auto" variant="outline" onClick={result.reload}>
          刷新
        </Button>
        <Button onClick={() => history.push('/evolution-factory/new?type=pack')}>创建 Pack</Button>
      </div>
      <ErrorBox message={context.error || result.error} retry={result.reload} />
      {result.loading ? (
        <Spin tip="加载进化版本…" />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[1000px] text-left text-sm">
            <thead className="bg-muted/40">
              <tr>
                {['版本', 'Bot / Owner', '来源任务', '状态', 'Test Bench', 'Review / Spec', 'Pack', '时间', '操作'].map(
                  (t) => (
                    <th className={`whitespace-nowrap p-3 ${t === '操作' ? 'sticky right-0 bg-muted' : ''}`} key={t}>
                      {t}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {items.slice((page - 1) * pageSize, page * pageSize).map((v) => (
                <tr key={v.versionId} className="border-t border-border">
                  <td className="p-3">{versionLabel(v)}</td>
                  <td className="max-w-[200px] break-all p-3">
                    {v.botId}
                    <p className="text-xs text-muted-foreground">{v.userId}</p>
                  </td>
                  <td className="max-w-[240px] break-all p-3">
                    <Button
                      variant="ghost"
                      style={{ height: 'auto', whiteSpace: 'normal' }}
                      className="text-primary"
                      onClick={() => history.push(`/evolution-factory/tasks/${encodeURIComponent(v.taskId)}`)}
                    >
                      {String(v.taskName || v.taskId)}
                    </Button>
                  </td>
                  <td className="whitespace-nowrap p-3">{versionStatus(v)}</td>
                  <td className="p-3">
                    <Comparison version={v} />
                  </td>
                  <td className="p-3">
                    {v.reviewStatus || '—'} / {v.specVersion || '—'}
                  </td>
                  <td className="max-w-[140px] break-all p-3 text-xs">{v.pack?.packId || '未登记'}</td>
                  <td className="whitespace-nowrap p-3 text-xs">{formatTime(v.createdAt)}</td>
                  <td className="sticky right-0 min-w-[180px] bg-card p-3">
                    <VersionActions version={v} onDiff={(v) => void viewDiff(v)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!items.length && !result.error && <p className="p-6 text-center text-muted-foreground">暂无进化版本</p>}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-end gap-3 text-xs text-muted-foreground">
        <span>
          共 {items.length} 条 · 第 {page} 页
        </span>
        <div className="w-[110px]">
          <Choice
            label="每页条数"
            value={String(pageSize)}
            onChange={(v) => {
              setPageSize(Number(v));
              setPage(1);
            }}
            options={[10, 20, 50].map((n) => ({ value: String(n), label: `${n}条/页` }))}
          />
        </div>
        <Button size="sm" variant="outline" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
          上一页
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={page * pageSize >= items.length}
          onClick={() => setPage((p) => p + 1)}
        >
          下一页
        </Button>
      </div>
      {reviewModal}
    </div>
  );
}
