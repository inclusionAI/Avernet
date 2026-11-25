import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Empty } from '@/components/ui/Empty';
import { Pagination } from '@/components/ui/Pagination';
import { Segmented } from '@/components/ui/Segmented';
import { useRunList } from '@/hooks/useRunList';
import { resolveAgentswarmTenant } from '@/services/agentswarm/tenant';
import { runStatusLabel } from '@/services/agentswarm/types';
import { history } from '@umijs/max';
import { ArrowDown, ArrowUp, ArrowUpDown, Plus } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

const BASE = '/agent-compute';

type SortKey = 'id' | 'run_id' | 'status' | 'started_at' | 'ended_at';
type SortDir = 'asc' | 'desc';

/**
 * The gateway reports epoch **milliseconds**, but accept seconds too so an
 * older payload cannot render as an invalid date.
 */
// A cached formatter: `toLocaleString` rebuilds its options each call and is
// ~60x slower than one `Intl.DateTimeFormat` reused across cells.
/**
 * `yyyy-MM-dd HH:mm`, the Java SimpleDateFormat pattern used elsewhere in this
 * product. Built from parts on purpose: handing the locale the job lets it pick
 * its own separators (zh-CN gives `2026/11/11 10:56`), and `hourCycle: 'h23'`
 * alone does not guarantee a 24-hour clock across engines.
 */
const TIME_FORMAT = new Intl.DateTimeFormat('en-CA', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

function formatTime(value: number | null | undefined): string {
  if (!value) return '—';
  const millis = value > 10_000_000_000 ? value : value * 1000;
  const date = new Date(millis);
  if (Number.isNaN(date.getTime())) return '—';
  const parts = TIME_FORMAT.formatToParts(date);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}



/**
 * Agent Compute 主页面 = 运行列表（PRD v1.7 §1）。
 *
 * 入口为实验室 →「计算任务」。列顺序：ID → 编号 → 状态 → 目标（单行截断）→
 * 开始 → 结束 → 操作。表头统一两字，列宽固定，避免 w-full 把余量塞给某一列。
 * 分页走网关的 page/page_size；网关不支持排序参数，
 * 因此当前页按所选列在本地排序。
 */
export default function AgentComputeListPage() {
  const tenant = useMemo(() => resolveAgentswarmTenant(), []);
  const [query, setQuery] = useState('');
  // 输入框即时回显，但只在停顿 300ms 后检索：否则每敲一个字就发一次请求。
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState<'all' | 'running' | 'terminal'>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  // 默认按行 id 倒序：最新提交的排在最前，与网关的默认顺序一致。
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'id', dir: 'desc' });
  useEffect(() => {
    const timer = setTimeout(() => {
      setKeyword(query.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  // 搜索、排序、分页全部由网关承担：只有这样跨页排序才是真的，而不是只排当前页。
  const { runs, total, loaded, error } = useRunList({
    tenant,
    enabled: true,
    page,
    pageSize,
    sort: sort.key,
    order: sort.dir,
    keyword: keyword || undefined,
  });

  // 运行中/已结束是本地分流，因为网关的 status 是七态枚举而不是这两段；
  // 该分流只作用于当前页，与分页语义一致（总数仍以网关为准）。
  const visible = useMemo(() => {
    if (status === 'all') return runs;
    return runs.filter((run) =>
      status === 'running' ? run.status === 'RUNNING' : run.status !== 'RUNNING',
    );
  }, [runs, status]);

  const hasAnyRun = total > 0;

  const toggleSort = (key: SortKey) => {
    setSort((current) =>
      current.key === key
        ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: 'asc' },
    );
  };

  const sortIcon = (key: SortKey) => {
    if (sort.key !== key) return <ArrowUpDown className="size-3.5 opacity-50" aria-hidden />;
    return sort.dir === 'asc' ? (
      <ArrowUp className="size-3.5" aria-hidden />
    ) : (
      <ArrowDown className="size-3.5" aria-hidden />
    );
  };

  const header = (key: SortKey, label: string, width?: string, align?: 'right') => (
    <th
      className={['whitespace-nowrap px-4 py-2 font-medium', width, align === 'right' ? 'text-right' : '']
        .filter(Boolean)
        .join(' ')}
    >
      <button
        type="button"
        onClick={() => toggleSort(key)}
        className="inline-flex items-center gap-1 whitespace-nowrap hover:text-foreground"
        aria-label={`按${label}排序`}
        aria-sort={sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
      >
        {label}
        {sortIcon(key)}
      </button>
    </th>
  );

  return (
    <div className="flex h-full flex-col bg-background">
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col gap-5 px-5 py-6">
          <PageHeader
            title="计算任务"
            description="定义一个或多个执行 Agent，协同完成批量计算。"
            actions={
              <Button onClick={() => history.push(`${BASE}/new`)}>
                <Plus className="mr-2 size-4" aria-hidden />
                新建计算任务
              </Button>
            }
          />

          <div className="flex flex-wrap items-center gap-3">
            <Input
              aria-label="搜索计算任务"
              className="max-w-sm"
              placeholder="搜索计算目标或任务编号"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Segmented
              aria-label="筛选任务状态"
              value={status}
              onChange={(next) => {
                setStatus(next as 'all' | 'running' | 'terminal');
                setPage(1);
              }}
              options={[
                { label: '全部状态', value: 'all' },
                { label: '执行中', value: 'running' },
                { label: '已结束', value: 'terminal' },
              ]}
            />
          </div>

          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}

          {!loaded ? (
            <p role="status" className="text-sm text-muted-foreground">
              正在加载计算任务…
            </p>
          ) : !visible.length ? (
            <Empty
              title={hasAnyRun ? '没有匹配的计算任务' : '暂无计算任务'}
              description={
                hasAnyRun ? '请调整搜索内容或状态筛选。' : '新建任务，配置执行 Agent 后开始计算。'
              }
              action={
                hasAnyRun ? undefined : (
                  <Button variant="outline" onClick={() => history.push(`${BASE}/new`)}>
                    新建计算任务
                  </Button>
                )
              }
            />
          ) : (
            <div className="overflow-hidden rounded-xl border border-divider">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-left text-muted-foreground">
                  <tr>
                    {header('id', 'ID', 'w-20')}
                    {header('run_id', '编号', 'w-44')}
                    {header('status', '状态', 'w-28 min-w-28')}
                    <th className="w-80 max-w-80 px-4 py-2 font-medium">目标</th>
                    {header('started_at', '开始', 'w-36 min-w-36')}
                    {header('ended_at', '结束', 'w-36 min-w-36')}
                    <th className="w-16 min-w-16 px-2 py-2 text-center font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((run) => (
                    <tr key={run.run_id} className="border-t border-divider">
                      <td className="w-20 px-4 py-3 font-mono text-xs text-muted-foreground">
                        {run.row_id ?? '—'}
                      </td>
                      <td className="w-44 truncate px-4 py-3 font-mono text-xs text-muted-foreground">
                        <span title={run.run_id}>{run.run_id}</span>
                      </td>
                      <td className="w-28 min-w-28 whitespace-nowrap px-4 py-3">{runStatusLabel(run.status)}</td>
                      {/* 目标只是一段摘要（网关把 goal 截断到 160 字），单元格单行截断。
                          悬停给出可读的多行摘要，并说明完整目标在详情页——列表接口不含全文。 */}
                      <td className="w-80 max-w-80 px-4 py-3 font-medium">
                        {/* A native title, not the Radix Tooltip: one Radix root
                            per row meant refs, state and a getBoundingClientRect
                            (a forced reflow) on every pointer leave, which made
                            scrolling janky. The row only carries a 160-char
                            excerpt anyway, so the full text is still the detail
                            page's job. */}
                        <span
                          className="block cursor-default truncate"
                          title={run.goal_excerpt?.trim() || undefined}
                        >
                          {run.goal_excerpt?.trim() || '（无目标摘要）'}
                        </span>
                      </td>
                      <td className="w-36 min-w-36 whitespace-nowrap px-2 py-3 text-muted-foreground">
                        {formatTime(run.started_at)}
                      </td>
                      <td className="w-36 min-w-36 whitespace-nowrap px-2 py-3 text-muted-foreground">
                        {formatTime(run.ended_at)}
                      </td>
                      <td className="w-16 min-w-16 whitespace-nowrap px-2 py-3 text-center">
                        {/* Centered column: the ghost button's own padding is
                            symmetric, so no negative-margin correction is needed. */}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => history.push(`${BASE}/${encodeURIComponent(run.run_id)}`)}
                        >
                          查看
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {loaded && total > 0 ? (
            <Pagination
              current={page}
              pageSize={pageSize}
              total={total}
              onChange={setPage}
              onPageSizeChange={(next) => {
                setPageSize(next);
                setPage(1);
              }}
              layout="between"
            />
          ) : null}
        </div>
      </main>
    </div>
  );
}