import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { recoveryStatus, recoveryTime } from '@/domain/recovery/presentation';
import {
  outcomeReport,
  presentOutcomeReport,
  readableReport,
  recordedDiagnosis,
  recordedOpening,
  reportSection,
  userContinuationEvent,
} from '@/domain/recovery/report';
import type { RecoveryTask } from '@/domain/recovery/types';
import { recoveryApi } from '@/services/backendApi/clawinsight/recoveryController';
import { ArrowLeft, RefreshCw } from 'lucide-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { MarkdownReport } from '../OutputPrimitives';
import { ErrorBox, Section } from '../shared';
import { RecoveryHistory, RecoveryPlans } from './RecoveryHistory';
import { OriginalSessionLink } from './RecoveryList';
import { RecoveryOwnerAction, RecoveryPlanView, StopRecovery } from './RecoveryPlan';

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid min-w-0 gap-1 sm:grid-cols-[84px_minmax(0,1fr)] sm:gap-4">
      <dt className="text-[13px] leading-6 text-muted-foreground">{label}</dt>
      <dd className="m-0 min-w-0 text-sm">{children}</dd>
    </div>
  );
}

function Diagnosis({ task }: { task: RecoveryTask }) {
  const item = recordedDiagnosis(task);
  const state =
    item.diagnosis?.decision === 'UNRESOLVED'
      ? '未判定'
      : item.diagnosis?.handlerName === 'COMPLETED'
      ? '已完成'
      : item.diagnosis?.handlerName === 'NOT_COMPLETED'
      ? '未完成'
      : '未知';
  return (
    <div id="recovery-diagnosis" tabIndex={-1} className="scroll-mt-6 outline-none">
      <Section title="诊断详情">
        <dl className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
          {[
            ['问题类型', item.diagnosis?.businessProblemCategory || '未提供'],
            ['诊断时任务状态', state],
            ['诊断时间', recoveryTime(item.diagnosis?.diagnosedAt)],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="m-0 mt-1 break-words text-sm">{value}</dd>
            </div>
          ))}
        </dl>
        <dl className="m-0 space-y-3">
          <DetailRow label={item.goal ? '原任务目标' : '业务诊断'}>
            <MarkdownReport content={readableReport(item.goal || item.business || '未提供业务诊断。')} />
          </DetailRow>
          <DetailRow label={item.cause ? '核查结论' : '系统诊断'}>
            {item.at && item.cause && (
              <p className="mb-1 text-xs tabular-nums text-muted-foreground">{recoveryTime(item.at)}</p>
            )}
            <MarkdownReport content={readableReport(item.cause || item.system || '未提供系统诊断。')} />
          </DetailRow>
        </dl>
        {item.goal && item.cause && (item.business || item.system) && (
          <details className="mt-3">
            <summary className="cursor-pointer text-[13px] text-muted-foreground hover:text-foreground">
              诊断报告
            </summary>
            <dl className="mt-2 space-y-3">
              {item.business && (
                <DetailRow label="业务诊断">
                  <MarkdownReport content={readableReport(item.business)} />
                </DetailRow>
              )}
              {item.system && (
                <DetailRow label="系统诊断">
                  <MarkdownReport content={readableReport(item.system)} />
                </DetailRow>
              )}
            </dl>
          </details>
        )}
      </Section>
    </div>
  );
}

function Outcome({ task }: { task: RecoveryTask }) {
  const state = recoveryStatus(task);
  const saved = task.judgment?.summary ?? '';
  const report = presentOutcomeReport(outcomeReport(task) ?? '');
  const yielded = task.completionReason === 'user_continued';
  const basis = reportSection(saved, ['恢复判据复核', '核验依据']);
  const firstParagraph = recordedOpening(report || saved);
  const opening = !report || report.startsWith(firstParagraph) ? firstParagraph : '';
  const lead = yielded
    ? task.final_summary?.match(/(?:^|\n)当前结果[：:]\s*([^。\n]+。?)/)?.[1] ||
      '本次自愈已退出。原任务结果以会话进展为准。'
    : (task.judgment?.effect === 'recovered' ? opening.replace(/^原任务已恢复[。！]\s*(?=\S)/, '') : opening) ||
      (task.status === 'canceled' ? '原任务的恢复情况尚未确认。' : '');
  const resultDetails = report && !yielded && report !== opening ? report.slice(opening.length).trim() : '';
  const stopped =
    task.status === 'canceled'
      ? task.timeline.find((event) => event.kind === 'termination' && typeof event.payload.actor_role === 'string')
      : undefined;
  if (!lead && !basis && !stopped && ['running', 'waiting_owner'].includes(task.status)) return null;
  return (
    <div id="recovery-result" tabIndex={-1} className="scroll-mt-6 outline-none">
      <Section title="恢复结果">
        <dl className="m-0 space-y-3">
          <DetailRow label="恢复结论">
            <div className="flex flex-wrap items-baseline gap-2">
              <Badge tone={state.tone}>{state.label}</Badge>
              {lead && <MarkdownReport content={readableReport(lead)} />}
            </div>
            {resultDetails && <MarkdownReport content={readableReport(resultDetails)} />}
          </DetailRow>
          {basis && !yielded && (
            <DetailRow label="核验依据">
              <MarkdownReport content={readableReport(basis)} />
            </DetailRow>
          )}
          {stopped && (
            <DetailRow label="停止说明">
              <p className="m-0">
                {recoveryTime(stopped.at_ms)}
                {typeof stopped.payload.reason === 'string' && ` · ${stopped.payload.reason}`}
              </p>
              <p className="mt-1 text-muted-foreground">
                {task.termination?.status === 'remote_stopped'
                  ? '自愈助手已停止，后续自愈操作已取消。'
                  : '后续自愈操作已取消，自愈助手是否完全停止尚未确认。'}
              </p>
            </DetailRow>
          )}
        </dl>
      </Section>
    </div>
  );
}

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
    recoveryApi
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
              onConfirm={() => mutate(() => recoveryApi.terminate(taskId, '用户手动停止任务'), true)}
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
            <Diagnosis task={task} />
            <RecoveryOwnerAction
              key={task.ownerRequest?.requestId}
              task={task}
              busy={busy}
              onConfirm={(requestId, feedback) => mutate(() => recoveryApi.confirmOwner(taskId, requestId, feedback))}
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
                    recoveryApi.decide(taskId, {
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
          <Outcome task={task} />
        </div>
      )}
    </div>
  );
}
