import { Button } from '@/components/ui/Button';
import { DataTable } from '@/components/ui/DataTable';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { isBotEvolutionTask, taskTypes } from '@/domain/evolution/types';
import { Plus, RefreshCw, Search } from 'lucide-react';
import { useCallback, useState } from 'react';
import { COLUMN_WIDTHS, useTaskListColumns } from './TaskListColumns';
import { ErrorBox, FilterSelect, ListSkeleton, PlaceholderPanel, TablePanel, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';

export default function TaskList({ onCreate }: { onCreate?: () => void }) {
  const identity = useEvolutionContext();
  const evolutionApi = identity.api;
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [status, setStatus] = useState('all');
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState('');
  const [operationError, setOperationError] = useState('');
  const load = useCallback(
    () =>
      evolutionApi.tasks({
        query,
        category,
        status,
        page,
        pageSize: 20,
        taskTypes: taskTypes.map((item) => item.type).join(','),
        targetType: 'bot',
      }),
    [query, category, status, page, identity.key],
  );
  const result = useResource(load, identity.ready);
  const visibleTasks = result.data?.tasks.filter(isBotEvolutionTask) ?? [];
  const hiddenCount = (result.data?.tasks.length ?? 0) - visibleTasks.length;
  const hasActiveFilter = Boolean(query) || category !== 'all' || status !== 'all';
  const action = async (taskId: string, stepId: string, operation: 'retry' | 'cancel') => {
    if (busy) return;
    setBusy(taskId);
    setOperationError('');
    try {
      await evolutionApi.stepAction(taskId, stepId, operation);
      result.reload();
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : '操作失败');
      throw error;
    } finally {
      setBusy('');
    }
  };
  const columns = useTaskListColumns(busy, action);
  return (
    <div className="min-w-0">
      <ErrorBox message={identity.error ?? result.error} retry={result.reload} />
      <ErrorBox message={operationError} />
      <ErrorBox
        message={hiddenCount ? '任务列表返回了筛选范围外的数据，请确认后端已部署筛选参数支持后刷新。' : ''}
        retry={result.reload}
      />
      {/* 筛选行：与 Bot 管理工具行同一 idiom —— 搜索框 + 带常驻小标题的下拉 + 右侧重置/操作。 */}
      <form
        className="mb-4 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(search.trim());
          setPage(1);
        }}
      >
        <div className="relative w-full sm:w-[240px] sm:shrink-0">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            aria-label="搜索任务"
            placeholder="任务名称 / ID"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              // 清空即恢复全量，无需再敲回车；有内容时回车提交
              if (!e.target.value && query) {
                setQuery('');
                setPage(1);
              }
            }}
          />
        </div>
        <FilterSelect
          label="任务分类"
          value={category}
          onChange={(v) => {
            setCategory(v);
            setPage(1);
          }}
          options={[
            { value: 'all', label: '全部' },
            { value: 'diagnosis', label: '诊断类' },
            { value: 'optimization', label: '优化类' },
            { value: 'full', label: '全流程类' },
          ]}
        />
        <FilterSelect
          label="任务状态"
          value={status}
          onChange={(v) => {
            setStatus(v);
            setPage(1);
          }}
          options={[
            { value: 'all', label: '全部' },
            { value: 'running', label: '进行中' },
            { value: 'success', label: '已完成' },
            { value: 'failed', label: '失败/取消' },
          ]}
        />
        <div className="ml-auto flex w-full items-center justify-end gap-2.5 sm:w-auto">
          {hasActiveFilter ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearch('');
                setQuery('');
                setCategory('all');
                setStatus('all');
                setPage(1);
              }}
            >
              重置
            </Button>
          ) : null}
          <Button type="button" variant="outline" size="sm" leftIcon={<RefreshCw size={14} />} onClick={result.reload}>
            刷新
          </Button>
          {onCreate ? (
            <Button type="button" leftIcon={<Plus size={14} />} onClick={onCreate}>
              发起进化
            </Button>
          ) : null}
        </div>
      </form>
      {result.loading || identity.loading ? (
        <TablePanel>
          <ListSkeleton widths={COLUMN_WIDTHS} ariaLabel="任务列表加载中" />
        </TablePanel>
      ) : (
        result.data && (
          <>
            <TablePanel>
              <DataTable
                columns={columns}
                rows={visibleTasks}
                getRowKey={(task) => task.task_id}
                rowHeight="h-18"
                ariaLabel="进化任务列表"
                minWidth="min-w-[1320px]"
              />
            </TablePanel>
            {!visibleTasks.length && !hiddenCount ? (
              <PlaceholderPanel className="mt-3">
                <Empty
                  title={hasActiveFilter ? '没有符合条件的任务' : '当前空间暂无进化任务'}
                  description={
                    hasActiveFilter
                      ? '尝试清除搜索词或调整筛选条件。'
                      : '发起一次 Bot 诊断或 Bench 诊断，量化当前能力并定位改进点。'
                  }
                  action={onCreate ? <Button onClick={onCreate}>发起进化</Button> : undefined}
                />
              </PlaceholderPanel>
            ) : null}
            {result.data.total > 0 && !hiddenCount ? (
              <Pagination
                current={page}
                pageSize={20}
                total={result.data.total}
                onChange={setPage}
                layout="between"
                className="mt-4"
              />
            ) : null}
          </>
        )
      )}
    </div>
  );
}