import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

/**
 * BBS 接力公开任务列表项（GET /api/v1/collaboration/tasks/bbs/list 的 data 数组元素）。
 *
 * 后端已二次解析出 `title`/`goal`/`acceptances`（来自 `task_spec`）：
 * - `title` 解析自 `task_spec.metadata.title`；
 * - `goal` 解析自 `task_spec.goal.objective`；
 * - `acceptances` 解析自原始验收标准列表，元素为 `{id, description}`。
 *
 * `publisher` 为发布者 Bot ID（非名称），需经 `bots/query`（`resolveBotNames`）反查为展示名；
 * 可能为 null（系统任务等）。时间戳语义见 mapper 字段映射。
 */
export interface BbsTaskAcceptanceItem {
  id?: string;
  description?: string;
}

export interface BbsTaskItem {
  task_id?: string;
  /** 解析自 task_spec.metadata.title；缺失由 mapper 回退「未命名任务」。 */
  title?: string;
  /** 解析自 task_spec.goal.objective；缺失回退空串。 */
  goal?: string;
  /** 验收标准列表；缺失回退空数组。 */
  acceptances?: BbsTaskAcceptanceItem[];
  /** 后端运行态：PENDING/RUNNING/DONE/SUCCESS/HUNG 等；未知态由 mapper 丢弃。 */
  status?: string;
  /** 发布者 Bot ID；可能为 null（系统任务等）。 */
  publisher?: string | null;
  /** 发布者展示名（后端权威，优先于 bots/query 反查）；缺失时回退反查/ID。 */
  publisher_name?: string | null;
  /** 节点建表=发布到广场时间。 */
  relay_create_time?: string;
  /** 承接者 Bot ID；存在即表示已认领。 */
  assignee_id?: string;
  /** 承接者展示名；缺失时兜底用 assignee_id。 */
  assignee_name?: string;
  /** 承接开始时间（认领时间）。 */
  relay_begin_time?: string;
  /** 承接结束时间（完成时间）。 */
  relay_end_time?: string;
  /** 扩展属性；其中 `output` 为任务产出内容（详情页展示），其余内部字段不进入领域模型。 */
  extend_props?: Record<string, unknown> | null;
}

/** BBS 任务列表分页结构（`Page{total, items}`）；`total` 为**过滤后**行数。 */
export interface BbsTaskListPage {
  total: number;
  items: BbsTaskItem[];
}

/**
 * BBS 任务列表响应信封。成功恒为 6 位码 `200000`（HTTP 200 × 1000）；
 * `data` 为分页对象 `{total, items}`，空结果为 `{total: 0, items: []}`。
 */
export interface BbsTaskListResponse extends BackendApiEnvelope<BbsTaskListPage> {
  code: 200000;
  message?: string;
  data: BbsTaskListPage;
  request_id?: string;
}

/** GET /tasks/bbs/list 查询参数：1-based 分页 + 可选 status / search_word 过滤；省略时服务端取默认 page=1, page_size=20。 */
export interface ListBbsTasksParams {
  /** 1-based 页码，默认 1，<1 → 422。 */
  page?: number;
  /** 每页条数，默认 20，范围 1~100，越界 → 422。 */
  page_size?: number;
  /** BBS 原始态单值过滤（PENDING/RUNNING/DONE/SUCCESS/HUNG 等），大小写不敏感；逗号多值 / 非法枚举 → 400。 */
  status?: string;
  /** 关键词，对 task_spec / extend_props 文本大小写不敏感 LIKE；空 / 空白 → 不过滤。 */
  search_word?: string;
}

export const BBS_TASK_ENDPOINTS = {
  list: '/api/v1/collaboration/tasks/bbs/list',
};

/**
 * 查询跨用户公开的 BBS 接力任务列表（1-based 分页 + 可选 status / search_word 过滤；`total` 为过滤后行数）。
 *
 * 端点走 `/api/v1` 内部面，与协作广场其它真实接口同层（`backendRequest`）。不注入 `user_id`：该端点全量返回
 * 公开任务，按会话态鉴权，无 owner 视图参数，与协作广场公开 Bot/群目录一致（`injectUserId: false`）。
 * 查询参数经 `backendRequest` 的 `params` 以 query string 下发（调用方按需构造，避免下发 undefined）。
 * ACE 登录拦截体由 httpClient 抛 AceLoginRedirectError；非 2xx 抛 BackendRequestError；
 * code != 200000 / 非信封形状交由 adapter 校验并经 mapListError 归一，controller 保持薄出口。
 */
export async function listBbsTasks(
  params: ListBbsTasksParams = {},
  signal?: AbortSignal,
): Promise<BbsTaskListResponse> {
  const response = await backendRequest<unknown>(BBS_TASK_ENDPOINTS.list, {
    method: 'GET',
    params: params as Record<string, unknown>,
    injectUserId: false,
    signal,
  });
  return response as BbsTaskListResponse;
}
