import { createHash } from "node:crypto";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type {
  CompletionState,
  InsightQueryScope,
  FailureTaskIndex,
  FailureTaskPage,
  FailureTaskQuery,
} from "../services/insight/contracts.js";
import { INSIGHT_CONTRACT_VERSION } from "../services/insight/contracts.js";
import { InsightCursorError } from "../services/insight/providers/insight-read-provider.js";

export type InsightFailureTaskRow = {
  id: number;
  source_dt: string;
  owner_user_id: string;
  bot_id: string;
  bot_name: string;
  session_id: string;
  task_index: number;
  task_description: string;
  is_complete: number;
  failure_class: string;
  judge_reason_summary: string | null;
  session_start_time: string | null;
  session_end_time: string | null;
  session_duration_seconds: number | null;
  is_cron: number;
  payload_ref: string;
  payload_etag: string;
  payload_version_id: string | null;
  batch_id: string;
  data_as_of: string;
  judged_at: string | null;
  gmt_create: number | string | Date;
  gmt_modified: number | string | Date;
};

export type InsightFailureTaskCleanupScope = {
  ownerUserIds: string[];
  botIds?: string[];
  sourceDt?: string;
};

export type InsightFailureTaskCleanupResult = {
  matched: number;
  byOwner: Array<{ ownerUserId: string; count: number }>;
};

export type UpsertInsightFailureTaskInput = {
  sourceDt: string;
  ownerUserId: string;
  botId: string;
  botName: string;
  sessionId: string;
  taskIndex: number;
  taskDescription: string;
  isComplete: CompletionState;
  failureClass: string;
  judgeReasonSummary: string | null;
  sessionStartTime: string | null;
  sessionEndTime: string | null;
  sessionDurationSeconds: number | null;
  isCron: boolean;
  payloadRef: string;
  payloadEtag: string;
  payloadVersionId: string | null;
  batchId: string;
  dataAsOf: string;
  judgedAt: string | null;
};

type CursorPayload = {
  beforeSessionEnd: string | null;
  beforeId: number;
  queryHash: string;
  dataAsOf: string;
};

type LatestRow = { data_as_of: string; batch_id: string };

function compactDate(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const compact = value.replaceAll("-", "").slice(0, 8);
  return /^\d{8}$/.test(compact) ? compact : undefined;
}

function rowToFailureTask(row: InsightFailureTaskRow): FailureTaskIndex {
  return {
    sourceDt: row.source_dt,
    ownerUserId: row.owner_user_id,
    botId: row.bot_id,
    botName: row.bot_name,
    sessionId: row.session_id,
    taskIndex: Number(row.task_index),
    taskDescription: row.task_description,
    isComplete: Number(row.is_complete) as CompletionState,
    failureClass: row.failure_class,
    judgeReasonSummary: row.judge_reason_summary,
    sessionStartTime: row.session_start_time,
    sessionEndTime: row.session_end_time,
    sessionDurationSeconds: row.session_duration_seconds == null ? null : Number(row.session_duration_seconds),
    isCron: Number(row.is_cron) === 1,
    payloadRef: row.payload_ref,
    payloadEtag: row.payload_etag,
    payloadVersionId: row.payload_version_id,
    judgedAt: row.judged_at,
    batchId: row.batch_id,
    dataAsOf: row.data_as_of,
  };
}

function queryFingerprint(query: FailureTaskQuery): string {
  return createHash("sha256").update(JSON.stringify({
    userId: query.userId,
    botId: query.botId ?? null,
    from: compactDate(query.from) ?? null,
    to: compactDate(query.to) ?? null,
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
    if (
      (value.beforeSessionEnd !== null && typeof value.beforeSessionEnd !== "string")
      || !Number.isInteger(value.beforeId)
      || Number(value.beforeId) <= 0
      || !value.queryHash
      || !value.dataAsOf
    ) {
      throw new Error("invalid cursor payload");
    }
    return value as CursorPayload;
  } catch {
    throw new InsightCursorError("失败任务游标无效，请刷新后重试");
  }
}

function addScopeConditions(scope: InsightQueryScope, conditions: string[], params: unknown[]): void {
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
}

export class InsightTaskIndexRepository {
  constructor(private readonly db: IDatabase) {}

  async upsertMany(items: UpsertInsightFailureTaskInput[]): Promise<{ accepted: number }> {
    if (items.length === 0) return { accepted: 0 };
    await this.db.transaction(async (tx) => {
      const now = tx.dialect.now();
      for (const item of items) {
        const params = [
          item.sourceDt,
          item.ownerUserId,
          item.botId,
          item.botName,
          item.sessionId,
          item.taskIndex,
          item.taskDescription,
          item.isComplete,
          item.failureClass,
          item.judgeReasonSummary,
          item.sessionStartTime,
          item.sessionEndTime,
          item.sessionDurationSeconds,
          item.isCron ? 1 : 0,
          item.payloadRef,
          item.payloadEtag,
          item.payloadVersionId,
          item.batchId,
          item.dataAsOf,
          item.judgedAt,
          now,
          now,
        ];
        if (tx.dbType === "mysql" || tx.dbType === "zdas") {
          await tx.exec(
            `INSERT INTO insight_failure_task
             (source_dt, owner_user_id, bot_id, bot_name, session_id, task_index, task_description,
              is_complete, failure_class, judge_reason_summary, session_start_time, session_end_time,
              session_duration_seconds, is_cron, payload_ref, payload_etag, payload_version_id,
              batch_id, data_as_of, judged_at, gmt_create, gmt_modified)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON DUPLICATE KEY UPDATE
              source_dt = VALUES(source_dt), bot_id = VALUES(bot_id), bot_name = VALUES(bot_name), task_description = VALUES(task_description),
              is_complete = VALUES(is_complete), failure_class = VALUES(failure_class), judge_reason_summary = VALUES(judge_reason_summary),
              session_start_time = VALUES(session_start_time), session_end_time = VALUES(session_end_time),
              session_duration_seconds = VALUES(session_duration_seconds), is_cron = VALUES(is_cron),
              payload_ref = VALUES(payload_ref), payload_etag = VALUES(payload_etag), payload_version_id = VALUES(payload_version_id),
              batch_id = VALUES(batch_id), data_as_of = VALUES(data_as_of), judged_at = VALUES(judged_at),
              gmt_modified = VALUES(gmt_modified)`,
            params,
          );
        } else {
          await tx.exec(
            `INSERT INTO insight_failure_task
             (source_dt, owner_user_id, bot_id, bot_name, session_id, task_index, task_description,
              is_complete, failure_class, judge_reason_summary, session_start_time, session_end_time,
              session_duration_seconds, is_cron, payload_ref, payload_etag, payload_version_id,
              batch_id, data_as_of, judged_at, gmt_create, gmt_modified)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(owner_user_id, session_id, task_index) DO UPDATE SET
              source_dt = excluded.source_dt,
              bot_id = excluded.bot_id,
              bot_name = excluded.bot_name,
              task_description = excluded.task_description,
              is_complete = excluded.is_complete,
              failure_class = excluded.failure_class,
              judge_reason_summary = excluded.judge_reason_summary,
              session_start_time = excluded.session_start_time,
              session_end_time = excluded.session_end_time,
              session_duration_seconds = excluded.session_duration_seconds,
              is_cron = excluded.is_cron,
              payload_ref = excluded.payload_ref,
              payload_etag = excluded.payload_etag,
              payload_version_id = excluded.payload_version_id,
              batch_id = excluded.batch_id,
              data_as_of = excluded.data_as_of,
              judged_at = excluded.judged_at,
              gmt_modified = excluded.gmt_modified`,
            params,
          );
        }
      }
    });
    return { accepted: items.length };
  }

  async previewCleanup(scope: InsightFailureTaskCleanupScope): Promise<InsightFailureTaskCleanupResult> {
    const placeholders = scope.ownerUserIds.map(() => "?").join(", ");
    const conditions = [`owner_user_id IN (${placeholders})`];
    const params: unknown[] = [...scope.ownerUserIds];
    if (scope.botIds?.length) {
      conditions.push(`bot_id IN (${scope.botIds.map(() => "?").join(", ")})`);
      params.push(...scope.botIds);
    }
    if (scope.sourceDt) {
      conditions.push("source_dt = ?");
      params.push(scope.sourceDt);
    }
    const rows = await this.db.query<{ owner_user_id: string; count: number | string }>(
      `SELECT owner_user_id, COUNT(*) AS count FROM insight_failure_task
       WHERE ${conditions.join(" AND ")}
       GROUP BY owner_user_id`,
      params,
    );
    const countByOwner = new Map(rows.map((row) => [row.owner_user_id, Number(row.count)]));
    const byOwner = scope.ownerUserIds.map((ownerUserId) => ({
      ownerUserId,
      count: countByOwner.get(ownerUserId) ?? 0,
    }));
    return {
      matched: byOwner.reduce((sum, item) => sum + item.count, 0),
      byOwner,
    };
  }

  async cleanup(scope: InsightFailureTaskCleanupScope): Promise<InsightFailureTaskCleanupResult> {
    const preview = await this.previewCleanup(scope);
    if (preview.matched === 0) return preview;
    const placeholders = scope.ownerUserIds.map(() => "?").join(", ");
    const conditions = [`owner_user_id IN (${placeholders})`];
    const params: unknown[] = [...scope.ownerUserIds];
    if (scope.botIds?.length) {
      conditions.push(`bot_id IN (${scope.botIds.map(() => "?").join(", ")})`);
      params.push(...scope.botIds);
    }
    if (scope.sourceDt) {
      conditions.push("source_dt = ?");
      params.push(scope.sourceDt);
    }
    const result = await this.db.exec(
      `DELETE FROM insight_failure_task WHERE ${conditions.join(" AND ")}`,
      params,
    );
    return { ...preview, matched: result.affectedRows };
  }

  async getLatest(scope: InsightQueryScope): Promise<LatestRow | null> {
    const conditions: string[] = [];
    const params: unknown[] = [];
    addScopeConditions(scope, conditions, params);
    return (await this.db.query<LatestRow>(
      `SELECT data_as_of, batch_id FROM insight_failure_task
       ${conditions.length ? `WHERE ${conditions.join(" AND ")}` : ""}
       ORDER BY data_as_of DESC, id DESC LIMIT 1`,
      params,
    ))[0] ?? null;
  }

  async listFailureTasks(query: FailureTaskQuery): Promise<FailureTaskPage> {
    const latest = await this.getLatest(query);
    const conditions: string[] = [];
    const params: unknown[] = [];
    addScopeConditions(query, conditions, params);
    if (query.failureClass) {
      conditions.push("failure_class = ?");
      params.push(query.failureClass);
    }
    const completionStates = query.completionStates ?? [0, 2, 3];
    conditions.push(`is_complete IN (${completionStates.map(() => "?").join(", ")})`);
    params.push(...completionStates);

    const fingerprint = queryFingerprint(query);
    if (query.cursor) {
      const cursor = decodeCursor(query.cursor);
      if (cursor.queryHash !== fingerprint || cursor.dataAsOf !== (latest?.data_as_of ?? "db-empty")) {
        throw new InsightCursorError();
      }
      if (cursor.beforeSessionEnd === null) {
        conditions.push("session_end_time IS NULL AND id < ?");
        params.push(cursor.beforeId);
      } else {
        conditions.push("(session_end_time < ? OR (session_end_time = ? AND id < ?) OR session_end_time IS NULL)");
        params.push(cursor.beforeSessionEnd, cursor.beforeSessionEnd, cursor.beforeId);
      }
    }
    params.push(query.pageSize + 1);
    const rows = await this.db.query<InsightFailureTaskRow>(
      `SELECT * FROM insight_failure_task
       WHERE ${conditions.join(" AND ")}
       ORDER BY session_end_time DESC, id DESC LIMIT ?`,
      params,
    );
    const hasMore = rows.length > query.pageSize;
    const visible = rows.slice(0, query.pageSize);
    return {
      contractVersion: INSIGHT_CONTRACT_VERSION,
      dataAsOf: latest?.data_as_of ?? new Date().toISOString(),
      sourceBatchId: latest?.batch_id ?? "db-empty",
      items: visible.map(rowToFailureTask),
      nextCursor: hasMore && visible.length
        ? encodeCursor({
            beforeSessionEnd: visible[visible.length - 1].session_end_time,
            beforeId: visible[visible.length - 1].id,
            queryHash: fingerprint,
            dataAsOf: latest?.data_as_of ?? "db-empty",
          })
        : null,
    };
  }

  async getFailureTask(ownerUserId: string, sessionId: string, taskIndex: number): Promise<FailureTaskIndex | null> {
    const row = (await this.db.query<InsightFailureTaskRow>(
      `SELECT * FROM insight_failure_task
       WHERE owner_user_id = ? AND session_id = ? AND task_index = ?
       ORDER BY id DESC LIMIT 1`,
      [ownerUserId, sessionId, taskIndex],
    ))[0];
    return row ? rowToFailureTask(row) : null;
  }
}
