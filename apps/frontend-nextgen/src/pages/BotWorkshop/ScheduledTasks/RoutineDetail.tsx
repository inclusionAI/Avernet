import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Pagination } from '@/components/ui/Pagination';
import type { createRoutineClient } from '@/services/botScheduledTasks/service';
import { describeFailure, runStatusLabel } from '@/services/botScheduledTasks/results';
import {
  stageNames,
  type RoutineAddress,
  type ManagedRoutine,
  type RoutineContext,
  type RoutineRun,
  type RuntimePage,
} from '@/services/botScheduledTasks/types';
import { getRoutineScheduleLabel } from '@/services/botWorkshop/routineSchedule';
import { useEffect, useState, type ReactNode } from 'react';
import { RoutineNextRun, useRoutineClock } from './RoutineNextRun';
import { RoutineRunConversation } from '@/components/ScheduledTasks/RoutineRunConversation';
export const formatTime = (value?: string) =>
  value
    ? Number.isNaN(Date.parse(value))
      ? '未报告'
      : new Date(value).toLocaleString('zh-CN', { hour12: false })
    : '未报告';
export function RoutineDetail({
  task,
  context,
  client,
  revision,
  actions,
}: {
  task: RoutineAddress;
  context: RoutineContext;
  client: ReturnType<typeof createRoutineClient>;
  revision: number;
  actions: (task: ManagedRoutine) => ReactNode;
}) {
  const now = useRoutineClock();
  const hideMissingTarget = Boolean(context.bot_type?.trim()) && context.bot_type !== 'service';
  const [detail, setDetail] = useState<ManagedRoutine>();
  const [history, setHistory] = useState<RuntimePage<RoutineRun>>({ items: [], total: 0, failed_targets: [] });
  const [detailError, setDetailError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const key = task.key;
  useEffect(() => {
    setPage(1);
    setDetail(undefined);
  }, [key]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setDetailError('');
    setHistoryError('');
    void Promise.allSettled([client.detail(context, task), client.runs(context, task, page, pageSize)])
      .then(([item, runs]) => {
        if (!active) return;
        if (item.status === 'fulfilled') setDetail(item.value);
        else setDetailError(item.reason instanceof Error ? item.reason.message : '任务当前不可用或不可见');
        if (runs.status === 'fulfilled') setHistory(runs.value);
        else {
          setHistory({ items: [], total: 0, failed_targets: [] });
          setHistoryError(runs.reason instanceof Error ? runs.reason.message : '历史查询失败');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [client, context, key, page, pageSize, refresh, revision]);
  const fields: Array<[string, ReactNode]> = detail
    ? [
        ['任务名称', detail.name],
        ['调度状态', detail.enabled ? '已启用' : '已停用'],
        ['生效环境', detail.runtime_stage ? stageNames[detail.runtime_stage] || detail.runtime_stage : '未报告'],
        ['运行实例', detail.device_uuid || '未报告'],
        ['Caller', detail.operator_id || '未报告'],
        ['执行频率', getRoutineScheduleLabel(detail.cron)],
        ['下次执行（预计）', <RoutineNextRun key="next" task={detail} now={now} />],
        ['时区', detail.timezone || '未报告'],
        ['模型', detail.model || '跟随 Bot 默认模型'],
        ['超时时间', `${detail.timeoutSecs} 秒`],
        ['创建时间', formatTime(detail.createdAt)],
        ['修改时间', formatTime(detail.modifiedAt)],
      ]
    : [];
  return (
    <div className="space-y-6">
      {detailError && (
        <Empty
          title="详情刷新失败，任务可能不可用或不可见"
          description={detailError}
          action={<Button onClick={() => setRefresh((value) => value + 1)}>重试</Button>}
        />
      )}
      {detail && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="m-0 text-lg font-semibold">{detail.name}</h2>
            {actions(detail)}
          </div>
          <section className="rounded-xl border border-border bg-card p-6">
            <h2 className="mb-5 text-base font-semibold">任务配置</h2>
            <dl className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {fields.map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="m-0 mt-2 break-all text-sm">{value}</dd>
                </div>
              ))}
            </dl>
            <h3 className="mb-2 mt-6 text-sm font-medium">执行指令</h3>
            <p className="whitespace-pre-wrap break-words rounded-lg bg-muted/40 p-4 text-sm">{detail.command}</p>
          </section>
        </>
      )}
      <section className="rounded-xl border border-border bg-card p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">历史执行实例</h2>
          <Button size="sm" variant="outline" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>
            刷新记录
          </Button>
        </div>
        <p className="mb-4 text-xs text-muted-foreground">
          仅展示引擎保留的历史。触发确认不代表实际执行完成，历史记录总数不是累计执行次数。
        </p>
        {history.failed_targets.length > 0 && (
          <p role="status" className="text-sm text-destructive">
            历史查询不完整：{history.failed_targets.map(describeFailure).join('；')}
          </p>
        )}
        {loading ? (
          <p role="status" className="py-8 text-center text-sm text-muted-foreground">
            加载中…
          </p>
        ) : historyError ? (
          <Empty title="历史查询未成功" description={historyError} />
        ) : history.items.length ? (
          <div className="space-y-3">
            {history.items.map((run, index) => (
              <article
                key={`${key}-${page}-${pageSize}-${run.runtime_stage}-${run.device_uuid}-${run.operator_id}-${run.id}-${run.startedAt}-${index}`}
                className="rounded-lg border border-border p-4 text-sm"
              >
                <div className="flex justify-between gap-4">
                  <span className="break-all font-medium">{run.id}</span>
                  <span>{runStatusLabel(run.status)}</span>
                </div>
                <dl className="mt-3 grid gap-3 text-xs text-muted-foreground sm:grid-cols-3">
                  <div>开始：{formatTime(run.startedAt)}</div>
                  <div>结束：{formatTime(run.finishedAt)}</div>
                  <div>耗时：{run.duration || '未报告'}</div>
                  {(!hideMissingTarget || run.runtime_stage?.trim()) && <div>环境：{run.runtime_stage ? stageNames[run.runtime_stage] || run.runtime_stage : '未报告'}</div>}
                  {(!hideMissingTarget || run.device_uuid?.trim()) && <div className="break-all">运行实例：{run.device_uuid || '未报告'}</div>}
                  {(!hideMissingTarget || run.operator_id?.trim()) && <div className="break-all">Caller：{run.operator_id || '未报告'}</div>}
                </dl>
                <div className="mt-4 flex justify-end">
                  <RoutineRunConversation run={run} botType={context.bot_type} />
                </div>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            title={history.failed_targets.length ? '未获取到记录，存在查询失败的实例' : '当前历史范围暂无执行记录'}
          />
        )}
        <Pagination
          current={page}
          pageSize={pageSize}
          total={history.total}
          onChange={setPage}
          onPageSizeChange={(value) => {
            setPageSize(value);
            setPage(1);
          }}
          pageSizeOptions={[10, 20, 50]}
          className="mt-5 justify-end"
        />
      </section>
    </div>
  );
}
