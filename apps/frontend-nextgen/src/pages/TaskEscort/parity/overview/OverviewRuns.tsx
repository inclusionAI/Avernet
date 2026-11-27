import { Input } from '@/components/ui/Input';
import {
  overviewDuration,
  overviewStatuses,
  overviewStatusLabels,
  overviewTime,
} from '@/domain/taskEscortParity/overview';
import type {
  OverviewReadState,
  OverviewRunFilters,
  OverviewRuns as RunsData,
} from '@/domain/taskEscortParity/overviewTypes';
import React from 'react';
import {
  Button,
  Empty,
  OverviewSelect,
  OverviewStatus,
  ReadFeedback,
  UnavailableActions,
  writeUnavailable,
} from './OverviewShared';

export function OverviewRuns({
  state,
  filters,
  search,
  onSearch,
  onFilters,
  onSubmit,
  onOpen,
  runRefs,
}: {
  state: OverviewReadState<RunsData>;
  filters: OverviewRunFilters;
  search: string;
  onSearch: (value: string) => void;
  onFilters: (value: OverviewRunFilters) => void;
  onSubmit: () => void;
  onOpen: (id: string) => void;
  runRefs: React.MutableRefObject<Map<string, HTMLButtonElement>>;
}) {
  const pages = Math.max(1, Math.ceil((state.data?.total ?? 0) / 20));
  const reset = () => {
    onSearch('');
    onFilters({ page: 0, status: '', query: '', time: '7d' });
  };
  return (
    <>
      <form
        aria-label="运行记录筛选"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
        className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3"
      >
        <OverviewSelect
          label="运行时间范围"
          value={filters.time}
          onChange={(time) => onFilters({ ...filters, time, page: 0 })}
          options={[
            ['', '全部时间'],
            ['1d', '近 1 天'],
            ['3d', '近 3 天'],
            ['7d', '近 7 天'],
          ]}
        />
        <OverviewSelect
          label="运行状态"
          value={filters.status}
          onChange={(status) => onFilters({ ...filters, status, page: 0 })}
          options={[
            ['', '全部状态'],
            ...overviewStatuses.map((status): [string, string] => [status, overviewStatusLabels[status]]),
          ]}
        />
        <Input
          type="search"
          aria-label="搜索运行记录"
          placeholder="搜索输入内容 / Run ID / 发起方 / Bot ID"
          value={search}
          onChange={(event) => onSearch(event.target.value)}
          className="min-w-0 flex-1 basis-64 sm:max-w-sm"
        />
        <Button type="submit" size="sm">
          搜索
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={reset}
          disabled={!search && !filters.query && !filters.status && filters.time === '7d'}
        >
          重置筛选
        </Button>
        {state.data && (
          <span className="ml-auto text-xs text-muted-foreground" aria-live="polite">
            共 {state.data.total} 条
          </span>
        )}
      </form>
      <ReadFeedback state={state} label="运行列表" />
      {!state.loading &&
        !state.error &&
        (state.data?.runs.length ? (
          <div className="tcov-scroll">
            <table className="tcov-table" aria-label="运行列表">
              <thead>
                <tr>
                  {['Run ID', '状态', '节点', '发起方', '版本', '引擎', '开始时间', '耗时', '操作'].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {state.data.runs.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <Button
                        ref={(element) => {
                          if (element) runRefs.current.set(run.id, element);
                          else runRefs.current.delete(run.id);
                        }}
                        variant="link"
                        size="sm"
                        className="h-auto px-0 font-mono text-xs"
                        onClick={() => onOpen(run.id)}
                      >
                        {run.id}
                      </Button>
                    </td>
                    <td>
                      <OverviewStatus status={run.status} />
                    </td>
                    <td className="text-muted-foreground">
                      {run.nodeCount === null || run.nodeCount === undefined
                        ? '—'
                        : `${run.succeededCount ?? '—'} 成功 / ${run.failedCount ?? '—'} 失败 / ${run.nodeCount} 总计`}
                    </td>
                    <td>
                      <span className="font-mono">{run.initiator || '—'}</span>
                      {run.botId && (
                        <span className="mt-1 block max-w-52 whitespace-normal break-all text-[10px] text-muted-foreground">
                          {run.botId}
                          {run.pluginVersion ? ` / ${run.pluginVersion}` : ''}
                        </span>
                      )}
                    </td>
                    <td>{run.version === -1 ? '未绑定发布版本' : run.version ?? '—'}</td>
                    <td>{run.engine || '—'}</td>
                    <td>{overviewTime(run.startedAt)}</td>
                    <td>{overviewDuration(run.durationMs)}</td>
                    <td>
                      <UnavailableActions runId={run.id} compact />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            compact
            title={filters.query || filters.status ? '没有匹配的运行' : '暂无运行'}
            description={
              filters.query || filters.status
                ? '仅展示真实接口结果；可调整时间、状态或搜索条件。'
                : '运行记录由执行引擎执行工作流后产生；执行一次工作流后即可在此查看。'
            }
          />
        ))}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3">
        <p className="text-xs text-muted-foreground">
          每页 20 条 · 第 {filters.page + 1} / {pages} 页
        </p>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={filters.page === 0 || state.loading}
            onClick={() => onFilters({ ...filters, page: filters.page - 1 })}
          >
            上一页
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!state.data || state.loading || filters.page + 1 >= pages}
            onClick={() => onFilters({ ...filters, page: filters.page + 1 })}
          >
            下一页
          </Button>
        </div>
      </div>
      <p className="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">{writeUnavailable}</p>
    </>
  );
}
