export type TaskType = 'diagnose' | 'bench' | 'bench_optimize' | 'optimize' | 'full';
export const taskTypes: { type: TaskType; label: string; group: string; description: string }[] = [
  {
    type: 'diagnose',
    label: 'Bot 诊断任务',
    group: '诊断类',
    description: '分析 Bot 历史 Session，提取问题与 Good/Bad Case，并生成目标规划。',
  },
  {
    type: 'bench',
    label: 'Bench 诊断任务',
    group: '诊断类',
    description: '使用已发布的评测模板评测 Bot，查看结果与 Session。',
  },
  {
    type: 'bench_optimize',
    label: 'Bench 优化任务',
    group: '优化类',
    description: '选择训练与测试评测模板，按目标优化并独立验证。',
  },
  {
    type: 'optimize',
    label: '诊断后优化任务',
    group: '优化类',
    description: '复用已完成诊断和 Plan 的任务，继续优化。',
  },
  { type: 'full', label: 'Bot 自进化', group: '全流程类', description: '按一句话目标执行规划、优化与验证。' },
];
export type JsonObject = Record<string, unknown>;
export type Timestamp = number | string;
export interface Step {
  stepId: string;
  stepType: string;
  status: string;
  roundNo?: number | null;
  summary?: string | null;
  command?: string;
  output?: JsonObject | null;
  startedAt?: Timestamp | null;
  completedAt?: Timestamp | null;
  botResponse?: JsonObject | null;
  error?: { code?: string | null; message?: string | null; retryable?: boolean | null } | null;
}
export interface Task {
  task_id: string;
  task_type: string;
  task_name: string;
  remark?: string | null;
  user_id: string;
  bot_id: string;
  created_by: string;
  status: string;
  gmt_create?: Timestamp;
  started_at?: Timestamp | null;
  completed_at?: Timestamp | null;
  config: JsonObject;
  interactions?: { interactionId: string; stepId: string; status: string; question: JsonObject; answer?: unknown }[];
  steps?: Step[];
}
export interface TaskPage {
  tasks: Task[];
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  canViewAll: boolean;
}
export interface ModelOptions {
  defaultModel: string;
  models: string[];
}
export interface Domain {
  domainId: string;
  name: string;
  description: string | null;
  ownerUserId: string;
  status: string;
  templateCount: number;
}
export interface Template {
  templateName: string;
  displayName: string | null;
  description: string | null;
  status: string;
  latestVersion: number;
  publishedVersion: number | null;
  versions: { version: number; status: string; contentMd?: string }[];
}
export interface BenchRun {
  benchRunId: string;
  domainId: string;
  ownerUserId: string;
  templateName: string;
  templateVersion: number;
  runScope?: string;
  templateCount?: number | null;
  suite?: string | null;
  scene?: string | null;
  clawmindFlowId?: string | null;
  tokenUsage?: BenchTokenUsage | null;
  completedAt?: number | null;
  status: string;
  score: number | null;
  maxScore: number | null;
  passRate: number | null;
  model: string | null;
  startedAt: number | null;
  summary: JsonObject | null;
  errorText: string | null;
}
const auxiliaryLabels: Record<string, string> = { pack: '创建 Pack', pack_restore: '应用 Pack' };
export const labelOf = (type: string) =>
  taskTypes.find((item) => item.type === type)?.label ?? auxiliaryLabels[type] ?? type;
export const isTaskType = (type: string): type is TaskType => taskTypes.some((item) => item.type === type);
export const statuses: Record<string, string> = {
  created: '待派发',
  pending: '等待启动',
  accepted: '已接收',
  dispatched: '已派发',
  running: '运行中',
  waiting_approval: '等待审批',
  waiting_acceptance: '等待验收',
  waiting_context: '等待补充上下文',
  succeeded: '已完成',
  completed: '已完成',
  failed: '失败',
  canceled: '已取消',
  cancelled: '已取消',
};
export const statusLabel = (value: string) => statuses[value] ?? value;
export const activeStatus = (value: string) =>
  ['created', 'pending', 'accepted', 'dispatched', 'running'].includes(value);
/** Same timestamp units as the existing ClawWeb: numeric seconds/ms or a date string. */
export function timestampMs(value?: Timestamp | null): number | null {
  if (value === null || value === undefined || value === '') return null;
  const numeric = typeof value === 'number' ? value : /^\d+(?:\.\d+)?$/.test(value.trim()) ? Number(value) : null;
  const time = numeric !== null ? (numeric < 10_000_000_000 ? numeric * 1000 : numeric) : new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}
export const formatTime = (value?: Timestamp | null) => {
  const time = timestampMs(value);
  return time === null ? '—' : new Date(time).toLocaleString();
};
export function canManage(task: Task, actor: string, admin: boolean): boolean {
  return admin || task.created_by === actor;
}
export function completedDiagnosis(task: Task): boolean {
  return (
    !task.config.targetSkillAssetId &&
    ['diagnose', 'plan'].every((type) => task.steps?.some((s) => s.stepType === type && s.status === 'succeeded'))
  );
}
export interface BenchCase {
  tokenUsage?: BenchTokenUsage | null;
  executionTimeMs?: number | null;
  gradingType?: string | null;
  resultId: string;
  taskId: string;
  taskName: string | null;
  status: string;
  score: number | null;
  maxScore: number | null;
  errorText: string | null;
  resultJson: JsonObject | null;
  breakdown: JsonObject | null;
}
export interface BenchSession {
  artifactId: string;
  taskId: string | null;
  filename: string | null;
  eventCount: number | null;
  totalTokens: number | null;
}

export const isBotEvolutionTask = (task: Task) => isTaskType(task.task_type) && !task.config.targetSkillAssetId;
/** Match the existing server's last-node rule, including Skill initialization failures. */
export function canRetryStep(task: Task, step: Step): boolean {
  if (!['failed', 'canceled'].includes(step.status)) return false;
  const others = (task.steps ?? []).filter((s) => s.stepId !== step.stepId);
  const terminal = (s: Step) => ['succeeded', 'completed', 'failed', 'canceled'].includes(s.status);
  const failedInit =
    step.stepType === 'skill_init' &&
    others.every(
      (s) => (s.status === 'created' && s.stepType !== 'skill_init') || (terminal(s) && s.stepType === 'skill_init'),
    );
  return failedInit || (task.steps?.at(-1)?.stepId === step.stepId && others.every(terminal));
}
export function canCancelStep(task: Task, step: Step): boolean {
  if (!['created', 'dispatched', 'running', 'waiting_context'].includes(step.status)) return false;
  const others = (task.steps ?? []).filter((s) => s.stepId !== step.stepId);
  if (
    step.status === 'created' &&
    step.stepType !== 'skill_init' &&
    others.some((s) => s.stepType === 'skill_init' && activeStatus(s.status))
  )
    return false;
  return (
    task.steps?.at(-1)?.stepId === step.stepId ||
    (step.stepType === 'skill_init' && others.every((s) => s.status === 'created' && s.stepType !== 'skill_init'))
  );
}

export interface BenchTokenUsage {
  totalTokens?: number;
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  requestCount?: number;
}

export interface SpaceBot {
  unambiguousEnvironment?: boolean;
  ownerId: string;
  botId: string;
  env: string;
  botName: string;
  activeEngine: string;
  deviceProvider?: string | null;
  botType?: string | null;
  hasServiceBot?: boolean;
}
export interface EvolutionVersion extends JsonObject {
  versionId: string;
  kind: 'initial' | 'round' | 'snapshot';
  taskId: string;
  stepId: string;
  userId: string;
  botId: string;
  round?: number | null;
  acceptanceStatus: string;
  status?: string;
  accepted?: boolean | null;
  createdAt?: number | string;
  pack?: { packId: string; status: string; artifact?: JsonObject } | null;
  scoreComparison?: { name?: string; baseline: number | null; candidate: number | null; delta: number | null } | null;
  reviewStatus?: string | null;
  specVersion?: string | null;
  diff?: { summary?: string; available?: boolean; artifactAvailable?: boolean; files?: unknown[] } | null;
}
