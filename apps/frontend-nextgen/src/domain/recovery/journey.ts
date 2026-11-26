import {
  guidanceBody,
  object,
  readableReport,
  recordedOpening,
  reportSection,
  text,
  userContinuationEvent,
} from './report';
import type { RecoveryEvent, RecoveryTask } from './types';

export const planAnchor = (event: RecoveryEvent, latest: RecoveryEvent | undefined) =>
  event === latest ? 'recovery-plan-document' : 'plan-document-' + encodeURIComponent(event.event_id);

type Entry = {
  title: string;
  body?: string;
  details?: Array<{ label: string; body: string }>;
  tone?: 'success' | 'needs-attention' | 'failed';
  status?: string;
  originalMessage?: string;
  cardContent?: string;
  phase?: 'verify';
  progress?: { label: string; state?: string; approval?: boolean };
  link?: { label: string; target: string };
};

export const recoveryEntryAnchor = (id: string) => 'recovery-entry-' + encodeURIComponent(id);

function operationContent(event: RecoveryEvent) {
  const request = object(event.payload.request);
  const result = object(event.payload.result);
  return {
    request,
    result,
    toolName: text(event.payload.tool_name) || text(request.operation),
    status: text(event.payload.status),
    message: text(result.message) || text(request.message),
    error: text(result.error) || text(object(result.error).message),
  };
}

function readableRecoveryText(value: string) {
  return readableReport(
    guidanceBody(value)
      .replace(/（第\d+(?:[-–至]\d+)?行）/g, '')
      .trim(),
  );
}

function cooperationDetails(request: NonNullable<RecoveryTask['ownerRequest']>) {
  return [
    { label: '需要你配合的原因', body: readableRecoveryText(request.summary) },
    { label: '卡片中的处理要求', body: readableRecoveryText(request.instructions) },
  ].filter((detail) => detail.body);
}

function ownerCardRequest(event: RecoveryEvent) {
  const p = event.payload;
  return event.kind === 'notification' &&
    p.channel === 'owner_card' &&
    p.operation === 'create' &&
    p.kind === 'owner_request' &&
    p.sent === true
    ? text(p.owner_request_id)
    : '';
}

function outcomeEntry(task: RecoveryTask, event: RecoveryEvent): Entry | undefined {
  const judgment = object(event.payload.judgment);
  if (!['recovered', 'not_recovered', 'unknown'].includes(text(judgment.effect))) return;
  const current = task.judgment?.summary === judgment.summary;
  const summary = text(judgment.summary);
  const response = reportSection(summary, ['响应', 'Bot 是否继续处理']);
  const recorded = response ? readableRecoveryText(response) : '';
  const action = recordedOpening(recorded).replace(/，(?:依次)?执行了[：:]$/, '。');
  const reply = recorded
    .split('\n')
    .find((line) => /^\s*(?:\d+\.\s+|[-*]\s+)(?:已)?(?:回复|告知)用户/.test(line))
    ?.replace(/^\s*(?:\d+\.\s+|[-*]\s+)/, '');
  const responseText = [action, reply].filter(Boolean).join('\n\n');
  return {
    title: '核验原任务',
    phase: 'verify',
    status: judgment.effect === 'recovered' ? '已恢复' : judgment.effect === 'not_recovered' ? '仍未恢复' : '待确认',
    tone:
      judgment.effect === 'recovered' ? 'success' : judgment.effect === 'not_recovered' ? 'failed' : 'needs-attention',
    body: current
      ? undefined
      : readableRecoveryText(
          recordedOpening(reportSection(summary, ['效果', '原任务结果', '恢复结果', '恢复结论']) || summary),
        ),
    details: responseText ? [{ label: 'Bot 的响应', body: responseText }] : undefined,
    link: current ? { label: '查看恢复结果', target: 'recovery-result' } : undefined,
  };
}

function messageEntry(task: RecoveryTask, event: RecoveryEvent): Entry {
  const c = operationContent(event);
  const originalMessage = guidanceBody(c.message);
  const message = recordedOpening(readableRecoveryText(originalMessage));
  const details: Entry['details'] = [];
  const purpose = readableRecoveryText(text(c.request.purpose));
  if (purpose) details.push({ label: '处理目的', body: purpose });
  if (message && message !== purpose) details.push({ label: '恢复消息摘录', body: message });
  const entry = { title: '向 Bot 发送恢复指引', progress: { label: '发送恢复指引' }, details, originalMessage };
  if (c.status === 'failed' || c.result.dispatched === false) {
    return { ...entry, status: '发送失败', body: readableRecoveryText(c.error), tone: 'failed' };
  }
  const key = text(c.result.delivery_key);
  const laterProof =
    key &&
    task.timeline.some((later) => {
      if (later.kind !== 'observation' || later.at_ms < event.at_ms) return false;
      const judgment = object(later.payload.judgment);
      const delivery = reportSection(text(judgment.summary), ['触达', '消息是否送达']);
      return judgment.delivery === 'confirmed' && Boolean(delivery?.includes(key));
    });
  if (laterProof || c.result.delivery_confirmed === true) {
    return { ...entry, status: '已送达', tone: 'success' };
  }
  const queued = c.result.status === 'accepted' || c.result.accepted === true;
  return {
    ...entry,
    status: queued ? '待确认送达' : c.result.dispatched === true ? '已发出' : '发送情况待确认',
    tone: 'needs-attention',
  };
}

export function recoveryJourney(task: RecoveryTask) {
  const events = [...task.timeline].sort((a, b) => a.at_ms - b.at_ms);
  const latestPlan = events.filter((event) => event.kind === 'plan').at(-1);
  const sentCards = new Map<string, RecoveryEvent>();
  for (const event of events) {
    const id = ownerCardRequest(event);
    if (id && !sentCards.has(id)) sentCards.set(id, event);
  }
  const waitingIds = new Set(
    events.filter((event) => event.kind === 'waiting').map((event) => text(event.payload.owner_request_id)),
  );
  const request = task.ownerRequest;
  const continuation = userContinuationEvent(task);
  const showCurrentRequest = task.status === 'waiting_owner' && request?.status === 'pending';
  const requestEntry = (sent: boolean, id: string): Entry => {
    const current = request?.requestId === id ? request : undefined;
    return {
      title: sent ? '通知负责人' : '请负责人配合处理',
      progress: { label: sent ? '发送待办卡片' : '请求负责人配合', state: sent ? '已发送' : '待处理' },
      tone: sent ? 'success' : 'needs-attention',
      status: sent ? '已发待办卡片' : undefined,
      details: current && !showCurrentRequest ? cooperationDetails(current) : undefined,
      link: current && showCurrentRequest ? { label: '查看待办', target: 'owner-cooperation' } : undefined,
    };
  };

  return events
    .flatMap((event) => {
      const p = event.payload;
      const id = text(p.owner_request_id);
      let entry: Entry | undefined;
      if (id && sentCards.get(id) === event) {
        entry = requestEntry(true, id);
      } else if (event.kind === 'waiting' && id && !sentCards.has(id)) {
        entry = requestEntry(false, id);
      } else if (event.kind === 'owner_decision' && id) {
        const current = request?.requestId === id ? request : undefined;
        const feedback = text(p.feedback) || current?.feedback?.text;
        // 展示已送出卡片所对应的配合事项记录。
        const sent = sentCards.get(id);
        const card =
          sent && sent.at_ms <= event.at_ms && current ? [current.summary, current.instructions].filter(Boolean) : [];
        const cardContent = card.length ? card.join('\n\n') : undefined;
        const title = 'Bot Owner配合处理';
        entry = { title, progress: { label: title, state: '已处理' }, tone: 'success', body: feedback, cardContent };
      } else if (event === continuation) {
        const message = event.summary.match(/用户(?:新消息|续聊)[：:]\s*「([^」]+)」/)?.[1];
        const title = '用户续聊，自愈让路';
        entry = {
          title,
          progress: { label: '自愈让路', state: '已退出' },
          tone: 'success',
          status: '已退出',
          details: message ? [{ label: '用户续聊原文', body: message }] : undefined,
          link: { label: '查看让路结果', target: 'recovery-result' },
        };
      } else if (event.kind === 'plan') {
        const saved = object(p.plan);
        const summary = text(saved.summary) || event.summary;
        entry = {
          title: '提出修复方案',
          tone: 'success',
          progress: { label: '提出修复方案', state: '已提出', approval: event === latestPlan },
          body: reportSection(summary, ['解决方案', '处理思路', '修复方案']) || recordedOpening(summary),
          link: { label: '查看完整方案', target: planAnchor(event, latestPlan) },
        };
      } else if (event.kind === 'notification' && p.kind === 'plan' && typeof p.sent === 'boolean') {
        const source = events.find(
          (candidate) => candidate.kind === 'plan' && candidate.event_id === p.source_event_id,
        );
        entry = {
          title: p.channel === 'group_webhook' ? '发送方案群通知' : '发送修复方案通知',
          progress: { label: '发送方案通知' },
          status: p.sent ? '已发送' : '发送未确认',
          tone: p.sent ? 'success' : 'needs-attention',
          link: source ? { label: '查看对应方案', target: planAnchor(source, latestPlan) } : undefined,
        };
      } else if (event.kind === 'owner_decision' && typeof p.approved === 'boolean') {
        entry = {
          title: '负责人确认方案',
          status: p.approved ? '已确认' : '未同意',
          tone: p.approved ? 'success' : 'needs-attention',
          body: text(p.feedback),
        };
      } else if (
        event.kind === 'operation' &&
        operationContent(event).toolName === 'retry_message' &&
        !task.simulation
      ) {
        entry = messageEntry(task, event);
      } else if (event.kind === 'operation' && typeof p.success === 'boolean' && !task.simulation) {
        entry = {
          title: '执行修复方案',
          status: p.applicable === false ? '方案已不适用' : p.success ? '已执行' : '未执行成功',
          body: event.summary,
          tone: p.applicable === false ? 'needs-attention' : p.success ? 'success' : 'failed',
        };
      } else if (event.kind === 'observation') {
        entry = outcomeEntry(task, event);
      }
      return entry ? [{ ...entry, id: event.event_id, atMs: event.at_ms }] : [];
    })
    .concat(
      request && !sentCards.has(request.requestId) && !waitingIds.has(request.requestId)
        ? [{ ...requestEntry(false, request.requestId), id: request.requestId, atMs: request.createdAtMs }]
        : [],
    )
    .sort((a, b) => a.atMs - b.atMs);
}
