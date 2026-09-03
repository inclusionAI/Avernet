/**
 * Bot 健康检查领域模型
 * 对齐 open-claw Harness 数据结构，但使用 TeamClaw 的 key/login。
 */

export type BotHealthDimensionKey =
  | 'configuration'
  | 'taskUnderstanding'
  | 'planningExecution'
  | 'capabilityInvocation'
  | 'contextLearning'
  | 'taskDelivery';

export type BotHealthDimensionLevel = 'L1' | 'L2' | 'L3' | 'L4' | 'L5' | 'L6';

export type BotHealthItemStatus = 'passed' | 'warning' | 'error' | 'scanning' | 'unknown';
export type BotHealthOverallStatus = 'healthy' | 'warning' | 'critical' | 'scanning' | 'unknown';

export type BotHealthCheckResult = 'pass' | 'warning' | 'fail' | 'error' | null;
export type BotHealthRiskLevel = 'critical' | 'warning' | 'info';

export interface BotHarnessContext {
  entityType: string;
  entityId: string;
  botPublishId?: string;
}

export interface BotHealthFindingDetail {
  rule_id: string;
  name: string;
  message: string;
  risk_level: BotHealthRiskLevel;
  result: Exclude<BotHealthCheckResult, null>;
  score: number | null;
  suggested_template_ids?: number[];
  patch_id_list?: string[];
}

export interface BotHealthFinding {
  check_item: string;
  all_patch_id_list?: string[];
  finding_details: BotHealthFindingDetail[];
}

export interface BotHealthCheckItem {
  /** 检测项目名称（兼容旧字段 name） */
  name: string;
  /** 适合内部表格使用的 check_item */
  checkItem?: string;
  note?: string | null;
  /** 运行状态：scanning/pending/completed 等 */
  status: string;
  /** 检测结果 */
  result: BotHealthCheckResult;
  resultDetail?: string | null;
  score?: number | null;
  repairSuggestion?: string | null;
  riskLevel?: string | null;
  evidence?: Record<string, unknown> | null;
  conclusion?: string;
  badCase?: string;
}

export interface BotHealthPatchOperation {
  op: string;
  target: string;
  section_title?: string | null;
  anchor_section?: string | null;
  diff?: string;
  op_summary?: string;
  content?: string;
  template?: string | null;
  detail?: Record<string, unknown>;
}

export interface BotHealthPatch {
  patch_id: string | number;
  name: string;
  description: string | null;
  is_applied: boolean;
  layer?: BotHealthDimensionLevel | null;
  operations?: BotHealthPatchOperation[];
  gmt_create?: string | null;
  is_advise?: boolean;
  advise?: {
    advise_content: string;
  } | null;
}

export interface BotHealthDimension {
  key: BotHealthDimensionKey;
  label: string;
  /** 后端原始 scan_dim */
  scanDim: string;
  description?: string;
  score?: number | null;
  grade?: string | null;
  /** 后端诊断任务状态，与健康质量状态 status 分开保存 */
  scanStatus?: string | null;
  status: BotHealthItemStatus;
  checkedCount?: number;
  passedCount?: number;
  warningCount?: number;
  errorCount?: number;
  pendingCount?: number;
  findingsSummary?: Record<string, number>;
  checkItems?: BotHealthCheckItem[];
  findings?: BotHealthFinding[];
  patches?: BotHealthPatch[];
  updatedAt?: string | null;
  durationMs?: number | null;
  conclusion?: string;
  triggerSource?: string | null;
  scanType?: string | null;
  scanReportType?: string | null;
  failedReason?: string | null;
  env?: string | null;
  layer?: BotHealthDimensionLevel | null;
  raw?: unknown;
}

export interface BotHealthHistoryItem {
  id: string;
  scanId?: number;
  key: BotHealthDimensionKey;
  label: string;
  scanDim: string;
  score?: number | null;
  grade?: string | null;
  status: BotHealthItemStatus;
  checkedAt?: string | null;
  durationMs?: number | null;
  triggerSource?: string | null;
  scanReportType?: string | null;
  /** 历史详情复用维度结构 */
  dimension: BotHealthDimension;
}

export interface BotHealthCheckSummary {
  botId: string;
  entityId: string;
  overallStatus: BotHealthOverallStatus;
  healthScore?: number | null;
  grade?: string | null;
  latestAt?: string | null;
  durationMs?: number | null;
  dimensions: BotHealthDimension[];
  history: BotHealthHistoryItem[];
  raw?: {
    dimReport?: unknown;
    dimHistory?: unknown;
  };
}

export interface BotHealthCapability {
  dimensions: BotHealthDimensionKey[];
  showRadar: boolean;
  showLogDetails: boolean;
  showRawSnapshot: boolean;
}

export interface BotHealthCheckTarget {
  botId: string;
  userId: string;
  botName: string;
  engine: string;
  context: BotHarnessContext;
}
