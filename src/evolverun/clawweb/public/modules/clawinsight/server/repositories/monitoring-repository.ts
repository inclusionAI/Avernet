import type { IDatabase, Row } from "@avernet/clawweb-shared/server/db";
import { CHECK_VERSION, MonitoringError, type BotCheck, type DiagnosisEvent, type DiagnosisItem,
  type DiagnosisPage, type DiagnosisQuery, type MonitoringStore } from "../services/monitoring/contracts.js";
import { parseCheck, parseDiagnosis } from "../services/monitoring/validation.js";
import { CHECKS_TABLE, DIAGNOSES_TABLE, verifyMonitoringSchema } from "./monitoring-schema-check.js";

const fields = {
  schemaVersion: "schema_version", eventId: "event_id", botId: "bot_id", engine: "engine", sessionKey: "session_key",
  sessionId: "session_id", traceId: "trace_id", occurredAt: "occurred_at_ms", diagnosedAt: "diagnosed_at_ms",
  decision: "decision", tcFaultLabel: "tc_fault_label", confidence: "confidence_json",
  businessProblemCategory: "business_problem_category", businessProblemSubtype: "business_problem_subtype",
  systemDiagnosis: "system_diagnosis", businessDiagnosis: "business_diagnosis", handlerName: "handler_name",
  humanIntervention: "human_intervention",
} satisfies Partial<Record<keyof DiagnosisEvent, string>>;
const columns = Object.values(fields);
function integer(value: unknown): number {
  if ((typeof value !== "number" && typeof value !== "string") || value === "") throw new Error("Invalid stored integer");
  const n = Number(value);
  if (!Number.isSafeInteger(n)) throw new Error("Invalid stored integer");
  return n;
}
function iso(value: unknown): string | null {
  return value == null ? null : new Date(integer(value)).toISOString();
}
function eventFromRow(row: Row): DiagnosisEvent {
  const event: Record<string, unknown> = {};
  for (const [key, col] of Object.entries(fields)) event[key] = row[col];
  event.diagnosisId = row.event_id;
  event.occurredAt = iso(row.occurred_at_ms);
  event.diagnosedAt = iso(row.diagnosed_at_ms);
  event.confidence = row.confidence_json == null ? null : JSON.parse(String(row.confidence_json));
  const intervention = integer(row.human_intervention);
  if (intervention !== 0 && intervention !== 1) throw new Error("Invalid stored intervention");
  event.humanIntervention = intervention === 1;
  return parseDiagnosis(event, String(row.event_id));
}
function checkFromRow(row: Row): BotCheck {
  return parseCheck({ schemaVersion: CHECK_VERSION, botId: row.bot_id, engine: row.engine,
    checkedAt: iso(row.checked_at_ms), lastSuccessfulCheckAt: iso(row.last_successful_check_at_ms), status: row.status }, integer(row.checked_at_ms));
}
function item(event: DiagnosisEvent): DiagnosisItem {
  const { schemaVersion: _version, eventId: _event, engine: _engine, ...result } = event;
  return result;
}
function isDuplicate(error: unknown): boolean {
  const e = error as { code?: string; errno?: number };
  return e?.code === "SQLITE_CONSTRAINT_UNIQUE" || e?.code === "ER_DUP_ENTRY" || e?.errno === 1062;
}
function conflict(): never { throw new MonitoringError("EVENT_CONFLICT", "同一编号或检查时间已有不同内容，未覆盖原记录。"); }

/** Single-statement reads provide a consistent snapshot. Writes are autocommitted atomic statements.
 * No async BEGIN on the shared SQLite connection, and no process-local lock masquerading as deduplication.
 */
export class MonitoringRepository implements MonitoringStore {
  private ready: Promise<void> | null = null;
  constructor(private readonly db: IDatabase) {}
  private async run<T>(fn: () => Promise<T>): Promise<T> {
    try {
      this.ready ??= verifyMonitoringSchema(this.db).catch((error) => { this.ready = null; throw error; });
      await this.ready;
      return await fn();
    } catch (error) {
      if (error instanceof MonitoringError && error.code === "EVENT_CONFLICT") throw error;
      this.ready = null;
      throw new MonitoringError("NOT_READY", "监控存储暂不可用或表结构不兼容。");
    }
  }
  async insertDiagnosis(input: DiagnosisEvent, receivedAt: number): Promise<boolean> {
    const event = parseDiagnosis(input, input.eventId);
    return this.run(async () => {
      const values = Object.keys(fields).map((name) => {
        const value = event[name as keyof typeof fields];
        if (name === "occurredAt" || name === "diagnosedAt") return value === null ? null : Date.parse(String(value));
        if (name === "humanIntervention") return value ? 1 : 0;
        if (name === "confidence") return value === null ? null : JSON.stringify(value);
        return value;
      });
      try {
        const result = await this.db.exec(`INSERT INTO ${DIAGNOSES_TABLE} (${columns.join(", ")}, received_at_ms)
          VALUES (${[...values, receivedAt].map(() => "?").join(", ")})`, [...values, receivedAt]);
        if (result.affectedRows !== 1) throw new Error("Insert did not persist");
        return true;
      } catch (error) {
        if (!isDuplicate(error)) throw error;
        const [row] = await this.db.query(`SELECT ${columns.join(", ")} FROM ${DIAGNOSES_TABLE} WHERE event_id = ?`, [event.eventId]);
        if (!row) throw new Error("Duplicate not yet visible");
        if (JSON.stringify(eventFromRow(row)) !== JSON.stringify(event)) conflict();
        return false;
      }
    });
  }
  async applyCheck(input: BotCheck, receivedAt: number): Promise<boolean> {
    const check = parseCheck(input, receivedAt);
    return this.run(async () => {
      const checkedMs = Date.parse(check.checkedAt), successMs = check.lastSuccessfulCheckAt === null ? null : Date.parse(check.lastSuccessfulCheckAt);
      // CAS retries resolve concurrent inserts/updates across processes using the unique key and old timestamp.
      for (let attempt = 0; attempt < 8; attempt++) {
        const [row] = await this.db.query(`SELECT * FROM ${CHECKS_TABLE} WHERE bot_id = ?`, [check.botId]);
        if (!row) {
          try {
            const result = await this.db.exec(`INSERT INTO ${CHECKS_TABLE}
              (bot_id, engine, checked_at_ms, last_successful_check_at_ms, status, received_at_ms) VALUES (?, ?, ?, ?, ?, ?)`,
            [check.botId, check.engine, checkedMs, successMs, check.status, receivedAt]);
            if (result.affectedRows !== 1) throw new Error("Insert did not persist");
            return true;
          } catch (error) { if (isDuplicate(error)) continue; throw error; }
        }
        const previous = integer(row.checked_at_ms);
        if (previous > checkedMs) return false;
        if (previous === checkedMs) {
          if (JSON.stringify(checkFromRow(row)) !== JSON.stringify(check)) conflict();
          return false;
        }
        const result = await this.db.exec(`UPDATE ${CHECKS_TABLE} SET engine = ?, checked_at_ms = ?,
          last_successful_check_at_ms = ?, status = ?, received_at_ms = ? WHERE bot_id = ? AND checked_at_ms = ?`,
        [check.engine, checkedMs, successMs, check.status, receivedAt, check.botId, previous]);
        if (result.affectedRows === 1) return true;
      }
      throw new Error("Concurrent check updates; retry later");
    });
  }
  async readStatus(botId: string): Promise<{ check: BotCheck | null; count: number }> {
    return this.run(async () => {
      const [row] = await this.db.query(`SELECT counts.diagnosis_count, checks.* FROM
        (SELECT COUNT(*) AS diagnosis_count FROM ${DIAGNOSES_TABLE} WHERE bot_id = ?) counts
        LEFT JOIN ${CHECKS_TABLE} checks ON checks.bot_id = ?`, [botId, botId]);
      if (!row) throw new Error("Missing count result");
      return { check: row.bot_id == null ? null : checkFromRow(row), count: integer(row.diagnosis_count) };
    });
  }
  async listDiagnoses(botId: string, query: DiagnosisQuery): Promise<DiagnosisPage> {
    return this.run(async () => {
      const conditions = ["bot_id = ?"], params: unknown[] = [botId];
      if (query.startMs !== null) { conditions.push("occurred_at_ms >= ?"); params.push(query.startMs); }
      if (query.endMs !== null) { conditions.push("occurred_at_ms < ?"); params.push(query.endMs); }
      if (query.keyword) {
        // '!' avoids SQL-mode-dependent backslash escaping; a backslash in the bound pattern is literal.
        const pattern = `%${query.keyword.toLowerCase().replace(/[!%_]/g, "!$&")}%`;
        const searchable = ["event_id", "session_key", "session_id", "trace_id", "tc_fault_label", "business_problem_category", "business_problem_subtype"];
        conditions.push(`(${searchable.map((c) => `LOWER(${c}) LIKE ? ESCAPE '!'`).join(" OR ")})`);
        params.push(...searchable.map(() => pattern));
      }
      const where = conditions.join(" AND ");
      const filter = query.decision === "ALL" ? "" : " AND decision = ?";
      const selected = query.decision === "ALL" ? params : [...params, query.decision];
      const order = "CASE WHEN occurred_at_ms IS NULL THEN 1 ELSE 0 END, occurred_at_ms DESC, event_id DESC";
      // One statement rather than independent count/list queries: same snapshot even with concurrent ingestion.
      const rows = await this.db.query(`SELECT counts.*, page.* FROM
        (SELECT COUNT(*) AS all_count,
          COALESCE(SUM(CASE WHEN decision = 'ALERT' THEN 1 ELSE 0 END), 0) AS alert_count,
          COALESCE(SUM(CASE WHEN decision = 'PASS' THEN 1 ELSE 0 END), 0) AS pass_count,
          COALESCE(SUM(CASE WHEN decision = 'UNRESOLVED' THEN 1 ELSE 0 END), 0) AS unresolved_count
         FROM ${DIAGNOSES_TABLE} WHERE ${where}) counts
        LEFT JOIN (SELECT ${columns.join(", ")} FROM ${DIAGNOSES_TABLE} WHERE ${where}${filter}
          ORDER BY ${order} LIMIT ? OFFSET ?) page ON 1 = 1
        ORDER BY ${order}`, [...params, ...selected, query.pageSize, (query.page - 1) * query.pageSize]);
      if (!rows.length) throw new Error("Missing count result");
      const counts = { all: integer(rows[0].all_count), alert: integer(rows[0].alert_count), pass: integer(rows[0].pass_count), unresolved: integer(rows[0].unresolved_count) };
      const total = counts[query.decision.toLowerCase() as keyof typeof counts];
      return { botId, page: query.page, pageSize: query.pageSize, total, totalPages: Math.ceil(total / query.pageSize), counts,
        items: rows.filter((row) => row.event_id != null).map((row) => item(eventFromRow(row))) };
    });
  }
}
