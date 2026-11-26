import { Badge } from '@/components/ui/Badge';
import { planAnchor, recoveryEntryAnchor, recoveryJourney } from '@/domain/recovery/journey';
import { recoveryTime } from '@/domain/recovery/presentation';
import { object, readableReport, text } from '@/domain/recovery/report';
import type { RecoveryPlan, RecoveryTask } from '@/domain/recovery/types';
import { Check, CircleHelp } from 'lucide-react';
import { type ReactNode, useMemo } from 'react';
import { MarkdownReport } from '../OutputPrimitives';
import { Section } from '../shared';

export function locateRecoverySection(id: string) {
  const target = document.getElementById(id);
  target?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  target?.focus({ preventScroll: true });
}

function SectionLink({ id, children }: { id: string; children: string }) {
  return (
    <a
      href={`#${id}`}
      className="text-sm text-primary hover:underline"
      onClick={(event) => {
        event.preventDefault();
        locateRecoverySection(id);
      }}
    >
      {children}
    </a>
  );
}

function OriginalText({ content, label }: { content: string; label: string }) {
  return (
    <details className="rounded-md bg-muted/40 px-3 py-1">
      <summary className="cursor-pointer text-sm text-primary">{label}</summary>
      <pre className="my-2 max-h-72 overflow-y-auto whitespace-pre-wrap break-all text-xs leading-6 text-foreground">
        {content}
      </pre>
    </details>
  );
}

export function RecoveryHistory({ task, children }: { task: RecoveryTask; children: ReactNode }) {
  const entries = useMemo(() => recoveryJourney(task), [task]);
  const active = ['running', 'waiting_owner', 'waiting_effect'].includes(task.status);
  const planWaiting =
    task.status === 'waiting_owner' &&
    Boolean(task.plan && !task.plan.approvedAt) &&
    task.ownerRequest?.status !== 'pending';
  const permissionWaiting = task.status === 'waiting_owner' && task.ownerRequest?.status === 'pending';
  const observed = entries.some((entry) => entry.phase === 'verify');
  const diagnosisComplete = Boolean(
    entries.length || task.judgment || task.timeline.some((item) => item.kind === 'assessment'),
  );
  const nodes = [
    {
      id: 'recovery-diagnosis',
      label: '诊断问题',
      state: diagnosisComplete ? '已完成' : '诊断中',
      done: diagnosisComplete,
    },
    ...entries
      .filter((entry) => entry.phase !== 'verify')
      .map((entry) => {
        const awaiting =
          task.status === 'waiting_owner' &&
          (planWaiting ? entry.progress?.approval : entry.link?.target === 'owner-cooperation');
        return {
          id: recoveryEntryAnchor(entry.id),
          label: entry.progress?.label || entry.title,
          state: awaiting
            ? planWaiting
              ? '待确认方案'
              : '待你处理'
            : entry.progress?.state ?? entry.status ?? '已记录',
          done: !awaiting && entry.tone === 'success',
        };
      }),
    ...(!observed && task.completionReason !== 'user_continued'
      ? [
          {
            id: 'recovery-result',
            label: '核验结果',
            state:
              task.judgment?.effect === 'recovered'
                ? '已核实恢复'
                : task.status === 'waiting_effect'
                ? '等待核验'
                : active
                ? '未开始'
                : '未核验',
            done: task.judgment?.effect === 'recovered',
          },
        ]
      : entries
          .filter((entry) => entry.phase === 'verify')
          .map((entry) => ({
            id: recoveryEntryAnchor(entry.id),
            label: '核验结果',
            state: entry.status === '已恢复' ? '已核实恢复' : entry.status || '已记录',
            done: entry.tone !== 'needs-attention',
          }))),
  ];
  const done = nodes.filter((node) => node.done).length;

  return (
    <>
      <Section title="执行进度">
        <div className="mb-4 flex items-center gap-3 text-xs text-muted-foreground">
          <div
            className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-label="自愈节点完成数"
            aria-valuemin={0}
            aria-valuemax={nodes.length}
            aria-valuenow={done}
          >
            <div className="h-full bg-primary" style={{ width: `${(100 * done) / nodes.length}%` }} />
          </div>
          <span>
            已完成 {done} / {nodes.length} 个节点
          </span>
        </div>
        <ol className="m-0 flex list-none overflow-x-auto p-0 pb-2">
          {nodes.map(({ id, label, state, done: finished }, index) => (
            <li key={id} className="tc-recovery-progress-node relative min-w-[105px] flex-1 px-2 text-center">
              <button
                type="button"
                className="relative z-10 mx-auto flex w-full min-w-0 flex-col items-center gap-1"
                onClick={() => locateRecoverySection(id)}
              >
                <span
                  className={`grid size-8 shrink-0 place-items-center rounded-full border text-xs ${
                    finished
                      ? 'border-primary/50 bg-primary/10 text-primary'
                      : 'border-border bg-card text-muted-foreground'
                  }`}
                >
                  {finished ? <Check size={15} aria-hidden /> : index + 1}
                </span>
                <span className="text-sm font-medium">{label}</span>
                <span className="text-xs text-muted-foreground">{state}</span>
              </button>
            </li>
          ))}
        </ol>
        {(permissionWaiting || planWaiting) && (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md bg-primary/5 p-3 text-sm">
            <div className="min-w-0">
              <strong>{permissionWaiting ? '需要你处理配合事项' : '等待你确认修复方案'}</strong>
              {permissionWaiting && task.ownerRequest?.summary && (
                <MarkdownReport content={readableReport(task.ownerRequest.summary)} />
              )}
            </div>
            {permissionWaiting ? (
              <SectionLink id="owner-cooperation">查看待办</SectionLink>
            ) : (
              <SectionLink id="recovery-plan-document">查看并确认方案</SectionLink>
            )}
          </div>
        )}
      </Section>
      {children}
      {entries.length > 0 && (
        <Section title="处理经过">
          <ol className="m-0 list-none p-0">
            {entries.map((entry) => (
              <li
                id={recoveryEntryAnchor(entry.id)}
                key={entry.id}
                tabIndex={-1}
                className="relative flex scroll-mt-6 gap-3 pb-5 outline-none last:pb-0"
              >
                <span
                  className={`z-10 grid size-7 shrink-0 place-items-center rounded-full ${
                    entry.tone === 'success' ? 'bg-success/10 text-success' : 'bg-muted text-muted-foreground'
                  }`}
                >
                  {entry.tone === 'success' ? <Check size={14} aria-hidden /> : <CircleHelp size={14} aria-hidden />}
                </span>
                <span
                  className="pointer-events-none absolute bottom-0 left-[13px] top-7 border-l border-divider"
                  aria-hidden
                />
                <div className="min-w-0 flex-1 space-y-2">
                  <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <h3 className="m-0 text-sm font-semibold">{entry.title}</h3>
                    {entry.status && (
                      <Badge
                        tone={entry.tone === 'success' ? 'success' : entry.tone === 'failed' ? 'error' : 'warning'}
                      >
                        {entry.status}
                      </Badge>
                    )}
                    <time className="text-xs tabular-nums text-muted-foreground">{recoveryTime(entry.atMs)}</time>
                  </header>
                  {entry.body && <MarkdownReport content={readableReport(entry.body)} />}
                  {entry.details?.length ? (
                    <dl className="m-0 space-y-2">
                      {entry.details.map((detail) => (
                        <div key={detail.label} className="grid gap-1 sm:grid-cols-[108px_minmax(0,1fr)] sm:gap-3">
                          <dt className="text-xs leading-6 text-muted-foreground">{detail.label}</dt>
                          <dd className="m-0 min-w-0">
                            <MarkdownReport content={readableReport(detail.body)} />
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : null}
                  {entry.originalMessage && <OriginalText content={entry.originalMessage} label="查看指引原文" />}
                  {entry.cardContent && <OriginalText content={entry.cardContent} label="查看卡片内容" />}
                  {entry.link && <SectionLink id={entry.link.target}>{entry.link.label}</SectionLink>}
                </div>
              </li>
            ))}
          </ol>
        </Section>
      )}
    </>
  );
}

export function RecoveryPlans({
  task,
  renderPlan,
}: {
  task: RecoveryTask;
  renderPlan(plan: RecoveryPlan, historical: boolean): ReactNode;
}) {
  const events = task.timeline.filter((event) => event.kind === 'plan').sort((a, b) => a.at_ms - b.at_ms);
  const latest = events.at(-1);
  if (!latest && !task.plan) return null;
  return (
    <Section title="修复方案详情">
      {events.map((event) => {
        const current = event === latest;
        const saved = object(event.payload.plan);
        const plan =
          current && task.plan
            ? task.plan
            : Array.isArray(saved.actions) && Array.isArray(saved.owner_cooperation)
            ? ({ ...saved, approvedAt: null, digest: text(saved.digest) } as unknown as RecoveryPlan)
            : undefined;
        return (
          <div
            key={event.event_id}
            id={planAnchor(event, latest)}
            tabIndex={-1}
            className="mb-4 scroll-mt-6 space-y-3 outline-none"
          >
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span>提交于 {recoveryTime(event.at_ms)}</span>
              {current && task.status === 'canceled' && (
                <Badge>自愈已停止{plan && !plan.approvedAt ? ' · 方案未确认' : ''}</Badge>
              )}
            </div>
            {plan ? renderPlan(plan, !(current && task.plan)) : <MarkdownReport content={event.summary} />}
          </div>
        );
      })}
      {!latest && task.plan && (
        <div id="recovery-plan-document" tabIndex={-1} className="scroll-mt-6 outline-none">
          {renderPlan(task.plan, false)}
        </div>
      )}
    </Section>
  );
}
