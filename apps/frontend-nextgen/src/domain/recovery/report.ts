import MarkdownIt from 'markdown-it';
import type { RecoveryTask } from './types';

const parser = new MarkdownIt({ html: false });

export const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

export const text = (value: unknown) => (typeof value === 'string' ? value : '');

export function reportSection(value: string, names: string[]): string | null {
  const tokens = parser.parse(value, {});
  const lines = value.split('\n');
  for (let index = 0; index < tokens.length; index++) {
    const heading = tokens[index];
    const title = tokens[index + 1]?.children
      ?.filter((node) => node.type === 'text' || node.type === 'code_inline')
      .map((node) => node.content)
      .join('')
      .trim();
    if (heading.type !== 'heading_open' || !heading.map || !title || !names.includes(title)) continue;
    const next = tokens.slice(index + 1).find((token) => token.type === 'heading_open' && token.tag <= heading.tag);
    return lines.slice(heading.map[1], next?.map?.[0]).join('\n').trim() || null;
  }
  return null;
}

export function recordedOpening(report: string) {
  const paragraph = parser.parse(report, {}).find((token) => token.type === 'paragraph_open' && !token.hidden);
  return paragraph?.map ? report.split('\n').slice(paragraph.map[0], paragraph.map[1]).join('\n').trim() : '';
}

export function outcomeReport(task: RecoveryTask) {
  return reportSection(task.judgment?.summary ?? '', ['效果', '原任务结果', '恢复结果', '恢复结论']);
}

/** The result view can show recovery evidence without repeating structured document metadata. */
export function presentOutcomeReport(report: string) {
  const lines = report.split('\n');
  const candidates = parser.parse(report, {}).flatMap((token) => {
    if (token.type !== 'list_item_open' || token.level !== 1 || !token.map) return [];
    const label = lines[token.map[0]]?.match(/^\s*(?:[-*+]|\d+[.)])\s+\*\*([^*]+)\*\*[：:]/)?.[1];
    return label ? [{ label, start: token.map[0], end: token.map[1] }] : [];
  });
  if (!candidates.some(({ label }) => label === '文档链接') || !candidates.some(({ label }) => /^文档编号/.test(label)))
    return report;
  const documentFields = candidates.filter(({ label }) =>
    /^(?:标题|文档标题|文档编号(?:[（(]doc_id[）)])?|文档链接)$/.test(label),
  );
  return lines
    .filter((_, index) => !documentFields.some(({ start, end }) => index >= start && index < end))
    .join('\n')
    .trim();
}

export function recordedDiagnosis(task: RecoveryTask) {
  const diagnosis = task.diagnosis?.diagnosisId === task.request.event_id ? task.diagnosis : undefined;
  const investigation = task.timeline.reduce<RecoveryTask['timeline'][number] | undefined>((first, event) => {
    if (!['waiting', 'assessment'].includes(event.kind) || (first && first.at_ms <= event.at_ms)) return first;
    return reportSection(event.summary, ['原任务目标']) ? event : first;
  }, undefined);
  return {
    diagnosis,
    goal: reportSection(investigation?.summary ?? '', ['原任务目标']),
    cause: reportSection(investigation?.summary ?? '', ['当前阻塞', '根因']),
    at: investigation?.at_ms,
    business: diagnosis?.businessDiagnosis || task.request.diagnosis,
    system: diagnosis?.systemDiagnosis,
  };
}

export function userContinuationEvent(task: RecoveryTask) {
  if (task.completionReason !== 'user_continued') return undefined;
  return task.timeline.find(
    (event) => event.kind === 'termination' && event.payload.completion_reason === 'user_continued',
  );
}

export function guidanceBody(value: string) {
  return value.replace(/^\[会话自愈[：:][^\]\n]+\]\r?\n/, '');
}

export function readableReport(value: string) {
  return value
    .replace(/^#{1,6}\s+触达\s*$/gm, '### 消息是否送达')
    .replace(/^#{1,6}\s+响应\s*$/gm, '### Bot 是否继续处理')
    .replace(/^#{1,6}\s+效果\s*$/gm, '### 原任务结果')
    .replace(/\*\*触达\*\*/g, '**消息是否送达**')
    .replace(/\*\*响应\*\*/g, '**Bot 是否继续处理**')
    .replace(/\*\*效果\*\*/g, '**原任务结果**');
}
