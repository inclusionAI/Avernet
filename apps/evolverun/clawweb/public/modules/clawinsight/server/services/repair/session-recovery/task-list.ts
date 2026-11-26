import { fromMarkdown } from 'mdast-util-from-markdown';
import { SessionRecoveryError } from './errors.js';
import type { RecoveryCompletionReason, RecoveryEffect, RecoveryPhase, RecoveryStoredTask, RecoveryTaskStatus, RecoveryTimelineEvent } from './task-contracts.js';
export type RecoveryTaskListQuery = { page: number; pageSize: number; botId: string; env: string; status: string; includePresentation?: boolean };
export type RecoveryTaskListItem = { taskId: string; botId: string; entityId: string; env: string; sessionKey: string;
  status: RecoveryTaskStatus; phase: RecoveryPhase; effect: RecoveryEffect; createdAt: number | string;
  startAtMs?: number; endAtMs?: number; completionReason?: RecoveryCompletionReason; overview?: string };
export type RecoveryTaskListPage = { items: RecoveryTaskListItem[]; page: number; pageSize: number; total: number };
export function parseRecoveryTaskListQuery(input: Record<string, unknown>): RecoveryTaskListQuery {
  const page = Number(input.page ?? 1), pageSize = Number(input.pageSize ?? 20);
  const botId = String(input.botId ?? '').trim(), env = String(input.env ?? ''), status = String(input.status ?? '');
  const presentation = input.includePresentation;
  if (Object.keys(input).some(key => !['page', 'pageSize', 'botId', 'env', 'status', 'includePresentation'].includes(key))
    || (presentation !== undefined && presentation !== 'true' && presentation !== 'false')
    || !Number.isSafeInteger(page) || page < 1 || page > 100000 || !Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 50
    || botId.length > 255 || !['', 'pre', 'prod', 'dev', 'gray'].includes(env) || !['', 'running', 'waiting_owner', 'waiting_effect', 'completed', 'failed', 'canceled'].includes(status)) {
    throw new SessionRecoveryError(422, 'invalid_session_recovery', '任务查询条件无效');
  }
  return { page, pageSize, botId, env, status, ...(presentation === 'true' ? { includePresentation: true } : {}) };
}

function originalTaskGoal(report: string): string {
  const sections = fromMarkdown(report).children;
  for (const [index, section] of sections.entries()) {
    if (section.type !== 'heading' || section.children.map(child => 'value' in child ? child.value : '').join('').trim() !== '原任务目标') continue;
    const start = section.position?.end.offset;
    const end = sections.slice(index + 1).find(next => next.type === 'heading' && next.depth <= section.depth)?.position?.start.offset;
    if (start !== undefined) return report.slice(start, end).trim();
  }
  return '';
}

/** List display uses recorded task facts; updatedAt includes unrelated notifications. */
export function presentRecoveryTask(
  state: Pick<RecoveryStoredTask, 'request' | 'status' | 'completionReason'>,
  judgment: RecoveryStoredTask['judgment'] | undefined,
  events: RecoveryTimelineEvent[],
): Pick<RecoveryTaskListItem, 'overview' | 'startAtMs' | 'endAtMs' | 'completionReason'> {
  const timeline = [...events].sort((left, right) => left.at_ms - right.at_ms);
  let overview = '';
  for (const event of timeline) {
    if (event.kind !== 'waiting' && event.kind !== 'assessment') continue;
    overview = originalTaskGoal(event.summary);
    if (overview) break;
  }
  if (!overview) {
    const business = state.request.diagnosis.split(/\n\s*系统诊断[：:]/, 1)[0].replace(/^业务诊断[：:]\s*/, '');
    const paragraph = fromMarkdown(business).children.find(node => node.type === 'paragraph');
    if (paragraph?.position) overview = business.slice(paragraph.position.start.offset, paragraph.position.end.offset).trim();
  }
  const start = timeline.find(event => event.kind === 'execution' && event.actor === 'service'
    && event.summary === '启动自愈 Agent' && event.payload.execution_id);
  let end: RecoveryTimelineEvent | undefined;
  if (state.completionReason === 'user_continued') {
    end = timeline.find(event => event.kind === 'termination' && event.payload.completion_reason === 'user_continued');
  } else if (state.status === 'canceled') {
    end = timeline.find(event => event.kind === 'termination' && event.payload.actor_role);
  } else if (state.status === 'completed' && judgment?.effect === 'recovered') {
    end = timeline.findLast(event => {
      const candidate = event.payload.judgment as Partial<NonNullable<RecoveryStoredTask['judgment']>> | undefined;
      return event.kind === 'observation' && candidate?.effect === 'recovered' && candidate.summary === judgment.summary;
    });
  }
  return {
    ...(overview ? { overview } : {}),
    ...(start ? { startAtMs: start.at_ms } : {}),
    ...(end ? { endAtMs: end.at_ms } : {}),
    ...(state.completionReason ? { completionReason: state.completionReason } : {}),
  };
}
