import {
  DIM_KEY_MAPPING,
  DIM_NAME_MAPPING,
  RESULT_STYLES,
  type ResultKey,
} from '@/components/BotWorkshop/BotHealthCheckDrawer/constants';
import type { BotHealthCheckItem, BotHealthDimension, BotHealthFinding, BotHealthPatch } from '@/domain/botHealthCheck';

export function formatDateTime(value?: string | null): string {
  if (!value) return '-';
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return value;
  }
}

export function formatDate(value?: string | null): string {
  if (!value) return '-';
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
  } catch {
    return value;
  }
}

export function getDimensionDisplayName(scanDim?: string | null): string {
  if (!scanDim) return '未知维度';
  return DIM_NAME_MAPPING[scanDim] ?? scanDim;
}

export function extractDimensionKey(scanDim?: string | null): string | null {
  if (!scanDim) return null;
  return DIM_KEY_MAPPING[scanDim] ?? null;
}

export function normalizeResult(value?: string | null): ResultKey {
  const v = (value ?? '').toLowerCase();
  if (['pass', 'passed'].includes(v)) return 'pass';
  if (['warn', 'warning'].includes(v)) return 'warning';
  if (['fail', 'failed'].includes(v)) return 'fail';
  if (['error'].includes(v)) return 'error';
  if (['running', 'scanning', 'patching'].includes(v)) return 'running';
  return 'pending';
}

export function resultStyle(result?: ResultKey | null) {
  return RESULT_STYLES[result ?? 'pending'];
}

export function calculateCheckStats(items: BotHealthCheckItem[]) {
  const completed = items.filter((item) => item.status !== 'pending' && item.status !== 'running');
  return {
    total: items.length,
    completed: completed.length,
    pending: items.filter((item) => item.status === 'pending' || item.status === 'running').length,
    pass: completed.filter((item) => item.result === 'pass').length,
    warning: completed.filter((item) => item.result === 'warning').length,
    fail: completed.filter((item) => item.result === 'fail').length,
    error: completed.filter((item) => item.result === 'error').length,
  };
}

export function getDimensionByKey(dimensions: BotHealthDimension[], key: string): BotHealthDimension | undefined {
  return dimensions.find((dim) => dim.key === key || dim.scanDim === key);
}

export function calculateOverallHealthScore(dimensions: BotHealthDimension[]): number | null {
  const scores = dimensions.map((item) => item.score).filter((item): item is number => typeof item === 'number');
  if (!scores.length) return null;
  return Math.round(scores.reduce((sum, item) => sum + item, 0) / scores.length);
}

export interface RepairRecord {
  id: string | number;
  patch_id?: string | number;
  name?: string;
  status: 'applied' | 'not_applied';
  gmt_create?: string | null;
}

export function deriveRepairRecords(dimension: BotHealthDimension | null): RepairRecord[] {
  const patches: BotHealthPatch[] = dimension?.patches ?? [];
  return patches
    .map((patch) => ({
      id: patch.patch_id,
      patch_id: patch.patch_id,
      name: patch.name,
      status: (patch.is_applied ? 'applied' : 'not_applied') as 'applied' | 'not_applied',
      gmt_create: patch.gmt_create ?? null,
    }))
    .sort((a, b) => {
      const ta = a.gmt_create ? new Date(a.gmt_create).getTime() : 0;
      const tb = b.gmt_create ? new Date(b.gmt_create).getTime() : 0;
      return tb - ta;
    });
}

export function findFindingForCheckItem(
  findings: BotHealthFinding[] | undefined,
  checkItemName: string,
): BotHealthFinding | undefined {
  return findings?.find((finding) => finding.check_item && finding.check_item === checkItemName);
}

export function isPatchAllApplied(patches: BotHealthPatch[]): boolean {
  return patches.length > 0 && patches.every((patch) => patch.is_advise || patch.is_applied);
}

export function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined) return '-';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function parseEvidence(evidence: Record<string, unknown> | null | undefined): {
  sourceType: string | null;
  lowScoreSessionIds: string[];
  benchmarkName: string | null;
  benchmarkScope: string | null;
} {
  if (!evidence) {
    return { sourceType: null, lowScoreSessionIds: [], benchmarkName: null, benchmarkScope: null };
  }
  const sourceType = typeof evidence.source_type === 'string' ? evidence.source_type : null;
  const rawIds = evidence.low_score_session_ids;
  const lowScoreSessionIds = Array.isArray(rawIds) ? rawIds.map((id) => String(id)).filter(Boolean) : [];
  return {
    sourceType,
    lowScoreSessionIds,
    benchmarkName: typeof evidence.benchmark_name === 'string' ? evidence.benchmark_name : null,
    benchmarkScope: typeof evidence.benchmark_scope === 'string' ? evidence.benchmark_scope : null,
  };
}
