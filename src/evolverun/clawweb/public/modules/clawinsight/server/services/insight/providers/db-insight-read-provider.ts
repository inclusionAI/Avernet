import type {
  InsightOverview,
  InsightQueryScope,
  InsightTrend,
  InsightAdminTrendMetrics,
  FailureTaskIndex,
  FailureTaskPage,
  FailureTaskQuery,
} from "../contracts.js";
import type { InsightTaskIndexRepository } from "../../../repositories/insight-task-index-repository.js";
import type { InsightMetricDailyRepository } from "../../../repositories/insight-metric-daily-repository.js";
import type { InsightReadProvider } from "./insight-read-provider.js";

export class DbInsightReadProvider implements InsightReadProvider {
  constructor(
    private readonly taskRepo: InsightTaskIndexRepository,
    private readonly metricRepo: InsightMetricDailyRepository,
  ) {}

  getOverview(scope: InsightQueryScope): Promise<InsightOverview> {
    return this.metricRepo.getOverview(scope);
  }

  getTrend(scope: InsightQueryScope): Promise<InsightTrend> {
    return this.metricRepo.getTrend(scope);
  }

  getAdminTrendMetrics(scope: InsightQueryScope): Promise<InsightAdminTrendMetrics> {
    return this.metricRepo.getAdminTrendMetrics(scope);
  }

  listFailureTasks(query: FailureTaskQuery): Promise<FailureTaskPage> {
    return this.taskRepo.listFailureTasks(query);
  }

  getFailureTask(ownerUserId: string, sessionId: string, taskIndex: number): Promise<FailureTaskIndex | null> {
    return this.taskRepo.getFailureTask(ownerUserId, sessionId, taskIndex);
  }
}
