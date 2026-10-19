import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { Spin } from '@/components/ui/Spin';
import { canCancelStep, canRetryStep, formatTime, isBotEvolutionTask, taskTypes } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { Plus, Search } from 'lucide-react';
import { useCallback, useState } from 'react';
import { Choice, ErrorBox, Hint, StatusBadge, TypeTag, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';

import { stepName, taskTimes } from './presentation';

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
  return (
    <div className="space-y-4">
      <ErrorBox message={identity.error ?? result.error} retry={result.reload} />
      <ErrorBox message={operationError} />
      <ErrorBox
        message={hiddenCount ? '任务列表返回了筛选范围外的数据，请确认后端已部署筛选参数支持后刷新。' : ''}
        retry={result.reload}
      />
      <form
        className="flex flex-wrap items-center gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(search.trim());
          setPage(1);
        }}
      >
        <div className="relative w-[240px] max-w-full">
          <Search size={14} className="pointer-events-none absolute left-3 top-2.5 text-muted-foreground" />
          <Input
            className="pl-9"
            aria-label="搜索任务"
            placeholder="任务名称 / ID"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="w-[180px]">
          <Choice
            label="任务分类"
            value={category}
            onChange={(v) => {
              setCategory(v);
              setPage(1);
            }}
            options={[
              { value: 'all', label: '全部分类' },
              { value: 'diagnosis', label: '诊断类' },
              { value: 'optimization', label: '优化类' },
              { value: 'full', label: '全流程类' },
            ]}
          />
        </div>
        <div className="w-[130px]">
          <Choice
            label="任务状态"
            value={status}
            onChange={(v) => {
              setStatus(v);
              setPage(1);
            }}
            options={[
              { value: 'all', label: '全部状态' },
              { value: 'running', label: '进行中' },
              { value: 'success', label: '已完成' },
              { value: 'failed', label: '失败/取消' },
            ]}
          />
        </div>
        <Button type="submit" variant="outline">
          查询
        </Button>
        <Button type="button" variant="ghost" onClick={result.reload}>
          刷新
        </Button>
        {onCreate && (
          <Button type="button" className="ml-auto" leftIcon={<Plus size={14} />} onClick={onCreate}>
            发起进化
          </Button>
        )}
      </form>
      <p className="text-xs text-muted-foreground">当前空间任务</p>
      {result.loading || identity.loading ? (
        <Spin tip="加载任务…" />
      ) : (
        result.data && (
          <>
            <div className="overflow-x-auto rounded-xl border border-border bg-card">
              <table className="w-full min-w-[1540px] table-fixed text-left text-sm">
                <colgroup>
                  {[240, 180, 160, 160, 120, 100, 200, 170, 180].map((width, i) => (
                    <col key={i} style={{ width }} />
                  ))}
                </colgroup>
                <thead className="bg-muted text-muted-foreground">
                  <tr>
                    {[
                      '任务名称',
                      '备注',
                      '任务类型',
                      '进化对象',
                      '状态',
                      '创建人',
                      '开始/结束时间',
                      '当前节点',
                      '操作',
                    ].map((v) => (
                      <th key={v} className="whitespace-nowrap p-3 font-medium">
                        {v}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleTasks.map((task) => {
                    const retryTarget = task.steps?.filter((candidate) => canRetryStep(task, candidate)).at(-1);
                    const cancelTarget = task.steps?.find((candidate) => canCancelStep(task, candidate));
                    const step = cancelTarget ?? retryTarget ?? task.steps?.at(-1);
                    const times = taskTimes(task);
                    const manage = isBotEvolutionTask(task);
                    return (
                      <tr key={task.task_id} className="border-t border-border">
                        <td className="p-3">
                          <Hint content={task.task_name}>
                            <span className="block truncate font-medium">{task.task_name}</span>
                          </Hint>
                          <Hint content={task.task_id}>
                            <span className="mt-1 block truncate text-xs text-muted-foreground">{task.task_id}</span>
                          </Hint>
                        </td>
                        <td className="p-3">
                          <Hint content={task.remark || undefined}>
                            <div className="truncate text-xs text-muted-foreground">{task.remark || '—'}</div>
                          </Hint>
                        </td>
                        <td className="whitespace-nowrap p-3">
                          <TypeTag type={task.task_type} />
                        </td>
                        <td className="p-3">
                          <Hint content={task.bot_id}>
                            <div className="truncate">{task.bot_id}</div>
                          </Hint>
                          <span className="block text-xs text-muted-foreground">Owner {task.user_id}</span>
                        </td>
                        <td className="p-3">
                          <StatusBadge status={task.status} dot />
                        </td>
                        <td className="p-3">{task.created_by}</td>
                        <td className="whitespace-nowrap p-3 text-xs">
                          {formatTime(times.start)}
                          <br />
                          {formatTime(times.end)}
                        </td>
                        <td className="p-3">{step ? stepName(step.stepType) : '—'}</td>
                        <td className="whitespace-nowrap p-3">
                          <div className="flex items-center gap-1">
                            <Hint
                              content={
                                isBotEvolutionTask(task) ? undefined : '该历史任务不在本期迁移范围，请使用原入口'
                              }
                            >
                              <Button
                                className="px-1 text-primary"
                                variant="ghost"
                                size="sm"
                                disabled={!isBotEvolutionTask(task)}
                                onClick={() =>
                                  history.push(`/evolution-factory/tasks/${encodeURIComponent(task.task_id)}`)
                                }
                              >
                                详情
                              </Button>
                            </Hint>
                            {retryTarget && manage && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="px-1 text-primary"
                                disabled={Boolean(busy)}
                                onClick={() =>
                                  void action(task.task_id, retryTarget.stepId, 'retry').catch(() => undefined)
                                }
                              >
                                重试
                              </Button>
                            )}
                            {cancelTarget && manage && (
                              <ConfirmDialog
                                title="停止当前步骤？"
                                description="取消当前 Step；已完成步骤和产物保留。"
                                onConfirm={() => action(task.task_id, cancelTarget.stepId, 'cancel')}
                              >
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="px-1 text-destructive"
                                  disabled={Boolean(busy)}
                                >
                                  停止
                                </Button>
                              </ConfirmDialog>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!visibleTasks.length && !hiddenCount && (
              <Empty title="暂无任务" description="可以发起一次 Bot 诊断或 Bench 诊断。" />
            )}
            {result.data.total > 0 && !hiddenCount && (
              <Pagination current={page} pageSize={20} total={result.data.total} onChange={setPage} />
            )}
          </>
        )
      )}
    </div>
  );
}
