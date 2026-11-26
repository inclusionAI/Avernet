import { Badge } from '@/components/ui/Badge';
import { recoveryStatus, recoveryTime } from '@/domain/recovery/presentation';
import {
  outcomeReport,
  presentOutcomeReport,
  readableReport,
  recordedDiagnosis,
  recordedOpening,
  reportSection,
} from '@/domain/recovery/report';
import type { RecoveryTask } from '@/domain/recovery/types';
import type { ReactNode } from 'react';
import { MarkdownReport } from '../OutputPrimitives';
import { Section } from '../shared';

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid min-w-0 gap-1 sm:grid-cols-[84px_minmax(0,1fr)] sm:gap-4">
      <dt className="text-[13px] leading-6 text-muted-foreground">{label}</dt>
      <dd className="m-0 min-w-0 text-sm">{children}</dd>
    </div>
  );
}

export function RecoveryDiagnosis({ task }: { task: RecoveryTask }) {
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

export function RecoveryOutcome({ task }: { task: RecoveryTask }) {
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
