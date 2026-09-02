import type { TaskListItem } from '@/domain/tasks/models';
import { listTasks as listTaskController } from '@/services/backendApi/tasks/taskController';
import type { BackendApiPage } from '@/services/backendApi/types';

// re-export 信封成败判定：pages 层(useMyTaskTasks)受 import-boundaries 门禁不可直接依赖
// @/services/backendApi/types，由 service 层转出，保持全库判定逻辑单一收口(见 types.ts isEnvelopeFailure)。
export { isEnvelopeFailure } from '@/services/backendApi/types';

/** 我的任务列表查询参数（对齐 GET /api/v1/collaboration/tasks/list）。 */
export interface ListMyTaskParams {
  user_id: string;
  status?: string;
  /** 服务端分页页码(1-based)；与 page_size 同时传 → 走服务端分页。 */
  page?: number;
  /** 每页条数(1-100)；与 page 同时传。 */
  page_size?: number;
}

export type ListMyTaskData = TaskListItem[] | BackendApiPage<TaskListItem>;

/**
 * 我的任务列表「状态 Tab」是产品态(DRAFTING/DEFINED/EXECUTING/REVIEWING/DONE/FAILED/CANCELLED);
 * 后端 GET /api/v1/collaboration/tasks/list 的 status 入参是运行时态枚举
 * (PENDING/PLANNING/RUNNING/DONE/FAILED/HUNG/CANCELLED),支持逗号分隔多值做 IN 过滤。
 * 这里把前端 Tab 的产品态反查为运行时态集合(对齐后端 runtime_status_to_product_status 逆映射;
 * DRAFTING 为作者态,运行时不产生 → 空集合,由上层短路返回空)。
 */
export const PRODUCT_STATUS_TO_RUNTIME_STATUSES: Record<string, string[]> = {
  DRAFTING: [],
  DEFINED: ['PENDING'],
  EXECUTING: ['PLANNING', 'RUNNING'],
  REVIEWING: ['HUNG'],
  DONE: ['DONE'],
  FAILED: ['FAILED'],
  CANCELLED: ['CANCELLED'],
};

/**
 * 产品态 Tab → 运行时态集合:
 * 'all' → undefined(不过滤,查全量);
 * 已识别产品态 → 运行时态数组(可能为空,如 DRAFTING);
 * 未识别 → 空数组。
 */
export function runtimeStatusesFromProductFilter(statusFilter: string): string[] | undefined {
  if (statusFilter === 'all') return undefined;
  return PRODUCT_STATUS_TO_RUNTIME_STATUSES[statusFilter] ?? [];
}

export interface ListMyTaskResult {
  code?: number;
  message?: string;
  data?: ListMyTaskData | null;
}

export interface NormalizedMyTaskPage {
  items: TaskListItem[];
  total: number;
  page: number;
  pageSize: number;
}

function clampTaskPageItems(items: TaskListItem[], page: number, pageSize: number): TaskListItem[] {
  if (pageSize <= 0 || items.length <= pageSize) {
    return items;
  }
  const start = Math.max(0, (page - 1) * pageSize);
  const end = start + pageSize;
  return items.slice(start, end);
}

export function normalizeMyTaskPage(
  data?: ListMyTaskData | null,
  fallbackPage = 1,
  fallbackPageSize = 10,
): NormalizedMyTaskPage {
  const page = fallbackPage > 0 ? fallbackPage : 1;
  const pageSize = fallbackPageSize > 0 ? fallbackPageSize : 10;

  if (Array.isArray(data)) {
    return {
      items: clampTaskPageItems(data, page, pageSize),
      total: data.length,
      page,
      pageSize,
    };
  }
  // 服务端已分页：直接采用后端返回的 items（一页）与 total，不再在前端二次切片。
  const pageItems = Array.isArray(data?.items) ? data.items : [];
  const total = typeof data?.total === 'number' ? data.total : pageItems.length;
  const normalizedPage = typeof data?.page === 'number' && data.page > 0 ? data.page : page;
  const normalizedPageSize =
    typeof data?.pageSize === 'number' && data.pageSize > 0
      ? data.pageSize
      : typeof data?.page_size === 'number' && data.page_size > 0
      ? data.page_size
      : pageSize;
  return {
    items: pageItems,
    total,
    page: normalizedPage,
    pageSize: normalizedPageSize,
  };
}

export function listMyTasks(params: ListMyTaskParams, baseUrl?: string): Promise<ListMyTaskResult> {
  return listTaskController(params, baseUrl) as Promise<ListMyTaskResult>;
}
