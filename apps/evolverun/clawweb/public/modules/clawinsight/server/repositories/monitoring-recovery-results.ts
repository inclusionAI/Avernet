import { isDeepStrictEqual } from "node:util";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { MonitoringError, type MonitoringRecoveryResult } from "../services/monitoring/contracts.js";
import { parseRecoveryResult } from "../services/monitoring/recovery-result-validation.js";
import { DIAGNOSES_TABLE } from "./monitoring-schema-check.js";

/** Cross-process compare-and-swap, never a read followed by an unconditional overwrite. */
export async function appendRecoveryResult(db: IDatabase, input: MonitoringRecoveryResult): Promise<boolean> {
  const result = parseRecoveryResult(input);
  for (let attempt = 0; attempt < 8; attempt++) {
    const [row] = await db.query(`SELECT self_healing_results FROM ${DIAGNOSES_TABLE} WHERE event_id = ?`, [result.event_id]);
    if (!row) throw new MonitoringError("RESULT_TARGET_NOT_FOUND", "原诊断记录不存在。");
    const previous = row.self_healing_results;
    if (previous !== null && typeof previous !== "string") throw new Error("Invalid stored recovery history");
    const history: unknown = previous === null ? [] : JSON.parse(previous);
    if (!Array.isArray(history)) throw new Error("Recovery history must be an array");
    // Corrupt history is not disposable. Fail without resetting it or appending over it.
    const entries = history.map(parseRecoveryResult);
    if (entries.some(entry => entry.event_id !== result.event_id)) throw new Error("Recovery history target mismatch");
    const existing = entries.find(entry => entry.task_id === result.task_id && entry.result_version === result.result_version);
    if (existing) {
      if (isDeepStrictEqual(existing, result)) return false;
      throw new MonitoringError("RESULT_CONFLICT", "同一任务和版本已有不同结果，未覆盖原记录。");
    }
    const serialized = JSON.stringify([...entries, result]);
    // MEDIUMTEXT's byte capacity, not a count limit or a truncation policy.
    if (Buffer.byteLength(serialized, "utf8") > 16_777_215) throw new Error("Recovery history capacity exceeded");
    const comparison = previous === null ? "self_healing_results IS NULL"
      : db.dbType === "sqlite" ? "self_healing_results IS ?"
        : "CAST(self_healing_results AS BINARY) = CAST(? AS BINARY)";
    const params = [serialized, result.event_id, ...(previous === null ? [] : [previous])];
    const updated = await db.exec(`UPDATE ${DIAGNOSES_TABLE} SET self_healing_results = ? WHERE event_id = ? AND ${comparison}`, params);
    if (updated.affectedRows === 1) return true;
    if (updated.affectedRows !== 0) throw new Error("Unexpected recovery update count");
  }
  throw new Error("Concurrent recovery updates; retry later");
}
