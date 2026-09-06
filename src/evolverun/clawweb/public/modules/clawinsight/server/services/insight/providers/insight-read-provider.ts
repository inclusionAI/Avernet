import type {
  InsightOverview,
  InsightQueryScope,
  InsightTrend,
  InsightAdminTrendMetrics,
  FailureTaskIndex,
  FailureTaskPage,
  FailureTaskQuery,
} from "../contracts.js";

export class InsightDataNotReadyError extends Error {
  readonly code = "DATA_NOT_READY";

  constructor(message = "效果数据尚未发布完成") {
    super(message);
    this.name = "InsightDataNotReadyError";
  }
}

export class InsightCursorError extends Error {
  readonly code = "INVALID_CURSOR";

  constructor(message = "分页游标无效或与当前查询条件不匹配") {
    super(message);
    this.name = "InsightCursorError";
  }
}

export interface InsightReadProvider {
  getOverview(scope: InsightQueryScope): Promise<InsightOverview>;
  getTrend(scope: InsightQueryScope): Promise<InsightTrend>;
  getAdminTrendMetrics(scope: InsightQueryScope): Promise<InsightAdminTrendMetrics>;
  listFailureTasks(query: FailureTaskQuery): Promise<FailureTaskPage>;
  getFailureTask(ownerUserId: string, sessionId: string, taskIndex: number): Promise<FailureTaskIndex | null>;
}
