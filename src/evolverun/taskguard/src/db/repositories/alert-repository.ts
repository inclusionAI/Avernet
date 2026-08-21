/**
 * TriggeredAlertRepository — persists and queries triggered alerts.
 *
 * Best-effort writes: DB failure is logged but doesn't throw.
 */
import type { IDatabase, Row } from "../types.js";
import { nowForDb } from "../types.js";
import type { ITriggeredAlertRepository } from "./types.js";

export type TriggeredAlertRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string | null;
  alert_rule: string;
  severity: string;
  message: string;
  time: number;
  acknowledged: number;
  gmt_create: number;
};

export type FindUnacknowledgedOptions = {
  severity?: string;
  limit?: number;
};

export class TriggeredAlertRepository implements ITriggeredAlertRepository {
  constructor(private db: IDatabase) {}

  /** Record a triggered alert. Returns true on success, false on failure. */
  async record(
    flowId: string,
    workflowId: string,
    nodeId: string | null,
    alertRule: string,
    severity: string,
    message: string,
  ): Promise<boolean> {
    try {
      await this.db.exec(
        `INSERT INTO triggered_alerts (flow_id, workflow_id, node_id, alert_rule, severity, message, time, acknowledged, gmt_create)
         VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)`,
        [flowId, workflowId, nodeId, alertRule, severity, message, Math.floor(Date.now() / 1000), nowForDb(this.db.dbType)],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] TriggeredAlertRepository.record failed: ${msg}`);
      return false;
    }
  }

  /** Find unacknowledged alerts (acknowledged = 0), optionally filtered by severity. */
  async findUnacknowledged(
    workflowId: string,
    options: FindUnacknowledgedOptions = {},
  ): Promise<TriggeredAlertRow[]> {
    const limit = options.limit ?? 100;
    try {
      if (options.severity) {
        return await this.db.query<TriggeredAlertRow>(
          `SELECT * FROM triggered_alerts
           WHERE workflow_id = ? AND acknowledged = 0 AND severity = ?
           ORDER BY time DESC LIMIT ?`,
          [workflowId, options.severity, limit],
        );
      }
      return await this.db.query<TriggeredAlertRow>(
        `SELECT * FROM triggered_alerts
         WHERE workflow_id = ? AND acknowledged = 0
         ORDER BY time DESC LIMIT ?`,
        [workflowId, limit],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] TriggeredAlertRepository.findUnacknowledged failed: ${msg}`);
      return [];
    }
  }

  /** Mark an alert as acknowledged. Returns true on success. */
  async acknowledge(alertId: number): Promise<boolean> {
    try {
      const result = await this.db.exec(
        "UPDATE triggered_alerts SET acknowledged = 1 WHERE id = ?",
        [alertId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] TriggeredAlertRepository.acknowledge failed: ${msg}`);
      return false;
    }
  }
}