import type {
  BotHealthCapability,
  BotHealthCheckSummary,
  BotHealthDimension,
  BotHealthDimensionKey,
  BotHealthHistoryItem,
  BotHealthOverallStatus,
} from '@/domain/botHealthCheck';
import type {
  HarnessDimHistoryRecordItemDto,
  HarnessDimHistoryResponseDto,
  HarnessDimReportItemDto,
  HarnessDimReportResponseDto,
} from '@/services/backendApi';
import {
  dimensionDescriptions,
  dimensionLabels,
  dimensionLevelByKey,
  inferDimensionKey,
  inferStatus,
  parseCheckItems,
  parseFindings,
  parseFindingsSummary,
  parsePatches,
  pickNumber,
  pickString,
  scanDimToKey,
} from './botHealthCheckTransformers';

export function calculateStats(
  checkItems: { status: string; result: string | null }[],
  findingsSummary?: Record<string, number>,
) {
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

export function buildPlaceholderDimension(key: BotHealthDimensionKey): BotHealthDimension {
  const level = dimensionLevelByKey[key];
  return {
    key,
    label: dimensionLabels[key],
    scanDim: scanDimToKey[level] ?? '',
    description: dimensionDescriptions[key],
    score: null,
    grade: null,
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

export function ensureAllDimensions(
  dimensions: BotHealthDimension[],
  capabilityDimensions: BotHealthDimensionKey[],
): BotHealthDimension[] {
  const byKey = new Map(dimensions.map((dim) => [dim.key, dim]));
  return capabilityDimensions.map((key) => byKey.get(key) ?? buildPlaceholderDimension(key));
}

export function inferOverall(dimensions: BotHealthDimension[]): BotHealthOverallStatus {
  if (!dimensions.length) return 'unknown';
  if (dimensions.some((item) => item.status === 'scanning')) return 'scanning';
  if (dimensions.some((item) => item.status === 'error')) return 'critical';
  if (dimensions.some((item) => item.status === 'warning')) return 'warning';
  if (dimensions.every((item) => item.status === 'passed')) return 'healthy';
  return 'unknown';
}

export function latestTime(dimensions: BotHealthDimension[], history: BotHealthHistoryItem[]) {
  return [...dimensions.map((item) => item.updatedAt), ...history.map((item) => item.checkedAt)]
    .filter((item): item is string => Boolean(item))
    .sort()
    .at(-1);
}

export function filterDimension<T extends { key: BotHealthDimensionKey }>(
  items: T[],
  capability: BotHealthCapability,
): T[] {
  const allowed = new Set(capability.dimensions);
  return items.filter((item) => allowed.has(item.key));
}

export function mapBotHealthSummary(
  report: HarnessDimReportResponseDto,
  history: HarnessDimHistoryResponseDto,
  capability: BotHealthCapability,
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
