import type { IssueGroup, Snapshot } from './evolutionTypes';
export const kindLabels: Record<string, string> = {
  kb_hint: '提示',
  prompt_patch: '提示词补丁',
  arg_template_fix: '参数模板修正',
  node_patch: '节点结构补丁',
  alert: '告警',
  'adjust-timeout': '超时调整',
  'retry-as-is': '直接重试',
  'skip-retry': '跳过重试',
};
export const statusLabels: Record<string, string> = {
  draft: '草稿',
  verified: '已验证',
  published: '已上线',
  retired: '已失效',
  pending: '待处理',
  processing: '处理中',
  awaiting_verification: '待验证',
  no_action: '暂不处理',
  ineffective: '未达预期',
  failed: '失败',
  review: '待审阅',
  drafting: '生成中',
  blocked: '受阻',
  publishing: '发布中',
  cancelled: '已取消',
  no_change: '无改动',
  applied_unverified: '已应用 · 待验证',
  applying: '应用中',
  adopted: '待应用',
  rejected: '已拒绝',
  benched: '已记录',
};
export function snapshotLabel(binding: Snapshot | null | undefined): string {
  if (binding?.status !== 'resolved' || !binding.snapshot) return '版本未确认';
  const s = binding.snapshot;
  if (s.kind === 'deployment') return `工作流运行版本 ${s.workflowVersion ?? '—'} · 部署 ${s.deployNumber ?? '—'}`;
  return s.kind === 'saved' ? `保存测试 ${s.commit?.slice(0, 8) ?? '—'}` : '版本未确认';
}
/** Same browser-safe key as the verified upstream workflowSnapshotKey contract. */
export function issueSnapshotKey(binding: Snapshot | null | undefined): string {
  if (binding?.status !== 'resolved' || !binding.snapshot) return 'unresolved';
  const s = binding.snapshot;
  return JSON.stringify(
    s.kind === 'deployment'
      ? ['deployment', s.workflowId, s.workflowVersion, s.deployNumber]
      : ['saved', s.workflowId, s.repoId ?? '', s.commit, s.specPath ?? ''],
  );
}
/** Never relabel a cross-version aggregate as a single-version explanation. */
export function scopeIssueGroup(group: IssueGroup, snapshotKey: string): IssueGroup {
  const sources = group.sources.filter((s) => issueSnapshotKey(s.snapshot) === snapshotKey);
  if (!sources.length) throw new Error('404：该问题的所选历史版本来源已不存在，请刷新');
  const summarySources = group.summarySources ?? group.sources;
  const safeSummary =
    summarySources.length > 0 &&
    summarySources.every((s) => issueSnapshotKey(s.snapshot) === snapshotKey) &&
    (group.summary?.causes.every((c) => c.sourceIds.every((id) => summarySources.some((s) => s.sourceId === id))) ??
      true);
  return {
    ...group,
    sources,
    flowIds: [...new Set(sources.flatMap((s) => (s.flowIds.length ? s.flowIds : [s.flowId])))],
    summary: safeSummary ? group.summary : null,
    summarySources: safeSummary ? summarySources : null,
    inputSummary: safeSummary ? group.inputSummary : null,
    priority: null,
    summaryScopeNote: !safeSummary && group.summary ? '本版本未提供独立摘要；跨版本聚合摘要不在此展示。' : undefined,
  };
}
export function historicalVersions(groups: IssueGroup[]) {
  const versions = new Map<string, { snapshotKey: string; label: string; groups: IssueGroup[] }>();
  for (const group of groups) {
    const sets = new Map<string, typeof group.sources>();
    for (const source of group.sources) {
      const key = issueSnapshotKey(source.snapshot);
      sets.set(key, [...(sets.get(key) ?? []), source]);
    }
    for (const [snapshotKey, sources] of sets) {
      const version = versions.get(snapshotKey) ?? {
        snapshotKey,
        label: snapshotLabel(sources[0]?.snapshot),
        groups: [],
      };
      version.groups.push(scopeIssueGroup(group, snapshotKey));
      versions.set(snapshotKey, version);
    }
  }
  return [...versions.values()];
}
export function displayDate(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = typeof value === 'number' ? value : /^\d+(\.\d+)?$/.test(value) ? Number(value) : null;
  const date = n === null ? new Date(value as string) : new Date(n < 1e12 ? n * 1000 : n);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : '—';
}
