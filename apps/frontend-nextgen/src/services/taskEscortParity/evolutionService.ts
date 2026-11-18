import type { Analysis, IssueQuery } from '@/domain/taskEscortParity/evolutionTypes';
import { evolutionController as api } from '@/services/backendApi/harnessFlow/evolutionController';
import {
  array,
  count,
  diagnosisView,
  issueGroupView,
  lessonView,
  match,
  object,
  suggestionView,
  text,
} from './evolutionMapper';
/** Retain HTTP semantics without retaining/logging raw prompt or session payloads. */
export async function evolutionRead<T>(read: () => Promise<T>): Promise<T> {
  try {
    return await read();
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    const e = error as { status?: number; response?: { status?: number }; message?: string };
    const status = e.status ?? e.response?.status;
    const labels: Record<number, string> = {
      403: '无权查看',
      404: '所选记录不存在',
      409: '来源或版本已变化，请刷新',
      503: '服务暂不可用',
    };
    throw new Error(
      status ? `${status}：${labels[status] ?? '读取失败'}` : error instanceof Error ? error.message : '读取失败',
    );
  }
}
export const evolutionService = {
  access: (workflowId: string, userId: string, signal: AbortSignal) =>
    evolutionRead(async () => {
      const d = await api.access(workflowId, userId, signal);
      object(d);
      match(d.workflowId, workflowId);
      if (typeof d.canView !== 'boolean' || typeof d.canEdit !== 'boolean') throw new Error('权限响应协议不完整');
      return { workflowId, canView: d.canView, canEdit: d.canEdit };
    }),
  groups: (workflowId: string, userId: string, query: IssueQuery, signal: AbortSignal) =>
    evolutionRead(async () => {
      const d = await api.groups(workflowId, userId, query, signal);
      object(d);
      const p = object(d.page),
        f = object(d.facets);
      return {
        groups: array(d.groups).map((g) => issueGroupView(g, workflowId)),
        facets: { nodes: array(f.nodes as string[]).map(text), modes: array(f.modes as string[]).map(text) },
        page: {
          page: count(p.page),
          pageSize: count(p.pageSize),
          total: count(p.total),
          totalPages: count(p.totalPages),
        },
      };
    }),
  group: (
    workflowId: string,
    userId: string,
    signature: string,
    versionScope: IssueQuery['versionScope'],
    signal: AbortSignal,
  ) =>
    evolutionRead(async () => {
      const d = await api.groups(workflowId, userId, { signature, versionScope }, signal);
      const groups = array(d.groups).map((g) => issueGroupView(g, workflowId));
      const group = groups.find((g) => g.signature === signature);
      if (!group) throw new Error('404：该问题已更新或不存在，请刷新');
      return group;
    }),
  diagnoses: (
    workflowId: string,
    userId: string,
    query: { flowId?: string; analysisId?: string; limit: number; offset: number },
    signal: AbortSignal,
  ) =>
    evolutionRead(async () => {
      const d = await api.diagnoses(workflowId, userId, query, signal);
      return {
        diagnoses: array(d.diagnoses).map((v) => diagnosisView(v, workflowId)),
        total: count(d.total),
        limit: count(d.limit),
        offset: count(d.offset),
      };
    }),
  analysis: (workflowId: string, flowId: string, analysisId: string | undefined, userId: string, signal: AbortSignal) =>
    evolutionRead(async (): Promise<Analysis | null> => {
      const d = await api.analysis(flowId, analysisId, userId, signal);
      object(d);
      if (d.analysis === null) return null;
      const a = d.analysis;
      object(a);
      match(a.workflowId, workflowId);
      match(a.flowId, flowId);
      if (analysisId) match(a.analysisId, analysisId);
      return {
        analysisId: text(a.analysisId),
        workflowId,
        flowId,
        status: text(a.status),
        evidenceStatus: a.evidenceStatus ?? null,
        facts: array(a.facts).map(text),
        inferences: array(a.inferences).map(text),
        unknowns: array(a.unknowns).map(text),
        diagnoses: array(a.diagnoses).map(object),
      };
    }),
  suggestions: (
    workflowId: string,
    userId: string,
    query: { status?: string; limit: number; offset: number },
    signal: AbortSignal,
  ) =>
    evolutionRead(async () => {
      const d = await api.suggestions(workflowId, userId, query, signal);
      return {
        suggestions: array(d.suggestions).map(suggestionView),
        total: count(d.total),
        limit: d.limit === undefined ? query.limit : count(d.limit),
        offset: d.offset === undefined ? query.offset : count(d.offset),
      };
    }),
  applyTasks: (ids: string[], userId: string, signal: AbortSignal) =>
    evolutionRead(async () => {
      if (!ids.length) return [];
      const d = await api.applyTasks(ids, userId, signal);
      return array(d.tasks).map((t) => {
        if (!ids.includes(t.suggestionId)) throw new Error('应用记录身份不匹配');
        return {
          taskId: text(t.taskId),
          suggestionId: text(t.suggestionId),
          status: text(t.status),
          summary: t.summary ?? null,
          errorMessage: t.errorMessage ?? null,
          proposal: t.proposal ?? null,
          progress: t.progress ?? null,
        };
      });
    }),
  lessons: (
    workflowId: string,
    userId: string,
    query: { status?: string; query?: string; limit?: number },
    signal: AbortSignal,
  ) =>
    evolutionRead(async () => {
      const d = await api.lessons(workflowId, userId, { ...query, limit: 100 }, signal);
      return {
        lessons: array(d.lessons)
          .map(lessonView)
          .map((l) => {
            if (l.workflowId !== null) match(l.workflowId, workflowId);
            return l;
          }),
        total: count(d.total),
        limit: count(d.limit),
        offset: count(d.offset),
      };
    }),
};
