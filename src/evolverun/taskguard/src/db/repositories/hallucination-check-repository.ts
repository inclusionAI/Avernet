/**
 * HallucinationCheckRepository — persists and queries node_hallucination_checks.
 *
 * Follows the same best-effort pattern as other repositories:
 * DB errors are logged and never throw.
 */

import type { IDatabase } from "../types.js";
import { nowForDb } from "../types.js";
import type {
  HallucinationCheckInsert,
  HallucinationCheckRow,
  HallucinationCheckSummary,
} from "./types.js";

export class HallucinationCheckRepository {
  constructor(private db: IDatabase) {}

  async insertChecks(checks: HallucinationCheckInsert[]): Promise<number> {
    if (checks.length === 0) return 0;

    try {
      // Single multi-row INSERT to avoid "maximum open cursors exceeded" on OceanBase
      // when using prepared statements in a loop. N rows = 1 cursor instead of N.
      // Use nowForDb() for cross-DB compatibility: SQLite expects unix seconds,
      // MySQL expects 'YYYY-MM-DD HH:MM:SS' (unixepoch() is SQLite-only).
      const now = nowForDb(this.db.dbType);
      const COLUMNS = 10;
      const valuePlaceholder = `(${Array.from({ length: COLUMNS }, () => "?").join(", ")}, ?, ?)`;
      const placeholders = checks.map(() => valuePlaceholder).join(", ");

      const params: unknown[] = [];
      for (const check of checks) {
        params.push(
          check.flowId,
          check.nodeId,
          check.attempt,
          check.checkType,
          check.severity ?? "low",
          check.passed ?? 1,
          check.description ?? null,
          check.evidence ?? null,
          check.riskScore ?? 0,
          check.riskLevel ?? "none",
          now,
          now,
        );
      }

      const result = await this.db.exec(
        `INSERT INTO node_hallucination_checks (
          flow_id, node_id, attempt, check_type, severity,
          passed, description, evidence, risk_score, risk_level,
          gmt_create, gmt_modified
        ) VALUES ${placeholders}`,
        params,
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] HallucinationCheckRepository.insertChecks failed: ${msg}`);
      return 0;
    }
  }

  async findByFlowNode(
    flowId: string,
    nodeId: string,
    attempt = 1,
  ): Promise<HallucinationCheckRow[]> {
    try {
      return await this.db.query<HallucinationCheckRow>(
        `SELECT * FROM node_hallucination_checks
         WHERE flow_id = ? AND node_id = ? AND attempt = ?
         ORDER BY check_type ASC`,
        [flowId, nodeId, attempt],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] HallucinationCheckRepository.findByFlowNode failed: ${msg}`);
      return [];
    }
  }

  async findSummaryByFlowId(flowId: string): Promise<HallucinationCheckSummary[]> {
    try {
      return await this.db.query<HallucinationCheckSummary>(
        `SELECT
           node_id,
           attempt,
           COUNT(*) AS totalChecks,
           SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) AS failedChecks,
           MAX(risk_score) AS riskScore,
           MAX(risk_level) AS riskLevel
         FROM node_hallucination_checks
         WHERE flow_id = ?
         GROUP BY node_id, attempt`,
        [flowId],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] HallucinationCheckRepository.findSummaryByFlowId failed: ${msg}`);
      return [];
    }
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        `DELETE FROM node_hallucination_checks WHERE flow_id = ?`,
        [flowId],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] HallucinationCheckRepository.deleteByFlowId failed: ${msg}`);
      return 0;
    }
  }
}