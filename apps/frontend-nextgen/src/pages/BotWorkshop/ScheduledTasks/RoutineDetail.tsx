import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { createRoutineClient } from '@/services/botScheduledTasks/service';
import {
  stageNames,
  type ManagedRoutine,
  type RoutineContext,
  type RoutineRun,
} from '@/services/botScheduledTasks/types';
import { getRoutineScheduleLabel } from '@/services/botWorkshop/routineSchedule';
import { useEffect, useState } from 'react';
export const formatTime = (value?: string) =>
  value ? (Number.isNaN(Date.parse(value)) ? value : new Date(value).toLocaleString('zh-CN', { hour12: false })) : '—';
export function RoutineDetail({
  task,
  context,
  client,
  revision,
}: {
  task: ManagedRoutine;
  context: RoutineContext;
  client: ReturnType<typeof createRoutineClient>;
  revision: number;
}) {
  const [detail, setDetail] = useState(task);
  const [runs, setRuns] = useState<RoutineRun[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    setDetail(task);
    setRuns([]);
    void Promise.all([client.detail(context, task), client.runs(context, task)])
      .then(([next, history]) => {
        if (active) {
          setDetail(next);
          setRuns(history);
        }
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : '任务详情加载失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [client, context, task, refresh, revision]);
  const fields = [
    ['任务名称', detail.name],
    ['调度状态', detail.enabled ? '已启用' : '已停用'],
    ['生效环境', detail.runtime_stage ? stageNames[detail.runtime_stage] : '默认环境'],
    ['运行实例', detail.device_uuid || '不适用'],
    ['执行频率', getRoutineScheduleLabel(detail.cron)],
    ['时区', detail.timezone],
    ['模型', detail.model || '跟随 Bot 默认模型'],
    ['超时时间', `${detail.timeoutSecs} 秒`],
    ['最近执行', formatTime(detail.lastRunAt)],
    ['下次执行', formatTime(detail.nextRunAt)],
  ];
  return (
    <div className="space-y-6">
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
      <section className="rounded-xl border border-border bg-card p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">历史执行实例</h2>
          <Button size="sm" variant="outline" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>
            刷新记录
          </Button>
        </div>
        <p className="mb-4 text-xs text-muted-foreground">
          展示引擎保留的最近执行记录；运行实例是执行目标，执行实例是一次任务运行。
        </p>
        {loading ? (
          <p role="status" className="py-8 text-center text-sm text-muted-foreground">
            加载中…
          </p>
        ) : error ? (
          <Empty
            title="加载失败"
            description={error}
            action={<Button onClick={() => setRefresh((value) => value + 1)}>重试</Button>}
          />
        ) : runs.length ? (
          <div className="space-y-3">
            {runs.map((run, index) => (
              <article key={`${run.id}-${index}`} className="rounded-lg border border-border p-4 text-sm">
                <div className="flex justify-between gap-4">
                  <span className="break-all font-medium">{run.id}</span>
                  <span>{run.status}</span>
                </div>
                <dl className="mt-3 grid gap-3 text-xs text-muted-foreground sm:grid-cols-3">
                  <div>开始：{formatTime(run.startedAt)}</div>
                  <div>结束：{formatTime(run.finishedAt)}</div>
                  <div>耗时：{run.duration || '—'}</div>
                  <div>环境：{run.target.runtime_stage ? stageNames[run.target.runtime_stage] : '默认环境'}</div>
                  <div className="break-all">运行实例：{run.target.device_uuid || '不适用'}</div>
                </dl>
                {run.output && <p className="mt-3 whitespace-pre-wrap break-words">{run.output}</p>}
                {run.error && <p className="mt-3 text-destructive">{run.error}</p>}
              </article>
            ))}
          </div>
        ) : (
          <Empty title="暂无执行记录" description="任务执行后将在这里展示历史实例。" />
        )}
      </section>
    </div>
  );
}
