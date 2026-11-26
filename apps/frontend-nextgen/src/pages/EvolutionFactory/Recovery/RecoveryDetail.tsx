import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { recoveryStatus, recoveryTime } from '@/domain/recovery/presentation';
import { userContinuationEvent } from '@/domain/recovery/report';
import type { RecoveryTask } from '@/domain/recovery/types';
import { recoveryService } from '@/services/recovery/recoveryService';
import { ArrowLeft, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { ErrorBox, Section } from '../shared';
import { RecoveryDiagnosis, RecoveryOutcome } from './RecoveryDetailSections';
import { RecoveryHistory, RecoveryPlans } from './RecoveryHistory';
import { OriginalSessionLink } from './RecoveryList';
import { RecoveryOwnerAction, RecoveryPlanView, StopRecovery } from './RecoveryPlan';

export function RecoveryDetail({ taskId, onBack }: { taskId: string; onBack(): void }) {
  const [task, setTask] = useState<RecoveryTask>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [busy, setBusy] = useState(false);
  const actionBusy = useRef(false);
  const [revision, setRevision] = useState(0);
  const live = useRef(false);
  const active = Boolean(task && ['running', 'waiting_owner', 'waiting_effect'].includes(task.status));
  const reload = () => setRevision((value) => value + 1);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    recoveryService
      .detail(taskId, controller.signal)
      .then(
        (value) => {
          if (controller.signal.aborted) return;
          if (value.taskId !== taskId || !Array.isArray(value.timeline) || !value.request?.target) {
            setError('自愈任务详情响应不完整，请刷新重试。');
            return;
          }
          setTask(value);
          setError('');
        },
        (failure: unknown) => {
          if (controller.signal.aborted) return;
          if ([401, 403, 404].includes((failure as { status?: number }).status ?? 0)) setTask(undefined);
          setError(failure instanceof Error ? failure.message : '未能读取自愈任务');
        },
      )
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [taskId, revision]);

  useEffect(() => {
    if (!active || busy) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) setRevision((value) => value + 1);
    }, 20000);
    return () => window.clearInterval(timer);
  }, [active, busy]);

  async function mutate(operation: () => Promise<unknown>, rethrow = false) {
    if (actionBusy.current || !task?.canOperate || task.simulation) return;
    actionBusy.current = true;
    setBusy(true);
    setActionError('');
    try {
      await operation();
    } catch (failure) {
      if (live.current) setActionError(failure instanceof Error ? failure.message : '操作未成功，请刷新后确认。');
      if (rethrow) throw failure;
    } finally {
      actionBusy.current = false;
      if (live.current) {
        setBusy(false);
        reload();
      }
    }
  }

  const target = task?.request.target;
  const state = task ? recoveryStatus(task) : undefined;
  const created = task?.timeline.find((event) => event.kind === 'created');
  const latest = task?.timeline.reduce<RecoveryTask['timeline'][number] | undefined>(
    (current, event) => (!current || current.at_ms < event.at_ms ? event : current),
    undefined,
  );
  const yielded = task ? userContinuationEvent(task) : undefined;

  return (
    <div className="tc-recovery-detail mx-auto w-full max-w-[1040px] min-w-0">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="mb-3 px-0"
        leftIcon={<ArrowLeft size={14} />}
        onClick={onBack}
      >
        返回自愈任务
      </Button>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="m-0 text-xl font-semibold">诊断与自愈详情</h2>
            {state && <Badge tone={state.tone}>{state.label}</Badge>}
          </div>
          {task && target && (
            <div className="mt-2 flex flex-wrap items-center gap-2 break-all text-xs text-muted-foreground">
              <span>{target.bot_id}</span>
              <span>任务 {task.taskId}</span>
              <OriginalSessionLink
                botId={target.bot_id}
                ownerId={target.entity_id}
                sessionKey={task.request.session_key}
              />
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {task?.canOperate && !task.simulation && active && (
            <StopRecovery
              busy={busy}
              error={actionError}
              onConfirm={() => mutate(() => recoveryService.terminate(taskId, '用户手动停止任务'), true)}
            />
          )}
          <Button
            type="button"
            size="sm"
            variant="outline"
            leftIcon={<RefreshCw size={14} />}
            disabled={loading || busy}
            onClick={reload}
          >
            刷新
          </Button>
        </div>
      </header>
      <ErrorBox message={error} retry={reload} />
      <ErrorBox message={actionError} retry={reload} />
      {loading && (
        <p role="status" className="text-sm text-muted-foreground">
          正在读取自愈任务…
        </p>
      )}
      {task && target && (
        <div className="space-y-7">
          <Section title="任务信息">
            <dl className="m-0 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {[
                ['Bot', target.bot_id],
                ['创建时间', recoveryTime(created?.at_ms)],
                ['最近更新', recoveryTime(yielded?.at_ms ?? latest?.at_ms)],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="m-0 mt-1 break-words text-sm">{value}</dd>
                </div>
              ))}
            </dl>
          </Section>
          <RecoveryHistory task={task}>
            <RecoveryDiagnosis task={task} />
            <RecoveryOwnerAction
              key={task.ownerRequest?.requestId}
              task={task}
              busy={busy}
              onConfirm={(requestId, feedback) => mutate(() => recoveryService.confirmOwner(taskId, requestId, feedback))}
            />
          </RecoveryHistory>
          <RecoveryPlans
            task={task}
            renderPlan={(plan, historical) => (
              <RecoveryPlanView
                key={plan.digest}
                task={task}
                plan={plan}
                historical={historical}
                busy={busy}
                onDecide={(approved, cooperationDone, feedback) =>
                  mutate(() =>
                    recoveryService.decide(taskId, {
                      plan_digest: plan.digest,
                      approved,
                      cooperation_done: cooperationDone,
                      feedback,
                    }),
                  )
                }
              />
            )}
          />
          <RecoveryOutcome task={task} />
        </div>
      )}
    </div>
  );
}
