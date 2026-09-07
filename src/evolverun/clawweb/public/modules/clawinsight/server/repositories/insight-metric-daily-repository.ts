import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type {
  InsightBotComparison,
  InsightFailureDistribution,
  InsightMetricCounts,
  InsightMetricRates,
  InsightOverview,
  InsightQueryScope,
  InsightTrend,
  InsightAdminTrendMetrics,
} from "../services/insight/contracts.js";
import { INSIGHT_CONTRACT_VERSION } from "../services/insight/contracts.js";

export type UpsertInsightMetricDailyInput = {
  sourceDt: string;
  ownerUserId: string;
  botId: string;
  botName: string;
  isCron: boolean;
  totalTaskCount: number;
  validTaskCount: number;
  completeTaskCount: number;
  capabilityTaskCount: number;
  capabilityCompleteTaskCount: number;
  autoCompleteTaskCount: number;
  failureDistribution: Record<string, number>;
  batchId: string;
  dataAsOf: string;
};

type InsightMetricDailyRow = {
  source_dt: string;
  owner_user_id: string;
  bot_id: string;
  bot_name: string;
  is_cron: number;
  total_task_count: number | string;
  valid_task_count: number | string;
  complete_task_count: number | string;
  capability_task_count: number | string;
  capability_complete_task_count: number | string;
  auto_complete_task_count: number | string;
  failure_distribution_json: string | null;
  batch_id: string;
  data_as_of: string;
};

type RawCounts = {
  totalTaskCount: number;
  validTaskCount: number;
  completeTaskCount: number;
  capabilityTaskCount: number;
  capabilityCompleteTaskCount: number;
  autoCompleteTaskCount: number;
};

type Aggregate = {
  counts: RawCounts;
  failures: Map<string, number>;
};

function compactDate(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const compact = value.replaceAll("-", "").slice(0, 8);
  return /^\d{8}$/.test(compact) ? compact : undefined;
}

function timestampForDb(dbType: IDatabase["dbType"], value: Date): number | string {
  if (dbType !== "mysql" && dbType !== "zdas") return Math.floor(value.getTime() / 1000);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())} ${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`;
}

function numeric(value: number | string | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function roundedCount(value: number): number {
  // Decimal columns can expose values such as 3.4999999999999996; task counts
  // are integer quantities, so normalize the floating-point noise before rounding.
  return Math.round(value + 1e-9);
}

function emptyCounts(): RawCounts {
  return {
    totalTaskCount: 0,
    validTaskCount: 0,
    completeTaskCount: 0,
    capabilityTaskCount: 0,
    capabilityCompleteTaskCount: 0,
    autoCompleteTaskCount: 0,
  };
}

function addCounts(target: RawCounts, row: InsightMetricDailyRow): void {
  target.totalTaskCount += numeric(row.total_task_count);
  target.validTaskCount += numeric(row.valid_task_count);
  target.completeTaskCount += numeric(row.complete_task_count);
  target.capabilityTaskCount += numeric(row.capability_task_count);
  target.capabilityCompleteTaskCount += numeric(row.capability_complete_task_count);
  target.autoCompleteTaskCount += numeric(row.auto_complete_task_count);
}

function parseFailureDistribution(raw: string | null): Record<string, number> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const result: Record<string, number> = {};
    for (const [key, value] of Object.entries(parsed)) {
      const count = numeric(value as number | string);
      if (key && count > 0) result[key] = count;
    }
    return result;
  } catch {
    return {};
  }
}

function addFailures(target: Map<string, number>, row: InsightMetricDailyRow): void {
  for (const [failureClass, count] of Object.entries(parseFailureDistribution(row.failure_distribution_json))) {
    target.set(failureClass, (target.get(failureClass) ?? 0) + count);
  }
}

function aggregateRows(rows: InsightMetricDailyRow[]): Aggregate {
  const aggregate = { counts: emptyCounts(), failures: new Map<string, number>() };
  for (const row of rows) {
    addCounts(aggregate.counts, row);
    addFailures(aggregate.failures, row);
  }
  return aggregate;
}

function rate(numerator: number, denominator: number): number | null {
  return denominator > 0 ? Number((numerator / denominator).toFixed(4)) : null;
}

function displayCounts(raw: RawCounts): InsightMetricCounts {
  return {
    totalTaskCount: Math.round(raw.totalTaskCount),
    validTaskCount: Math.round(raw.validTaskCount),
    completeTaskCount: Math.round(raw.completeTaskCount),
    capabilityTaskCount: Math.round(raw.capabilityTaskCount),
    capabilityCompleteTaskCount: Math.round(raw.capabilityCompleteTaskCount),
    autoCompleteTaskCount: Math.round(raw.autoCompleteTaskCount),
  };
}

function rates(raw: RawCounts): InsightMetricRates {
  return {
    completionRate: rate(raw.completeTaskCount, raw.totalTaskCount),
    capabilityCompletionRate: rate(raw.capabilityCompleteTaskCount, raw.capabilityTaskCount),
    autoCompletionRate: rate(raw.autoCompleteTaskCount, raw.validTaskCount),
  };
}

function distribution(failures: Map<string, number>): InsightFailureDistribution[] {
  const total = [...failures.values()].reduce((sum, count) => sum + count, 0);
  return [...failures.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .map(([failureClass, count]) => ({
      failureClass,
      taskCount: Math.round(count),
      ratio: rate(count, total) ?? 0,
    }));
}

function latestMetadata(rows: InsightMetricDailyRow[]): { dataAsOf: string; batchId: string } {
  const latest = [...rows].sort((left, right) =>
    right.data_as_of.localeCompare(left.data_as_of) || right.source_dt.localeCompare(left.source_dt),
  )[0];
  return latest
    ? { dataAsOf: latest.data_as_of, batchId: latest.batch_id }
    : { dataAsOf: new Date().toISOString(), batchId: "db-empty" };
}

function stableJson(value: Record<string, number>): string {
  return JSON.stringify(Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right))));
}

export class InsightMetricDailyRepository {
  constructor(private readonly db: IDatabase) {}

  async upsertMany(items: UpsertInsightMetricDailyInput[]): Promise<{ accepted: number }> {
    if (items.length === 0) return { accepted: 0 };
    await this.db.transaction(async (tx) => {
      const now = tx.dialect.now();
      for (const item of items) {
        const params = [
          item.sourceDt,
          item.ownerUserId,
          item.botId,
          item.botName,
          item.isCron ? 1 : 0,
          item.totalTaskCount,
          item.validTaskCount,
          item.completeTaskCount,
          item.capabilityTaskCount,
          item.capabilityCompleteTaskCount,
          item.autoCompleteTaskCount,
          stableJson(item.failureDistribution),
          item.batchId,
          item.dataAsOf,
          now,
          now,
        ];
        if (tx.dbType === "mysql" || tx.dbType === "zdas") {
          await tx.exec(
            `INSERT INTO insight_metric_daily
             (source_dt, owner_user_id, bot_id, bot_name, is_cron,
              total_task_count, valid_task_count, complete_task_count,
              capability_task_count, capability_complete_task_count, auto_complete_task_count,
              failure_distribution_json, batch_id, data_as_of, gmt_create, gmt_modified)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON DUPLICATE KEY UPDATE
              bot_name = VALUES(bot_name), total_task_count = VALUES(total_task_count),
              valid_task_count = VALUES(valid_task_count), complete_task_count = VALUES(complete_task_count),
              capability_task_count = VALUES(capability_task_count),
              capability_complete_task_count = VALUES(capability_complete_task_count),
              auto_complete_task_count = VALUES(auto_complete_task_count),
              failure_distribution_json = VALUES(failure_distribution_json), batch_id = VALUES(batch_id),
              data_as_of = VALUES(data_as_of), gmt_modified = VALUES(gmt_modified)`,
            params,
          );
        } else {
          await tx.exec(
            `INSERT INTO insight_metric_daily
             (source_dt, owner_user_id, bot_id, bot_name, is_cron,
              total_task_count, valid_task_count, complete_task_count,
              capability_task_count, capability_complete_task_count, auto_complete_task_count,
              failure_distribution_json, batch_id, data_as_of, gmt_create, gmt_modified)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(owner_user_id, bot_id, source_dt, is_cron) DO UPDATE SET
              bot_name = excluded.bot_name, total_task_count = excluded.total_task_count,
              valid_task_count = excluded.valid_task_count, complete_task_count = excluded.complete_task_count,
              capability_task_count = excluded.capability_task_count,
              capability_complete_task_count = excluded.capability_complete_task_count,
              auto_complete_task_count = excluded.auto_complete_task_count,
              failure_distribution_json = excluded.failure_distribution_json, batch_id = excluded.batch_id,
              data_as_of = excluded.data_as_of, gmt_modified = excluded.gmt_modified`,
            params,
          );
        }
      }
    });
    return { accepted: items.length };
  }

  private async getRows(scope: InsightQueryScope): Promise<InsightMetricDailyRow[]> {
    const conditions: string[] = [];
    const params: unknown[] = [];
    if (scope.userId !== "*") {
      conditions.push("owner_user_id = ?");
      params.push(scope.userId);
    }
    if (scope.botId) {
      conditions.push("bot_id = ?");
      params.push(scope.botId);
    }
    const from = compactDate(scope.from);
    if (from) {
      conditions.push("source_dt >= ?");
      params.push(from);
    }
    const to = compactDate(scope.to);
    if (to) {
      conditions.push("source_dt <= ?");
      params.push(to);
    }
    if (scope.isCron !== undefined) {
      conditions.push("is_cron = ?");
      params.push(scope.isCron ? 1 : 0);
    }
    return this.db.query<InsightMetricDailyRow>(
      `SELECT source_dt, owner_user_id, bot_id, bot_name, is_cron,
              total_task_count, valid_task_count, complete_task_count,
              capability_task_count, capability_complete_task_count, auto_complete_task_count,
              failure_distribution_json, batch_id, data_as_of
       FROM insight_metric_daily${conditions.length ? ` WHERE ${conditions.join(" AND ")}` : ""}
       ORDER BY source_dt, bot_id, is_cron`,
      params,
    );
  }

  async getOverview(scope: InsightQueryScope): Promise<InsightOverview> {
    const rows = await this.getRows(scope);
    const aggregate = aggregateRows(rows);
    const metadata = latestMetadata(rows);
    const byBot = new Map<string, InsightMetricDailyRow[]>();
    for (const row of rows) {
      const groupKey = scope.userId === "*"
        ? `${row.owner_user_id}\u0000${row.bot_id}`
        : row.bot_id;
      const group = byBot.get(groupKey) ?? [];
      group.push(row);
      byBot.set(groupKey, group);
    }
    const botComparison = [...byBot.values()].map((botRows): InsightBotComparison => {
      const botAggregate = aggregateRows(botRows);
      const latestBotRow = [...botRows].sort((left, right) => right.data_as_of.localeCompare(left.data_as_of))[0];
      return {
        ownerUserId: latestBotRow?.owner_user_id,
        botId: latestBotRow?.bot_id ?? "",
        botName: latestBotRow?.bot_name || latestBotRow?.bot_id || "",
        ...displayCounts(botAggregate.counts),
        ...rates(botAggregate.counts),
      };
    }).sort((left, right) => right.totalTaskCount - left.totalTaskCount || left.botId.localeCompare(right.botId)).slice(0, 20);

    return {
      contractVersion: INSIGHT_CONTRACT_VERSION,
      dataAsOf: metadata.dataAsOf,
      sourceBatchId: metadata.batchId,
      scope: { userId: scope.userId, botId: scope.botId ?? null },
      counts: displayCounts(aggregate.counts),
      rates: rates(aggregate.counts),
      failureDistribution: distribution(aggregate.failures),
      botComparison,
    };
  }

  async getAdminTrendMetrics(scope: InsightQueryScope): Promise<InsightAdminTrendMetrics> {
    // 这两条序列只由管理员请求，并跟随管理员当前的用户/Bot/任务来源范围；ownerUserId=* 才是全站口径。
    const [allRows, repairBotScopes] = await Promise.all([
      this.getRows(scope),
      this.db.query<{ owner_user_id: string; bot_id: string }>(
        `SELECT DISTINCT owner_user_id, bot_id
           FROM insight_improvement_item
          WHERE gmt_create >= ?
            AND owner_user_id <> ''
            AND bot_id <> ''
            ${scope.userId !== "*" ? "AND owner_user_id = ?" : ""}
            ${scope.botId ? "AND bot_id = ?" : ""}
        `,
        [
          timestampForDb(this.db.dbType, new Date(Date.now() - 30 * 24 * 60 * 60 * 1000)),
          ...(scope.userId !== "*" ? [scope.userId] : []),
          ...(scope.botId ? [scope.botId] : []),
        ],
      ),
    ]);
    const repairBotKeys = new Set(
      repairBotScopes.map((row) => `${row.owner_user_id}\u0000${row.bot_id}`),
    );
    const overallTaskCountByDate: Record<string, number> = {};
    const repairBotCapabilityFailureTaskCountByDate: Record<string, number> = {};
    for (const row of allRows) {
      overallTaskCountByDate[row.source_dt] =
        (overallTaskCountByDate[row.source_dt] ?? 0) + numeric(row.total_task_count);
      if (repairBotKeys.has(`${row.owner_user_id}\u0000${row.bot_id}`)) {
        const capabilityFailureCount = Math.max(
          0,
          numeric(row.capability_task_count) - numeric(row.capability_complete_task_count),
        );
        repairBotCapabilityFailureTaskCountByDate[row.source_dt] =
          (repairBotCapabilityFailureTaskCountByDate[row.source_dt] ?? 0) + capabilityFailureCount;
      }
    }
    return {
      overallTaskCountByDate: Object.fromEntries(
        Object.entries(overallTaskCountByDate).map(([date, count]) => [date, roundedCount(count)]),
      ),
      repairBotCapabilityFailureTaskCountByDate: Object.fromEntries(
        Object.entries(repairBotCapabilityFailureTaskCountByDate).map(([date, count]) => [date, roundedCount(count)]),
      ),
    };
  }

  async getTrend(scope: InsightQueryScope): Promise<InsightTrend> {
    const rows = await this.getRows(scope);
    const metadata = latestMetadata(rows);
    const byDate = new Map<string, InsightMetricDailyRow[]>();
    for (const row of rows) {
      const group = byDate.get(row.source_dt) ?? [];
      group.push(row);
      byDate.set(row.source_dt, group);
    }
    return {
      contractVersion: INSIGHT_CONTRACT_VERSION,
      dataAsOf: metadata.dataAsOf,
      sourceBatchId: metadata.batchId,
      scope: { userId: scope.userId, botId: scope.botId ?? null },
      governanceEvents: [],
      points: [...byDate.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([date, dateRows]) => {
        const aggregate = aggregateRows(dateRows);
        return { date, ...displayCounts(aggregate.counts), ...rates(aggregate.counts) };
      }),
    };
  }
}
