import type {
  Diagnosis,
  IssueGroup,
  IssueSource,
  Lesson,
  Snapshot,
  Suggestion,
} from '@/domain/taskEscortParity/evolutionTypes';
import type {
  EvolutionDiagnosisDto,
  EvolutionGroupDto,
  EvolutionLessonDto,
  EvolutionSourceDto,
  EvolutionSuggestionDto,
} from '@/services/backendApi/harnessFlow/evolutionController';
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('响应协议不完整');
  return value as Record<string, unknown>;
}
export function array<T>(value: T[] | undefined): T[] {
  if (!Array.isArray(value)) throw new Error('响应列表协议不完整');
  return value;
}
export function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('响应身份协议不完整');
  return value;
}
export const nullableText = (v: unknown) => (typeof v === 'string' ? v : null);
export const nullableNumber = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
export function match(actual: unknown, expected: string | number) {
  if (actual !== expected) throw new Error('响应身份不匹配，已阻止跨工作流或旧详情展示');
}
export function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('响应分页协议不完整');
  return value;
}
function sourceView(s: EvolutionSourceDto): IssueSource {
  object(s);
  return {
    sourceId: text(s.sourceId),
    analysisId: text(s.analysisId),
    diagnosisId: text(s.diagnosisId),
    flowId: text(s.flowId),
    flowIds: s.flowIds === undefined ? [s.flowId] : array(s.flowIds).map(text),
    nodeId: nullableText(s.nodeId),
    failureMode: text(s.failureMode),
    reasoning: nullableText(s.reasoning),
    completedAtMs: nullableNumber(s.completedAtMs),
    evidenceEventIds: (s.evidenceEventIds ?? []).map(text),
    snapshot: (s.analysisContext?.workflowSnapshot as Snapshot) ?? null,
    proposal: s.proposal ?? null,
  };
}
export function issueGroupView(dto: EvolutionGroupDto, workflowId: string): IssueGroup {
  object(dto);
  match(dto.workflowId, workflowId);
  if (typeof dto.stale !== 'boolean') throw new Error('响应摘要状态不完整');
  const summary =
    dto.summary === null || dto.summary === undefined
      ? null
      : {
          summary: text(dto.summary.summary),
          causes: array(dto.summary.causes).map((c) => ({
            title: text(c.title),
            conclusion: text(c.conclusion),
            certainty: text(c.certainty),
            sourceIds: array(c.sourceIds).map(text),
          })),
          unknowns: array(dto.summary.unknowns).map(text),
        };
  return {
    workflowId,
    signature: text(dto.signature),
    inputDigest: text(dto.inputDigest),
    flowIds: array(dto.flowIds).map(text),
    sources: array(dto.sources).map(sourceView),
    summarySources: dto.summarySources ? array(dto.summarySources).map(sourceView) : null,
    summary,
    stale: dto.stale,
    aggregationStatus: text(dto.aggregationStatus),
    aggregationId: nullableText(dto.aggregationId),
    inputSummary: dto.inputSummary ?? dto.aggregationInputSummary ?? null,
    priority: dto.priority
      ? {
          severity: nullableText(dto.priority.severity),
          day: { affected: nullableNumber(dto.priority.day?.affected) },
          week: { affected: nullableNumber(dto.priority.week?.affected) },
          lastOccurredAtMs: nullableNumber(dto.priority.lastOccurredAtMs),
        }
      : null,
  };
}
export function lessonView(d: EvolutionLessonDto): Lesson {
  object(d);
  return {
    id: text(d.lesson_id),
    kind: text(d.fix_kind),
    status: text(d.status),
    workflowId: nullableText(d.workflow_id),
    nodeId: nullableText(d.node_id),
    signature: text(d.failure_signature),
    mode: text(d.failure_mode),
    source: nullableText(d.source),
    createdAt: typeof d.gmt_create === 'number' || typeof d.gmt_create === 'string' ? d.gmt_create : null,
    hits: nullableNumber(d.hit_count),
    rescued: nullableNumber(d.rescued_count),
    successRate: nullableNumber(d.successRate),
  };
}
export function suggestionView(d: EvolutionSuggestionDto): Suggestion {
  object(d);
  return {
    id: text(d.id),
    signature: text(d.signature),
    description: text(d.description),
    kind: text(d.kind),
    status: text(d.status),
    nodeId: nullableText(d.weakNode),
    evidenceRuns: (d.evidenceRuns ?? []).map(text),
    proposal: d.proposal ?? null,
    verificationStatus: nullableText(d.verificationStatus),
    recurrenceCount: nullableNumber(d.recurrenceCount),
  };
}
export function diagnosisView(d: EvolutionDiagnosisDto, workflowId: string): Diagnosis {
  object(d);
  match(d.workflow_id, workflowId);
  return {
    id: text(d.diagnosis_id),
    workflowId,
    flowId: text(d.flow_id),
    analysisId: nullableText(d.analysis_id),
    signature: text(d.failure_signature),
    reasoning: nullableText(d.reasoning),
    nodeId: nullableText(d.node_id),
    mode: text(d.failure_mode),
    createdAt: d.gmt_create ?? null,
  };
}
