import { getCapabilities } from '@/capabilities';
import type {
  BotHealthCapability,
  BotHealthCheckItem,
  BotHealthCheckSummary,
  BotHealthCheckTarget,
  BotHealthDimension,
  BotHealthDimensionKey,
  BotHealthFinding,
  BotHealthFindingDetail,
  BotHealthHistoryItem,
  BotHealthItemStatus,
  BotHealthOverallStatus,
  BotHealthPatch,
  BotHealthRiskLevel,
} from '@/domain/botHealthCheck';
import {
  getHarnessDimHistory,
  getHarnessDimReport,
  startHarnessDiagnose,
  type HarnessDimHistoryRecordItemDto,
  type HarnessDimHistoryResponseDto,
  type HarnessDimReportItemDto,
  type HarnessDimReportResponseDto,
  type HarnessPatchItemDto,
} from '@/services/backendApi';
import type { BotActionAvailability, BotDomain } from '@/services/botWorkshop';

const dimensionLabels: Record<BotHealthDimensionKey, string> = {
  configuration: '配置健康度',
  taskUnderstanding: '任务理解力',
  planningExecution: '规划执行力',
  capabilityInvocation: '能力调用力',
  contextLearning: '上下文学习力',
  taskDelivery: '任务交付力',
};

const dimensionDescriptions: Record<BotHealthDimensionKey, string> = {
  configuration: 'Bot 是否有基础护栏',
  taskUnderstanding: 'Bot 是否听懂任务',
  planningExecution: 'Bot 能否拆解和推进任务',
  capabilityInvocation: 'Bot 调用能力',
  contextLearning: 'Bot 能被持续养育',
  taskDelivery: '最终是否完成任务、产出可用结果',
};

const scanDimToKey: Record<string, BotHealthDimensionKey> = {
  'full:L1': 'configuration',
  'full:L2': 'taskUnderstanding',
  'full:L3': 'planningExecution',
  'full:L4': 'capabilityInvocation',
  'full:L5': 'contextLearning',
  'full:L6': 'taskDelivery',
};

const statusLabels = new Set(['passed', 'warning', 'error', 'scanning', 'unknown']);

function getHealthCapability(): BotHealthCapability {
  return getCapabilities().getBotHealthCapability().value;
}

function pickString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function pickNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function inferStatus(dtoStatus?: string | null, grade?: string | null, score?: number | null): BotHealthItemStatus {
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

function inferDimensionKey(scanDim?: string | null): BotHealthDimensionKey {
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

function parseJsonField<T>(value: unknown): T | undefined {
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

function parseFindingsSummary(value: unknown): Record<string, number> | undefined {
  const parsed = parseJsonField<Record<string, unknown>>(value);
  if (!parsed) return undefined;
  return Object.entries(parsed).reduce<Record<string, number>>((acc, [key, item]) => {
    if (typeof item === 'number' && Number.isFinite(item)) acc[key] = item;
    return acc;
  }, {});
}

function normalizeCheckResult(value: unknown): BotHealthCheckItem['result'] {
  if (!value) return null;
  const v = String(value).toLowerCase();
  if (['pass', 'passed'].includes(v)) return 'pass';
  if (['warn', 'warning'].includes(v)) return 'warning';
  if (['fail', 'failed'].includes(v)) return 'fail';
  if (['error'].includes(v)) return 'error';
  return null;
}

function normalizeRiskLevel(value: unknown): BotHealthRiskLevel {
  const v = String(value).toLowerCase();
  if (['critical', 'high'].includes(v)) return 'critical';
  if (['warning', 'medium'].includes(v)) return 'warning';
  return 'info';
}

function normalizeEvidence(value: unknown): Record<string, unknown> | null {
  if (!value) return null;
  if (typeof value === 'object') return value as Record<string, unknown>;
  return parseJsonField<Record<string, unknown>>(value) ?? null;
}

function parseFindingDetails(details: unknown): BotHealthFindingDetail[] {
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

function parseFindings(value: unknown): BotHealthFinding[] | undefined {
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

function parseCheckItems(value: unknown): BotHealthCheckItem[] | undefined {
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

function parsePatch(item: HarnessPatchItemDto): BotHealthPatch {
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

function parsePatches(value: unknown): BotHealthPatch[] {
  const items = (Array.isArray(value) ? value : []) as HarnessPatchItemDto[];
  return items.map(parsePatch);
}

function calculateStats(checkItems: BotHealthCheckItem[], findingsSummary?: Record<string, number>) {
  const completed = checkItems.filter((item) => item.status !== 'pending' && item.status !== 'running');
  if (completed.length > 0) {
    return {
      checked: checkItems.length,
      passed: completed.filter((item) => item.result === 'pass').length,
      warning: completed.filter((item) => item.result === 'warning').length,
      error: completed.filter((item) => item.result === 'fail' || item.result === 'error').length,
      pending: checkItems.filter((item) => item.status === 'pending' || item.status === 'running').length,
    };
  }
  if (findingsSummary) {
    return {
      checked:
        (findingsSummary.pass ?? 0) +
        (findingsSummary.warning ?? 0) +
        (findingsSummary.fail ?? 0) +
        (findingsSummary.error ?? 0),
      passed: findingsSummary.pass ?? 0,
      warning: findingsSummary.warning ?? 0,
      error: (findingsSummary.fail ?? 0) + (findingsSummary.error ?? 0),
      pending: 0,
    };
  }
  return {
    checked: checkItems.length,
    passed: completed.filter((item) => item.result === 'pass').length,
    warning: completed.filter((item) => item.result === 'warning').length,
    error: completed.filter((item) => item.result === 'fail' || item.result === 'error').length,
    pending: checkItems.filter((item) => item.status === 'pending' || item.status === 'running').length,
  };
}

function mapDimension(item: HarnessDimReportItemDto): BotHealthDimension {
  const key = inferDimensionKey(item.scan_dim);
  const label = dimensionLabels[key];
  const checkItems = parseCheckItems(item.check_items) ?? [];
  const findings = parseFindings(item.findings);
  const findingsSummary = parseFindingsSummary(item.findings_summary);
  const stats = calculateStats(checkItems, findingsSummary);
  const score = pickNumber(item.health_score);
  const grade = pickString(item.grade) ?? null;
  return {
    key,
    label,
    scanDim: item.scan_dim ?? scanDimToKey[key] ?? '',
    description: dimensionDescriptions[key],
    score,
    grade,
    scanStatus: pickString(item.status) ?? null,
    status: inferStatus(item.status, grade, score),
    checkedCount: stats.checked,
    passedCount: stats.passed,
    warningCount: stats.warning,
    errorCount: stats.error,
    pendingCount: stats.pending,
    findingsSummary,
    checkItems,
    findings,
    patches: parsePatches(item.patches),
    updatedAt: pickString(item.gmt_create) ?? null,
    durationMs: pickNumber(item.duration_ms),
    conclusion: pickString(item.failed_reason) ?? undefined,
    triggerSource: item.trigger_source ?? null,
    scanType: item.scan_type ?? null,
    scanReportType: (item.scan_report_type as BotHealthDimension['scanReportType']) ?? null,
    failedReason: pickString(item.failed_reason) ?? null,
    env: item.env ?? null,
    layer: (item.scan_dim?.match(/L[1-6]$/)?.[0] as BotHealthDimension['layer']) ?? null,
    raw: item,
  };
}

function mapHistory(item: HarnessDimHistoryRecordItemDto): BotHealthHistoryItem {
  const dimension = mapDimension(item);
  const key = dimension.key;
  return {
    id: String(item.id ?? `${key}-${dimension.scanDim}`),
    scanId: pickNumber(item.id),
    key,
    label: dimension.label,
    scanDim: dimension.scanDim,
    score: dimension.score,
    grade: dimension.grade,
    status: dimension.status,
    checkedAt: dimension.updatedAt,
    durationMs: dimension.durationMs,
    triggerSource: dimension.triggerSource,
    scanReportType: dimension.scanReportType,
    dimension,
  };
}
const dimensionLevelByKey: Record<BotHealthDimensionKey, `L${1 | 2 | 3 | 4 | 5 | 6}`> = {
  configuration: 'L1',
  taskUnderstanding: 'L2',
  planningExecution: 'L3',
  capabilityInvocation: 'L4',
  contextLearning: 'L5',
  taskDelivery: 'L6',
};

function buildPlaceholderDimension(key: BotHealthDimensionKey): BotHealthDimension {
  const level = dimensionLevelByKey[key];
  return {
    key,
    label: dimensionLabels[key],
    scanDim: scanDimToKey[level] ?? '',
    description: dimensionDescriptions[key],
    score: null,
    grade: null,
    scanStatus: null,
    status: 'passed',
    checkedCount: 0,
    passedCount: 0,
    warningCount: 0,
    errorCount: 0,
    pendingCount: 0,
    findingsSummary: undefined,
    checkItems: [],
    findings: undefined,
    patches: [],
    triggerSource: null,
    scanType: null,
    scanReportType: null,
    failedReason: null,
    env: null,
    layer: level,
  };
}
function ensureAllDimensions(
  dimensions: BotHealthDimension[],
  capabilityDimensions: BotHealthDimensionKey[],
): BotHealthDimension[] {
  const byKey = new Map(dimensions.map((dim) => [dim.key, dim]));
  return capabilityDimensions.map((key) => byKey.get(key) ?? buildPlaceholderDimension(key));
}
function inferOverall(dimensions: BotHealthDimension[]): BotHealthOverallStatus {
  if (!dimensions.length) return 'unknown';
  if (dimensions.some((item) => item.status === 'scanning')) return 'scanning';
  if (dimensions.some((item) => item.status === 'error')) return 'critical';
  if (dimensions.some((item) => item.status === 'warning')) return 'warning';
  if (dimensions.every((item) => item.status === 'passed')) return 'healthy';
  return 'unknown';
}

function latestTime(dimensions: BotHealthDimension[], history: BotHealthHistoryItem[]) {
  return [...dimensions.map((item) => item.updatedAt), ...history.map((item) => item.checkedAt)]
    .filter((item): item is string => Boolean(item))
    .sort()
    .at(-1);
}

function filterDimension<T extends { key: BotHealthDimensionKey }>(items: T[], capability: BotHealthCapability): T[] {
  const allowed = new Set(capability.dimensions);
  return items.filter((item) => allowed.has(item.key));
}

export function resolveBotHealthActionAvailability(bot: BotDomain, userId?: string): BotActionAvailability {
  if (bot.runtime.engine !== 'openclaw') {
    return { action: 'health-check', visible: false, enabled: false, disabledReason: '当前引擎不支持健康检查' };
  }
  if (!bot.runtime.visibleInOpenCore) {
    return { action: 'health-check', visible: false, enabled: false, disabledReason: '当前运行时不可见' };
  }
  if (!bot.harnessContext?.entityId) {
    return { action: 'health-check', visible: true, enabled: false, disabledReason: '缺少健康检查所需的实体信息' };
  }
  if (bot.lifecycle === 'offline') {
    return { action: 'health-check', visible: true, enabled: false, disabledReason: 'Bot 已下线或回收' };
  }
  if (!userId?.trim()) {
    return { action: 'health-check', visible: true, enabled: false, disabledReason: '缺少当前用户身份' };
  }
  return { action: 'health-check', visible: true, enabled: true };
}

export function toHealthCheckTarget(bot: BotDomain, userId?: string): BotHealthCheckTarget | undefined {
  const availability = resolveBotHealthActionAvailability(bot, userId);
  if (!availability.visible || !availability.enabled || !bot.harnessContext || !userId?.trim()) return undefined;
  return {
    botId: bot.id,
    userId: userId.trim(),
    botName: bot.name,
    engine: bot.runtime.engine,
    context: bot.harnessContext,
  };
}

export function mapBotHealthSummary(
  report: HarnessDimReportResponseDto,
  history: HarnessDimHistoryResponseDto,
  capability: BotHealthCapability = getHealthCapability(),
): BotHealthCheckSummary {
  const dimensions = ensureAllDimensions(
    filterDimension(
      (report.items ?? []).map((item) => mapDimension(item)),
      capability,
    ),
    capability.dimensions,
  );
  const historyItems = filterDimension(
    (history.items ?? []).map((item) => mapHistory(item)),
    capability,
  );
  const scores = dimensions.map((item) => item.score).filter((item): item is number => typeof item === 'number');
  const healthScore = scores.length ? Math.round(scores.reduce((sum, item) => sum + item, 0) / scores.length) : null;
  return {
    botId: report.bot_id,
    entityId: report.entity_id,
    overallStatus: inferOverall(dimensions),
    healthScore,
    grade: dimensions[0]?.grade ?? null,
    latestAt: latestTime(dimensions, historyItems) ?? null,
    durationMs: dimensions[0]?.durationMs ?? null,
    dimensions,
    history: historyItems,
    raw: capability.showRawSnapshot ? { dimReport: report, dimHistory: history } : undefined,
  };
}

export const botHealthCheckService = {
  getCapability: getHealthCapability,
  resolveAvailability: resolveBotHealthActionAvailability,
  toTarget: toHealthCheckTarget,
  mapSummary: mapBotHealthSummary,
  async load(target: BotHealthCheckTarget): Promise<BotHealthCheckSummary> {
    const [report, history] = await Promise.all([
      getHarnessDimReport({
        botId: target.botId,
        userId: target.userId,
        entityId: target.context.entityId,
        botPublishId: target.context.botPublishId,
      }),
      getHarnessDimHistory({
        botId: target.botId,
        userId: target.userId,
        entityId: target.context.entityId,
        botPublishId: target.context.botPublishId,
        page: 1,
        size: 20,
      }),
    ]);
    return mapBotHealthSummary(report, history);
  },
  async runDiagnose(target: BotHealthCheckTarget) {
    return startHarnessDiagnose(target.botId, target.userId, {
      entity_type: target.context.entityType,
      entity_id: target.context.entityId,
      scan_type: 'full',
      layer: 'L1',
      bot_publish_id: target.context.botPublishId,
    });
  },
};
