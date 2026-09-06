import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type {
  CompletionState,
  InsightOverview,
  InsightQueryScope,
  InsightTrend,
  InsightAdminTrendMetrics,
  FailureTaskIndex,
  FailureTaskPage,
  FailureTaskQuery,
} from "../contracts.js";
import { INSIGHT_CONTRACT_VERSION } from "../contracts.js";
import {
  InsightCursorError,
  InsightDataNotReadyError,
  type InsightReadProvider,
} from "./insight-read-provider.js";

type CursorPayload = {
  offset: number;
  queryHash: string;
  dataAsOf: string;
};

type FixtureFailurePage = Omit<FailureTaskPage, "nextCursor"> & {
  nextCursor?: string | null;
};

function normalizeDate(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const compact = value.replaceAll("-", "").slice(0, 8);
  return /^\d{8}$/.test(compact) ? compact : undefined;
}

function queryFingerprint(query: FailureTaskQuery): string {
  return createHash("sha256").update(JSON.stringify({
    userId: query.userId,
    botId: query.botId ?? null,
    from: normalizeDate(query.from) ?? null,
    to: normalizeDate(query.to) ?? null,
    isCron: query.isCron ?? null,
    failureClass: query.failureClass ?? null,
    completionStates: [...(query.completionStates ?? [])].sort(),
    pageSize: query.pageSize,
  })).digest("hex");
}

function encodeCursor(cursor: CursorPayload): string {
  return Buffer.from(JSON.stringify(cursor), "utf8").toString("base64url");
}

function decodeCursor(raw: string): CursorPayload {
  try {
    const value = JSON.parse(Buffer.from(raw, "base64url").toString("utf8")) as Partial<CursorPayload>;
    if (!Number.isInteger(value.offset) || Number(value.offset) < 0 || !value.queryHash || !value.dataAsOf) {
      throw new Error("invalid cursor payload");
    }
    return value as CursorPayload;
  } catch {
    throw new InsightCursorError();
  }
}

function zeroOverview(scope: InsightQueryScope, source: InsightOverview): InsightOverview {
  return {
    contractVersion: INSIGHT_CONTRACT_VERSION,
    dataAsOf: source.dataAsOf,
    sourceBatchId: source.sourceBatchId,
    scope: { userId: scope.userId, botId: scope.botId ?? null },
    counts: {
      totalTaskCount: 0,
      validTaskCount: 0,
      completeTaskCount: 0,
      capabilityTaskCount: 0,
      capabilityCompleteTaskCount: 0,
      autoCompleteTaskCount: 0,
    },
    rates: {
      completionRate: null,
      capabilityCompletionRate: null,
      autoCompletionRate: null,
    },
    failureDistribution: [],
    botComparison: [],
  };
}

export class FixtureInsightReadProvider implements InsightReadProvider {
  private overviewCache: InsightOverview | null = null;
  private trendCache: InsightTrend | null = null;
  private failureCache: FixtureFailurePage | null = null;

  constructor(private readonly fixtureRoot: string) {}

  private async readJson<T>(fileName: string): Promise<T> {
    try {
      return JSON.parse(await readFile(join(this.fixtureRoot, fileName), "utf8")) as T;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new InsightDataNotReadyError(`本地 Insight Fixture 不可用: ${fileName}: ${message}`);
    }
  }

  private async overview(): Promise<InsightOverview> {
    this.overviewCache ??= await this.readJson<InsightOverview>("metric-overview-response.json");
    return this.overviewCache;
  }

  private async trend(): Promise<InsightTrend> {
    this.trendCache ??= await this.readJson<InsightTrend>("metric-trend-response.json");
    return this.trendCache;
  }

  private async failurePage(): Promise<FixtureFailurePage> {
    this.failureCache ??= await this.readJson<FixtureFailurePage>("failure-task-page-1.json");
    return this.failureCache;
  }

  async getOverview(scope: InsightQueryScope): Promise<InsightOverview> {
    const source = await this.overview();
    if (source.scope.userId !== scope.userId) return zeroOverview(scope, source);
    if (!scope.botId) return { ...source, scope: { userId: scope.userId, botId: null } };
    const bot = source.botComparison.find((item) => item.botId === scope.botId);
    if (!bot) return zeroOverview(scope, source);
    return {
      ...source,
      scope: { userId: scope.userId, botId: scope.botId },
      counts: {
        totalTaskCount: bot.totalTaskCount,
        validTaskCount: bot.validTaskCount,
        completeTaskCount: bot.completeTaskCount,
        capabilityTaskCount: bot.capabilityTaskCount,
        capabilityCompleteTaskCount: bot.capabilityCompleteTaskCount,
        autoCompleteTaskCount: bot.autoCompleteTaskCount,
      },
      rates: {
        completionRate: bot.completionRate,
        capabilityCompletionRate: bot.capabilityCompletionRate,
        autoCompletionRate: bot.autoCompletionRate,
      },
      botComparison: [bot],
    };
  }

  async getAdminTrendMetrics(): Promise<InsightAdminTrendMetrics> {
    return {
      overallTaskCountByDate: {},
      repairBotCapabilityFailureTaskCountByDate: {},
    };
  }

  async getTrend(scope: InsightQueryScope): Promise<InsightTrend> {
    const source = await this.trend();
    if (source.scope.userId !== scope.userId) {
      return { ...source, scope: { userId: scope.userId, botId: scope.botId ?? null }, points: [], governanceEvents: [] };
    }
    if (scope.botId && !(await this.overview()).botComparison.some((item) => item.botId === scope.botId)) {
      return { ...source, scope: { userId: scope.userId, botId: scope.botId }, points: [], governanceEvents: [] };
    }
    const from = normalizeDate(scope.from);
    const to = normalizeDate(scope.to);
    return {
      ...source,
      scope: { userId: scope.userId, botId: scope.botId ?? null },
      governanceEvents: [],
      points: source.points.filter((point) => {
        const date = normalizeDate(point.date) ?? point.date;
        return (!from || date >= from) && (!to || date <= to);
      }),
    };
  }

  async listFailureTasks(query: FailureTaskQuery): Promise<FailureTaskPage> {
    const source = await this.failurePage();
    const from = normalizeDate(query.from);
    const to = normalizeDate(query.to);
    const completionStates = new Set<CompletionState>(query.completionStates ?? [0, 2, 3]);
    const filtered = source.items.filter((item) =>
      (query.userId === "*" || item.ownerUserId === query.userId)
      && (!query.botId || item.botId === query.botId)
      && (!from || item.sourceDt >= from)
      && (!to || item.sourceDt <= to)
      && (query.isCron === undefined || item.isCron === query.isCron)
      && (!query.failureClass || item.failureClass === query.failureClass)
      && completionStates.has(item.isComplete),
    );
    const fingerprint = queryFingerprint(query);
    let offset = 0;
    if (query.cursor) {
      const cursor = decodeCursor(query.cursor);
      if (cursor.queryHash !== fingerprint || cursor.dataAsOf !== source.dataAsOf) {
        throw new InsightCursorError();
      }
      offset = cursor.offset;
    }
    const items = filtered.slice(offset, offset + query.pageSize);
    const nextOffset = offset + items.length;
    return {
      contractVersion: INSIGHT_CONTRACT_VERSION,
      dataAsOf: source.dataAsOf,
      sourceBatchId: source.sourceBatchId,
      items,
      nextCursor: nextOffset < filtered.length
        ? encodeCursor({ offset: nextOffset, queryHash: fingerprint, dataAsOf: source.dataAsOf })
        : null,
    };
  }

  async getFailureTask(ownerUserId: string, sessionId: string, taskIndex: number): Promise<FailureTaskIndex | null> {
    const source = await this.failurePage();
    return source.items.find((item) =>
      item.ownerUserId === ownerUserId && item.sessionId === sessionId && item.taskIndex === taskIndex,
    ) ?? null;
  }
}
