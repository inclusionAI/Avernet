import { RoutineRunConversation } from '@/components/ScheduledTasks/RoutineRunConversation';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Pagination } from '@/components/ui/Pagination';
import { describeFailure } from '@/services/botScheduledTasks/results';
import type { createRoutineClient } from '@/services/botScheduledTasks/service';
import {
  stageNames,
  type ManagedRoutine,
  type RoutineAddress,
  type RoutineContext,
  type RoutineRun,
  type RuntimePage,
} from '@/services/botScheduledTasks/types';
import { getRoutineScheduleLabel } from '@/services/botWorkshop/routineSchedule';
import { Terminal } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { EnableBadge, RunStatusBadge } from './RoutineBadges';
import { RoutineNextRun, useRoutineClock } from './RoutineNextRun';
export const formatTime = (value?: string) =>
  value
    ? Number.isNaN(Date.parse(value))
      ? '未报告'
      : new Date(value).toLocaleString('zh-CN', { hour12: false })
    : '未报告';

/** 配置分组：dt/dd 收进小组，避免 12 个字段平铺成一张“字段清单”。 */
function FieldGroup({ title, fields }: { title: string; fields: Array<[string, ReactNode]> }) {
  return (
    <div>
      <h3 className="m-0 mb-3 text-xs font-semibold tracking-wide text-muted-foreground">{title}</h3>
      <dl className="m-0 space-y-3">
        {fields.map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="m-0 mt-1 break-all text-sm">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

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
  return (
    <div className="space-y-5">
      {detailError && (
        <Empty
          title="详情刷新失败，任务可能不可用或不可见"
          description={detailError}
          action={<Button onClick={() => setRefresh((value) => value + 1)}>重试</Button>}
        />
      )}
      {detail && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="m-0 text-lg font-semibold">{detail.name}</h2>
            <EnableBadge enabled={detail.enabled} />
            <div className="ml-auto">{actions(detail)}</div>
          </div>
          <section className="rounded-xl border border-border bg-card px-5 py-5 sm:px-6">
            <div className="grid gap-x-8 gap-y-6 sm:grid-cols-2 xl:grid-cols-3">
              <FieldGroup
                title="调度"
                fields={[
                  ['执行频率', getRoutineScheduleLabel(detail.cron)],
                  ['下次执行（预计）', <RoutineNextRun key="next" task={detail} now={now} />],
                  ['时区', detail.timezone || '未报告'],
                  ['超时时间', `${detail.timeoutSecs} 秒`],
                ]}
              />
              <FieldGroup
                title="运行目标"
                fields={[
                  ['生效环境', detail.runtime_stage ? stageNames[detail.runtime_stage] || detail.runtime_stage : '未报告'],
                  ['运行实例', detail.device_uuid || '未报告'],
                  ['Caller', detail.operator_id || '未报告'],
                ]}
              />
              <FieldGroup
                title="元信息"
                fields={[
                  ['模型', detail.model || '跟随 Bot 默认模型'],
                  ['创建时间', formatTime(detail.createdAt)],
                  ['修改时间', formatTime(detail.modifiedAt)],
                ]}
              />
            </div>
            <div className="mt-6 overflow-hidden rounded-lg border border-border">
              <div className="flex items-center gap-2 border-b border-divider bg-muted/50 px-3 py-2">
                <Terminal aria-hidden className="size-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">执行指令</span>
              </div>
              <p className="m-0 whitespace-pre-wrap break-words px-4 py-3 font-mono text-[13px] leading-6">
                {detail.command}
              </p>
            </div>
          </section>
        </>
      )}
      <section className="rounded-xl border border-border bg-card px-5 py-5 sm:px-6">
        <div className="flex items-center justify-between gap-3">
          <h2 className="m-0 text-base font-semibold">
            历史执行实例
            {history.total > 0 && (
              <span className="ml-2 text-xs font-normal text-muted-foreground">共 {history.total} 条</span>
            )}
          </h2>
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground hover:text-foreground"
            disabled={loading}
            onClick={() => setRefresh((value) => value + 1)}
          >
            刷新记录
          </Button>
        </div>
        <p className="mb-4 mt-1 text-xs text-muted-foreground">
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
          <ul className="m-0 list-none space-y-3 p-0">
            {history.items.map((run, index) => (
              <li
                key={`${key}-${page}-${pageSize}-${run.runtime_stage}-${run.device_uuid}-${run.operator_id}-${run.id}-${run.startedAt}-${index}`}
                className="rounded-lg border border-border p-4 text-sm"
              >
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  <RunStatusBadge status={run.status} />
                  <span className="min-w-0 break-all font-mono text-xs text-foreground">{run.id}</span>
                  <div className="ml-auto flex flex-wrap items-center gap-x-4 text-xs text-muted-foreground">
                    <span>开始 {formatTime(run.startedAt)}</span>
                    <span>结束 {formatTime(run.finishedAt)}</span>
                    <span>耗时 {run.duration || '未报告'}</span>
                  </div>
                </div>
                {(!hideMissingTarget || run.runtime_stage?.trim() || run.device_uuid?.trim() || run.operator_id?.trim()) && (
                  <div className="mb-0 mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    {(!hideMissingTarget || run.runtime_stage?.trim()) && (
                      <span>环境：{run.runtime_stage ? stageNames[run.runtime_stage] || run.runtime_stage : '未报告'}</span>
                    )}
                    {(!hideMissingTarget || run.device_uuid?.trim()) && (
                      <span className="break-all font-mono">实例：{run.device_uuid || '未报告'}</span>
                    )}
                    {(!hideMissingTarget || run.operator_id?.trim()) && (
                      <span className="break-all font-mono">Caller：{run.operator_id || '未报告'}</span>
                    )}
                  </div>
                )}
                <div className="mt-3 flex justify-end">
                  <RoutineRunConversation run={run} botType={context.bot_type} />
                </div>
              </li>
            ))}
          </ul>
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
