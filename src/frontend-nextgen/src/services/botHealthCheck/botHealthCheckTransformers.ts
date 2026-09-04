import type {
  BotHealthCheckItem,
  BotHealthDimensionKey,
  BotHealthFinding,
  BotHealthFindingDetail,
  BotHealthItemStatus,
  BotHealthPatch,
  BotHealthRiskLevel,
} from '@/domain/botHealthCheck';
import type { HarnessPatchItemDto } from '@/services/backendApi';

export const dimensionLabels: Record<BotHealthDimensionKey, string> = {
  configuration: '配置健康度',
  taskUnderstanding: '任务理解力',
  planningExecution: '规划执行力',
  capabilityInvocation: '能力调用力',
  contextLearning: '上下文学习力',
  taskDelivery: '任务交付力',
};

export const dimensionDescriptions: Record<BotHealthDimensionKey, string> = {
  configuration: 'Bot 是否有基础护栏',
  taskUnderstanding: 'Bot 是否听懂任务',
  planningExecution: 'Bot 能否拆解和推进任务',
  capabilityInvocation: 'Bot 调用能力',
  contextLearning: 'Bot 能被持续养育',
  taskDelivery: '最终是否完成任务、产出可用结果',
};

export const scanDimToKey: Record<string, BotHealthDimensionKey> = {
  'full:L1': 'configuration',
  'full:L2': 'taskUnderstanding',
  'full:L3': 'planningExecution',
  'full:L4': 'capabilityInvocation',
  'full:L5': 'contextLearning',
  'full:L6': 'taskDelivery',
};

const statusLabels = new Set(['passed', 'warning', 'error', 'scanning', 'unknown']);

export const dimensionLevelByKey: Record<BotHealthDimensionKey, `L${1 | 2 | 3 | 4 | 5 | 6}`> = {
  configuration: 'L1',
  taskUnderstanding: 'L2',
  planningExecution: 'L3',
  capabilityInvocation: 'L4',
  contextLearning: 'L5',
  taskDelivery: 'L6',
};

export function pickString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

export function pickNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

export function inferStatus(
  dtoStatus?: string | null,
  grade?: string | null,
  score?: number | null,
): BotHealthItemStatus {
  const normalizedStatus = (dtoStatus ?? '').toLowerCase();
  if (['scanning', 'patching', 'pending', 'running'].includes(normalizedStatus)) return 'scanning';
  if (['failed', 'error'].includes(normalizedStatus)) return 'error';
  if (statusLabels.has(normalizedStatus)) return normalizedStatus as BotHealthItemStatus;

  const normalizedGrade = (grade ?? '').toLowerCase();
  if (['critical', 'd', 'fail', 'failed'].includes(normalizedGrade)) return 'error';
  if (['warning', 'c'].includes(normalizedGrade)) return 'warning';
  if (['excellent', 'good', 'a', 'b', 'passed', 'pass'].includes(normalizedGrade)) return 'passed';

  if (typeof score === 'number') {
    if (score >= 80) return 'passed';
    if (score >= 60) return 'warning';
    return 'error';
  }
  return 'unknown';
}

export function inferDimensionKey(scanDim?: string | null): BotHealthDimensionKey {
  const value = (scanDim ?? '').toLowerCase();
  if (scanDimToKey[value]) return scanDimToKey[value];
  if (value.includes('config')) return 'configuration';
  if (value.includes('understanding') || value.includes('understand') || value.includes('理解'))
    return 'taskUnderstanding';
  if (value.includes('plan') || value.includes('execution') || value.includes('规划') || value.includes('执行')) {
    return 'planningExecution';
  }
  if (value.includes('capability') || value.includes('tool') || value.includes('call') || value.includes('能力')) {
    return 'capabilityInvocation';
  }
  if (value.includes('context') || value.includes('learning') || value.includes('上下文') || value.includes('学习')) {
    return 'contextLearning';
  }
  if (value.includes('delivery') || value.includes('交付')) return 'taskDelivery';
  return 'configuration';
}

export function parseJsonField<T>(value: unknown): T | undefined {
  if (value === null || value === undefined) return undefined;
  if (typeof value === 'object') return value as T;
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (parsed === null || parsed === undefined) return undefined;
      return parsed as T;
    } catch {
      return undefined;
    }
  }
  return undefined;
}

export function parseFindingsSummary(value: unknown): Record<string, number> | undefined {
  const parsed = parseJsonField<Record<string, unknown>>(value);
  if (!parsed) return undefined;
  return Object.entries(parsed).reduce<Record<string, number>>((acc, [key, item]) => {
    if (typeof item === 'number' && Number.isFinite(item)) acc[key] = item;
    return acc;
  }, {});
}

export function normalizeCheckResult(value: unknown): BotHealthCheckItem['result'] {
  if (!value) return null;
  const v = String(value).toLowerCase();
  if (['pass', 'passed'].includes(v)) return 'pass';
  if (['warn', 'warning'].includes(v)) return 'warning';
  if (['fail', 'failed'].includes(v)) return 'fail';
  if (['error'].includes(v)) return 'error';
  return null;
}

export function normalizeRiskLevel(value: unknown): BotHealthRiskLevel {
  const v = String(value).toLowerCase();
  if (['critical', 'high'].includes(v)) return 'critical';
  if (['warning', 'medium'].includes(v)) return 'warning';
  return 'info';
}

export function normalizeEvidence(value: unknown): Record<string, unknown> | null {
  if (!value) return null;
  if (typeof value === 'object') return value as Record<string, unknown>;
  return parseJsonField<Record<string, unknown>>(value) ?? null;
}

export function parseFindingDetails(details: unknown): BotHealthFindingDetail[] {
  const items = parseJsonField<unknown[]>(details) ?? [];
  return items
    .map((item): BotHealthFindingDetail | undefined => {
      const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {};
      const result = normalizeCheckResult(record.result);
      const risk = normalizeRiskLevel(record.risk_level);
      const name = pickString(record.name) ?? '子检测项';
      const message = pickString(record.message) ?? '';
      if (!result) return undefined;
      return {
        rule_id: String(record.rule_id ?? ''),
        name,
        message,
        risk_level: risk,
        result,
        score: pickNumber(record.score) ?? null,
        suggested_template_ids: Array.isArray(record.suggested_template_ids)
          ? record.suggested_template_ids.map((id) => Number(id))
          : [],
        patch_id_list: Array.isArray(record.patch_id_list) ? record.patch_id_list.map((id) => String(id)) : [],
      };
    })
    .filter((item): item is BotHealthFindingDetail => Boolean(item));
}

export function parseFindings(value: unknown): BotHealthFinding[] | undefined {
  const items = parseJsonField<unknown[]>(value) ?? [];
  const findings = items
    .map((item): BotHealthFinding | undefined => {
      const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {};
      const checkItem = pickString(record.check_item) ?? pickString(record.checkItem) ?? '';
      const details = parseFindingDetails(record.finding_details);
      if (!checkItem && details.length === 0) return undefined;
      return {
        check_item: checkItem,
        all_patch_id_list: Array.isArray(record.all_patch_id_list)
          ? record.all_patch_id_list.map((id) => String(id))
          : [],
        finding_details: details,
      };
    })
    .filter((item): item is BotHealthFinding => Boolean(item));
  return findings.length ? findings : undefined;
}

export function parseCheckItems(value: unknown): BotHealthCheckItem[] | undefined {
  const items = parseJsonField<unknown[]>(value) ?? [];
  const result = items
    .map((item, index): BotHealthCheckItem | undefined => {
      if (typeof item === 'string') {
        const name = item.trim();
        if (!name) return undefined;
        return { name, status: 'passed', result: 'pass' };
      }
      if (!item || typeof item !== 'object') return undefined;
      const record = item as Record<string, unknown>;
      const result = normalizeCheckResult(record.result ?? record.status);
      const name =
        pickString(record.name) ??
        pickString(record.title) ??
        pickString(record.item_name) ??
        pickString(record.check_name) ??
        pickString(record.check_item) ??
        pickString(record.label) ??
        `检测项 ${index + 1}`;
      const rawStatus =
        typeof record.status === 'string'
          ? record.status
          : typeof record.result === 'string'
          ? record.result
          : 'unknown';
      const status: BotHealthCheckItem['status'] =
        rawStatus.toLowerCase() === 'passed' || rawStatus.toLowerCase() === 'pass'
          ? 'passed'
          : rawStatus.toLowerCase() === 'warning' || rawStatus.toLowerCase() === 'warn'
          ? 'warning'
          : ['failed', 'fail', 'error'].includes(rawStatus.toLowerCase())
          ? 'error'
          : ['scanning', 'running', 'patching', 'pending'].includes(rawStatus.toLowerCase())
          ? 'scanning'
          : 'unknown';
      const resultDetail =
        pickString(record.result_detail) ?? pickString(record.resultDetail) ?? pickString(record.detail) ?? null;
      return {
        name,
        checkItem: pickString(record.check_item) ?? name,
        note: pickString(record.note),
        status,
        result,
        resultDetail,
        score: pickNumber(record.score ?? record.health_score ?? record.value),
        repairSuggestion:
          pickString(record.repair_suggestion) ?? pickString(record.repairSuggestion) ?? pickString(record.suggestion),
        riskLevel: pickString(record.risk_level) ?? pickString(record.riskLevel),
        evidence: normalizeEvidence(record.evidence),
        conclusion:
          resultDetail ??
          pickString(record.conclusion) ??
          pickString(record.description) ??
          pickString(record.message) ??
          pickString(record.failed_reason),
        badCase:
          pickString(record.bad_case) ??
          pickString(record.badCase) ??
          pickString(record.case) ??
          pickString(record.bad_case_desc),
      };
    })
    .filter((item): item is BotHealthCheckItem => Boolean(item));
  return result.length ? result : undefined;
}

export function parsePatch(item: HarnessPatchItemDto): BotHealthPatch {
  return {
    patch_id: item.patch_id ?? 0,
    name: item.name ?? '补丁',
    description: item.description ?? null,
    is_applied: item.is_applied ?? false,
    layer: (item.layer as BotHealthPatch['layer']) ?? null,
    operations: Array.isArray(item.operations)
      ? item.operations.map((op) => ({
          op: op.op ?? '',
          target: op.target ?? '',
          template: op.template ?? null,
          detail: op.detail,
        }))
      : undefined,
    gmt_create: item.gmt_create ?? null,
    is_advise: item.is_advise ?? false,
    advise: item.advise ? { advise_content: item.advise } : null,
  };
}

export function parsePatches(value: unknown): BotHealthPatch[] {
  const items = (Array.isArray(value) ? value : []) as HarnessPatchItemDto[];
  return items.map(parsePatch);
}
