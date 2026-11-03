import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Spin } from '@/components/ui/Spin';
import type { Step, Task } from '@/domain/evolution/types';
import {
  activeStatus,
  canCancelStep,
  canRetryStep,
  formatTime,
  isBotEvolutionTask,
  labelOf,
} from '@/domain/evolution/types';
import { history, useLocation, useParams } from '@umijs/max';
import { ArrowLeft, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { CreateResult } from './CreateResult';
import { stepName, taskTimes } from './presentation';
import { ErrorBox, EvolutionHeader, JsonView, Section, StatusBadge, TypeTag } from './shared';
import { TaskArtifacts } from './TaskArtifacts';
import TaskLogArchives from './TaskLogArchives';
import { TaskProgress } from './TaskProgress';
import { TaskTimeline } from './TaskTimeline';
import { useEvolutionContext } from './useEvolutionContext';
import VersionList from './VersionList';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

function TaskDetailContent() {
  const { taskId = '' } = useParams<{ taskId: string }>();
  const location = useLocation();
  const created = new URLSearchParams(location.search).get('created') === '1';
  const identity = useEvolutionContext();
  const evolutionApi = identity.api;
  const createdState = location.state as { createdTask?: Task; spaceKey?: string } | null;
  const returnedTask = createdState?.spaceKey === identity.key ? createdState.createdTask : undefined;
  const [task, setTask] = useState<Task | undefined>(() =>
    returnedTask?.task_id === taskId ? returnedTask : undefined,
  );
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [operationError, setOperationError] = useState('');
  const [diff, setDiff] = useState<string>();
  const [outputOpen, setOutputOpen] = useState(false);
  const reload = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    if (!identity.ready) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setTask(returnedTask?.task_id === taskId ? returnedTask : undefined);
    setError('');
    setSelected('');
    setDiff(undefined);
    const refresh = async () => {
      try {
        const result = await evolutionApi.task(taskId, controller.signal);
        if (stopped) return;
        setTask(result);
        setError('');
        setSelected((current) =>
          result.steps?.some((s) => s.stepId === current) ? current : result.steps?.at(-1)?.stepId ?? '',
        );
        if (activeStatus(result.status) || result.status.startsWith('waiting_')) timer = setTimeout(refresh, 5000);
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : '任务加载失败');
      }
    };
    void refresh();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [taskId, identity.ready, identity.key, revision, returnedTask]);
  const step = task?.steps?.find((item) => item.stepId === selected);
  const cancelTarget = task?.steps?.find((candidate) => canCancelStep(task, candidate));
  const manage = task && (isBotEvolutionTask(task) || ['pack', 'pack_restore'].includes(task.task_type));
  const action = async (target: Step, operation: 'retry' | 'cancel') => {
    if (!task || busy) return;
    setBusy(true);
    setOperationError('');
    try {
      await evolutionApi.stepAction(task.task_id, target.stepId, operation);
      reload();
    } catch (e) {
      setOperationError(e instanceof Error ? e.message : '操作失败');
      throw e;
    } finally {
      setBusy(false);
    }
  };
  const viewDiff = async () => {
    if (!task || !step) return;
    setBusy(true);
    setOperationError('');
    try {
      setDiff(await evolutionApi.diff(task.task_id, step.stepId));
    } catch (e) {
      setOperationError(e instanceof Error ? e.message : 'Diff 加载失败');
    } finally {
      setBusy(false);
    }
  };
  const times = task ? taskTimes(task) : null;
  const rounds = (task?.steps ?? []).map((s) => s.roundNo).filter((n): n is number => typeof n === 'number');
  if (created && task && !error)
    return (
      <main className="h-full overflow-y-auto bg-background p-4 sm:p-6 lg:px-10">
        <EvolutionHeader tab="tasks" showTabs={false} />
        <CreateResult task={task} />
      </main>
    );
  return (
    <main className="h-full overflow-y-auto bg-background p-4 sm:p-6 lg:px-10">
      <div className="w-full min-w-0 space-y-5">
        <EvolutionHeader tab="tasks" showTabs={false} />
        <header className="flex flex-wrap items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            aria-label="返回任务列表"
            onClick={() => history.push('/evolution-factory')}
          >
            <ArrowLeft size={18} />
          </Button>
          <h2 className="text-[19px] font-semibold">{task?.task_name ?? '任务详情'}</h2>
          {task && <TypeTag type={task.task_type} />}
          {rounds.length > 0 && <span className="text-xs text-muted-foreground">第 {Math.max(...rounds)} 轮</span>}
          {task && <StatusBadge status={task.status} dot />}
          <Button className="ml-auto" variant="outline" leftIcon={<RefreshCw size={15} />} onClick={reload}>
            刷新
          </Button>
          {task && cancelTarget && manage && canCancelStep(task, cancelTarget) && (
            <ConfirmDialog
              title="停止当前步骤？"
              description="按原服务取消当前 Step 和关联执行。请确认目标 Bot；已完成步骤和产物保留。"
              onConfirm={() => action(cancelTarget, 'cancel')}
            >
              <Button
                size="sm"
                variant="outline"
                className="border-destructive/40 text-destructive hover:bg-destructive/5"
                disabled={busy}
              >
                停止当前步骤
              </Button>
            </ConfirmDialog>
          )}
        </header>
        {task && <p className="-mt-3 pl-12 text-xs text-muted-foreground break-all">任务ID：{task.task_id}</p>}
        <ErrorBox message={identity.error ?? error} retry={reload} />
        <ErrorBox message={operationError} />
        {!task && !error && <Spin tip="加载任务…" />}
        {task && (
          <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
            <div className="min-w-0 space-y-5">
              <div className="rounded-xl border border-dashed border-primary/20 bg-card px-4 py-3 text-xs leading-relaxed text-muted-foreground">
                基本信息 → 执行进度 → 时间线 → 产出物 → 执行日志。产出物与进度均以实际执行结果为准。
              </div>
              {!isBotEvolutionTask(task) && !['pack', 'pack_restore'].includes(task.task_type) && (
                <p className="text-xs text-muted-foreground">
                  历史版本来源，仅展示执行记录；本页不提供该类型的创建或重试操作。
                </p>
              )}
              <Section title="基本信息">
                <dl className="grid gap-x-6 gap-y-4 text-[13px] sm:grid-cols-3">
                  {[
                    ['目标 Bot', task.bot_id],
                    ['创建人', task.created_by],
                    ['创建时间', formatTime(task.gmt_create)],
                    ['任务类型', labelOf(task.task_type)],
                    ['Owner', task.user_id],
                    ['结束时间', formatTime(times?.end)],
                    ['备注', task.remark || '—'],
                  ].map(([key, value]) => (
                    <div key={key}>
                      <dt className="text-xs text-muted-foreground">{key}</dt>
                      <dd className="mt-1 break-all">{value}</dd>
                    </div>
                  ))}
                </dl>
              </Section>
              <Section title="执行进度">
                <TaskProgress steps={task.steps ?? []} />
              </Section>
              <TaskTimeline
                task={task}
                onSelect={(stepId) => {
                  setSelected(stepId);
                  setDiff(undefined);
                  setOperationError('');
                }}
              />
              <TaskArtifacts
                task={task}
                step={step}
                selected={selected}
                onSelect={(stepId) => {
                  setSelected(stepId);
                  setOutputOpen(true);
                  setDiff(undefined);
                  setOperationError('');
                }}
                outputOpen={outputOpen}
                setOutputOpen={setOutputOpen}
                onViewDiff={() => void viewDiff()}
                diff={diff}
                busy={busy}
                operationError={operationError}
              />
              {step && (
                <Section title={`执行日志 · ${stepName(step.stepType)}`}>
                  <div className="mb-4 flex flex-wrap gap-3">
                    <span className="text-xs text-muted-foreground">{step.stepId}</span>
                    {manage && canRetryStep(task, step) && (
                      <Button
                        size="sm"
                        variant="outline"
                        loading={busy}
                        onClick={() => {
                          Tracert.click(Points.evolution.runTask, { task_id: taskId });
                          void action(step, 'retry').catch(() => undefined);
                        }}
                      >
                        继续执行
                      </Button>
                    )}
                  </div>
                  <p className="mb-2 text-xs text-muted-foreground">实际命令与投递记录（非实时日志流）</p>
                  <pre className="max-h-[260px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-foreground p-4 font-mono text-xs leading-relaxed text-background">
                    {step.command || '暂无命令'}
                    {step.botResponse ? `\n\n${JSON.stringify(step.botResponse, null, 2)}` : ''}
                    {step.error?.message ? `\n\n${step.error.code || '执行错误'}: ${step.error.message}` : ''}
                  </pre>
                  {task.interactions
                    ?.filter((item) => item.stepId === step.stepId)
                    .map((item) => (
                      <details
                        key={item.interactionId}
                        className="mt-4 rounded-lg border border-border p-3"
                        open={item.status === 'waiting'}
                      >
                        <summary className="text-sm">
                          {item.status === 'waiting' ? '等待补充上下文' : '交互记录'}
                        </summary>
                        <JsonView value={{ question: item.question, answer: item.answer }} />
                      </details>
                    ))}
                  {step.status === 'succeeded' && step.stepType === 'optimize' && Boolean(step.output?.diff) && (
                    <Button className="mt-4" variant="outline" loading={busy} onClick={() => void viewDiff()}>
                      查看变更 Diff
                    </Button>
                  )}
                  {diff && (
                    <pre className="mt-3 max-h-[40rem] overflow-auto rounded-lg bg-muted p-4 font-mono text-xs">
                      {diff}
                    </pre>
                  )}
                  <TaskLogArchives taskId={task.task_id} />
                </Section>
              )}
              <details className="rounded-xl border border-border bg-card p-5">
                <summary className="cursor-pointer text-sm font-medium">任务配置与技术信息</summary>
                <div className="mt-4">
                  <JsonView value={task.config} />
                </div>
              </details>
            </div>
            <VersionList
              task={task}
              revision={revision + (task.steps ?? []).filter((s) => s.status === 'succeeded').length}
            />
          </div>
        )}
      </div>
    </main>
  );
}

export default function TaskDetail() {
  const context = useEvolutionContext();
  return <TaskDetailContent key={context.key} />;
}
