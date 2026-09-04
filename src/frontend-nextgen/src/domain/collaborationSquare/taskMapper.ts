import type { BbsTaskItem } from '@/services/backendApi/collaboration/bbsTaskController';
import { TASK_STATUS_CONFIG, type PlazaTaskStatus, type PublicTask, type TaskStatusFilter } from './types';

/**
 * 任务广场 transport DTO（snake_case）。仅用于 Mapper/Adapter 边界，不进入 Component/Hook/Store。
 * 索引签名允许 transport 携带内部字段（节点/DAG/日志/颜色 hex 等），{@link mapPublicTaskDto} 会显式丢弃。
 */
export interface PublicTaskTransport {
  task_id?: string;
  name?: string;
  goal?: string;
  acceptance_criteria?: string[];
  status?: string;
  publisher_bot_name?: string;
  publisher?: string;
  publisher_name?: string;
  published_at?: string;
  claimed_bot_name?: string;
  claimed_at?: string;
  completed_at?: string;
  output?: string;
  [key: string]: unknown;
}

// 从 TASK_STATUS_CONFIG 派生已知状态，避免重复声明导致漏改（将来新增第 5 态时自动生效）。
const KNOWN_TASK_STATUSES = new Set<PlazaTaskStatus>(Object.keys(TASK_STATUS_CONFIG) as PlazaTaskStatus[]);

function isPlazaTaskStatus(value: unknown): value is PlazaTaskStatus {
  return typeof value === 'string' && KNOWN_TASK_STATUSES.has(value as PlazaTaskStatus);
}

/**
 * 将任务广场 transport DTO 映射为只读 {@link PublicTask}。
 * - 未知/缺失 `status` 或缺失 `task_id` → 返回 `null`（安全降级，不入列）。
 * - 丢弃任何内部字段（节点/DAG/日志/颜色 hex 等），只保留广场只读字段。
 * - 缺失 `acceptance_criteria` 回退为空数组（对齐 {@link mapBotTransport} 的 capabilities 处理）。
 */
export function mapPublicTaskDto(dto: PublicTaskTransport): PublicTask | null {
  const id = dto.task_id?.trim() ?? '';
  if (!id) return null;
  if (!isPlazaTaskStatus(dto.status)) return null;

  const claimedBotName = dto.claimed_bot_name?.trim();
  const claimedAt = dto.claimed_at?.trim();
  const completedAt = dto.completed_at?.trim();
  const output = dto.output?.trim();
  const publisher = dto.publisher?.trim();
  const publisherName = dto.publisher_name?.trim();

  return {
    id,
    name: dto.name?.trim() || '未命名任务',
    goal: dto.goal?.trim() ?? '',
    acceptanceCriteria: Array.isArray(dto.acceptance_criteria) ? dto.acceptance_criteria.filter(Boolean) : [],
    status: dto.status,
    publisherBotName: dto.publisher_bot_name?.trim() || '未公开',
    publishedAt: dto.published_at?.trim() ?? '',
    ...(claimedBotName ? { claimedBotName } : {}),
    ...(claimedAt ? { claimedAt } : {}),
    ...(completedAt ? { completedAt } : {}),
    ...(output ? { output } : {}),
    ...(publisher ? { publisher } : {}),
    ...(publisherName ? { publisherName } : {}),
  };
}

/**
 * BBS 任务列表后端运行态（`status`）→ 广场只读 {@link PlazaTaskStatus} 映射。
 *
 * | 后端运行态 | 广场状态 | 语义 |
 * |---|---|---|
 * | `PENDING` | `pending_claim` | 待认领 |
 * | `HUNG` | `pending_claim` | 无人承接，回收为待认领 |
 * | `RUNNING` | `claimed` | 已认领执行中 |
 * | `DONE` | `reviewing` | 已完成执行，待验收 |
 * | `SUCCESS` | `completed` | 验收通过，已完成 |
 *
 * 其余未映射态（`CANCELLED`/`FAILED`/`PLANNING`/未知/缺失）→ `null`（丢弃，不入列）。
 * 对大小写与前后空白鲁棒（后端枚举通常大写，但防御性归一）。
 */
export function mapBbsTaskStatus(raw: string | undefined): PlazaTaskStatus | null {
  switch (raw?.trim().toUpperCase()) {
    case 'PENDING':
    case 'HUNG':
      return 'pending_claim';
    case 'RUNNING':
      return 'claimed';
    case 'DONE':
      return 'reviewing';
    case 'SUCCESS':
      return 'completed';
    default:
      return null;
  }
}

/**
 * 广场状态筛选 → BBS 原始态，用于任务广场服务端 `status` 过滤参数（GET /tasks/bbs/list?status=）。
 *
 * 后端 `status` 为单值（逗号多值 / 非法枚举 → 400），故 `pending_claim`（= PENDING 或 HUNG）目前仅映射
 * `PENDING`，HUNG 回收态暂不在该筛选下（待后端 `status` 支持逗号多值后再补 `PENDING,HUNG`）。`reviewing`
 * → `DONE`（待验收态筛选）；`completed` → `SUCCESS`。`all` / 未知 → `undefined`（不下发 `status`，不过滤）。
 */
export function mapPlazaStatusToBbsStatus(status: TaskStatusFilter): string | undefined {
  switch (status) {
    case 'claimed':
      return 'RUNNING';
    case 'reviewing':
      return 'DONE';
    case 'completed':
      return 'SUCCESS';
    case 'pending_claim':
      return 'PENDING';
    default:
      return undefined;
  }
}

/**
 * 将 `extend_props.output` 提取为详情页展示文本：
 * - 字符串 → 去空白直用；
 * - 对象且含 string 型 `content` → 取 `content`（后端包装 `{content, extra}` 形态）；
 * - 对象且含 string 型 `output` → 取 `output`（BBS 包装 `{output}` 形态）；
 * - 对象但无可用 content/output → 用整个 output 的 JSON 文本（不臆造其它字段名，原样序列化）；
 * - null / undefined / 其它原始值 → `undefined`（不展示）。
 *
 * 详情页以纯文本展示（`whitespace-pre-wrap`），不做 markdown 渲染。
 */
function toTaskOutputText(raw: unknown): string | undefined {
  if (typeof raw === 'string') return raw.trim() || undefined;
  if (raw !== null && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    const content = obj.content;
    if (typeof content === 'string') return content.trim() || undefined;
    const output = obj.output;
    if (typeof output === 'string') return output.trim() || undefined;
    const json = JSON.stringify(raw, null, 2);
    return json || undefined;
  }
  return undefined;
}

/**
 * 将 BBS 任务列表项（{@link BbsTaskItem}，GET /api/v1/collaboration/tasks/bbs/list 的 data 元素）
 * 映射为只读 {@link PublicTask}。
 *
 * 字段映射（确定的）：
 * - `id` ← `task_id`；缺失/空白 → `null`。
 * - `name` ← `title`（后端已二次解析自 `task_spec.metadata.title`）；缺失回退「未命名任务」。
 * - `goal` ← `goal`（解析自 `task_spec.goal.objective`）；缺失回退空串。
 * - `acceptanceCriteria` ← `acceptances[].description`（取 description 成 `string[]`，过滤空/缺失）；缺失 → `[]`。
 * - `status` ← {@link mapBbsTaskStatus}；未知态 → `null`（不入列）。
 * - `publisherBotName` ← `publisherNameMap[publisher]`（adapter 预先经 `resolveBotNames` 反查）；
 *   未命中兜底用 `publisher`（Bot ID）；`publisher` 为 null/空白 → `undefined`。
 * - `publisher` ← `publisher`（原始 Bot ID，详情页用于「id（name）」展示）；`publisher` 为 null/空白 → 不填。
 * - `publisherName` ← `publisher_name`（后端权威展示名，优先于反查，卡片优先用它）；缺失/空白 → 不填。
 * - `publishedAt` ← `relay_create_time`（节点建表=发布到广场时间）；缺失回退空串。
 * - `claimedBotName` ← `assignee_name`（后端权威）→ `assigneeNameMap[assignee_id]`（adapter 预先经
 *   `resolveBotNames` 反查；复合 `bot_id:owner` 已在 `resolveBotNames` 内拆 realBotId 并按原始 id 回填）
 *   → `assignee_id`（兜底）；仅当有承接者（`assignee_id` 存在）时填。
 * - `claimedAt` ← `relay_begin_time`；仅当有承接者时填。
 * - `completedAt` ← `relay_end_time`；仅当 `status` 映射为 `completed`（即 SUCCESS）或 `reviewing`（即 DONE）时填。
 * - `output` ← `extend_props.output` 经 {@link toTaskOutputText} 提取为文本（string 直用；对象含 string `content` 取 `content`、含 string `output` 取 `output`，否则对象 JSON）；缺失/null → 不填。
 */
export function mapBbsTaskItemDto(
  dto: BbsTaskItem,
  publisherNameMap: Record<string, string>,
  assigneeNameMap: Record<string, string> = {},
): PublicTask | null {
  const id = dto.task_id?.trim() ?? '';
  if (!id) return null;
  const status = mapBbsTaskStatus(dto.status);
  if (!status) return null;

  const publisherId = typeof dto.publisher === 'string' ? dto.publisher.trim() : '';
  const publisherBotName = publisherId ? publisherNameMap[publisherId] ?? publisherId : undefined;

  const assigneeId = dto.assignee_id?.trim() ?? '';
  const hasAssignee = assigneeId !== '';
  const claimedBotName = hasAssignee
    ? dto.assignee_name?.trim() || assigneeNameMap[assigneeId] || assigneeId
    : undefined;
  const claimedAt = hasAssignee ? dto.relay_begin_time?.trim() : undefined;
  const completedAt = status === 'completed' || status === 'reviewing' ? dto.relay_end_time?.trim() : undefined;
  const output = toTaskOutputText(dto.extend_props?.output);
  const publisherName = dto.publisher_name?.trim() || undefined;

  return {
    id,
    name: dto.title?.trim() || '未命名任务',
    goal: dto.goal?.trim() ?? '',
    acceptanceCriteria: Array.isArray(dto.acceptances)
      ? dto.acceptances
          .map((item) => (typeof item?.description === 'string' ? item.description.trim() : ''))
          .filter((desc): desc is string => desc !== '')
      : [],
    status,
    ...(publisherId ? { publisher: publisherId } : {}),
    ...(publisherName ? { publisherName } : {}),
    ...(publisherBotName ? { publisherBotName } : {}),
    publishedAt: dto.relay_create_time?.trim() ?? '',
    ...(claimedBotName ? { claimedBotName } : {}),
    ...(claimedAt ? { claimedAt } : {}),
    ...(completedAt ? { completedAt } : {}),
    ...(output ? { output } : {}),
  };
}

/** 解析 `publishedAt` ISO 字符串为时间戳；空/非法返回 `NaN`（用于排序兜底，不抛错）。 */
function parsePublishedTimestamp(iso: string | undefined): number {
  if (!iso) return NaN;
  const ts = new Date(iso).getTime();
  return Number.isNaN(ts) ? NaN : ts;
}

/**
 * 按 `publishedAt` 倒序排列公开任务（最新发布在前）。
 * - 用 `new Date(publishedAt)` 时间戳比较，时间戳大的在前。
 * - `publishedAt` 空/非法的项排到末尾。
 * - 相同时间戳（含同为无效）保持原相对顺序（稳定，以原索引兜底）。
 * - 返回新数组，不修改入参。
 */
export function sortPublicTasksByPublishedDesc(items: PublicTask[]): PublicTask[] {
  const indexed = items.map((item, index) => ({
    item,
    index,
    ts: parsePublishedTimestamp(item.publishedAt),
  }));
  indexed.sort((a, b) => {
    const aInvalid = Number.isNaN(a.ts);
    const bInvalid = Number.isNaN(b.ts);
    if (aInvalid || bInvalid) {
      // 任一无效 → 无效排末尾；两者都无效 → 按原序（稳定）。
      if (aInvalid && bInvalid) return a.index - b.index;
      return aInvalid ? 1 : -1;
    }
    if (a.ts !== b.ts) return b.ts - a.ts;
    return a.index - b.index;
  });
  return indexed.map(({ item }) => item);
}
