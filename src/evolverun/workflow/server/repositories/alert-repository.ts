/**
 * AlertRepository — reads and writes triggered_alerts table via raw SQL.
 * No dependency on ClawFlow; shares the same database schema.
 */
import type { IDatabase } from "../db.js";

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
  gmt_modified: number;
};

export type FindUnacknowledgedOptions = {
  severity?: string;
  limit?: number;
  offset?: number;
};

export class AlertRepository {
  constructor(protected db: IDatabase) {}

  // ── Write methods (best-effort: catch errors, log, return false) ──

  async record(
    flowId: string,
    workflowId: string,
    nodeId: string | null,
    alertRule: string,
    severity: string,
    message: string,
  ): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const time = typeof now === "number" ? now : Math.floor(Date.now() / 1000);
      await this.db.exec(
        `INSERT INTO triggered_alerts (flow_id, workflow_id, node_id, alert_rule, severity, message, time, acknowledged, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`,
        [
          flowId,
          workflowId,
          nodeId,
          alertRule,
          severity,
          message,
          time,
          now,
          now,
        ],
      );
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] AlertRepository.record failed: ${msg}`);
      return false;
    }
  }

  async acknowledge(alertId: number): Promise<boolean> {
    try {
      const now = this.db.dialect.now();
      const result = await this.db.exec(
        "UPDATE triggered_alerts SET acknowledged = 1, gmt_modified = ? WHERE id = ?",
        [now, alertId],
      );
      return result.affectedRows > 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] AlertRepository.acknowledge failed: ${msg}`);
      return false;
    }
  }

  // ── Read methods ──

  async findUnacknowledged(workflowId: string, options: FindUnacknowledgedOptions = {}): Promise<TriggeredAlertRow[]> {
    const limit = options.limit ?? 50;
    const offset = options.offset ?? 0;

    try {
      if (options.severity) {
        return await this.db.query<TriggeredAlertRow>(
          "SELECT * FROM triggered_alerts WHERE workflow_id = ? AND acknowledged = 0 AND severity = ? ORDER BY time DESC LIMIT ? OFFSET ?",
          [workflowId, options.severity, limit, offset],
        );
      }
      return await this.db.query<TriggeredAlertRow>(
        "SELECT * FROM triggered_alerts WHERE workflow_id = ? AND acknowledged = 0 ORDER BY time DESC LIMIT ? OFFSET ?",
        [workflowId, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] AlertRepository.findUnacknowledged failed: ${msg}`);
      return [];
    }
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        "DELETE FROM triggered_alerts WHERE flow_id = ?",
        [flowId],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] AlertRepository.deleteByFlowId failed: ${msg}`);
      return 0;
    }
  }

  async findById(alertId: number): Promise<TriggeredAlertRow | null> {
    try {
      const rows = await this.db.query<TriggeredAlertRow>(
        "SELECT * FROM triggered_alerts WHERE id = ?",
        [alertId],
      );
      return rows[0] ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] AlertRepository.findById failed: ${msg}`);
      return null;
    }
  }
}

// ── Backward-compatible aliases ──

/** @deprecated Use InsertAlertInput with positional args via record() */
export type InsertAlertInput = {
  flow_id: string;
  workflow_id: string;
  node_id?: string | null;
  alert_rule: string;
  severity?: string;
  message: string;
  time?: number;
};

/** @deprecated Use AlertRepository */
export class TriggeredAlertRepository extends AlertRepository {
  /** @deprecated Use record(flowId, workflowId, nodeId, alertRule, severity, message) */
  async insert(input: InsertAlertInput): Promise<TriggeredAlertRow> {
    const severity = input.severity ?? "warning";
    const ok = await this.record(
      input.flow_id,
      input.workflow_id,
      input.node_id ?? null,
      input.alert_rule,
      severity,
      input.message,
    );
    if (!ok) {
      throw new Error("Failed to insert alert");
    }
    const rows = await this.db.query<TriggeredAlertRow>(
      "SELECT * FROM triggered_alerts WHERE flow_id = ? AND alert_rule = ? ORDER BY id DESC LIMIT 1",
      [input.flow_id, input.alert_rule],
    );
    return rows[0]!;
  }

  /** @deprecated Use acknowledge(alertId) which returns boolean */
  async acknowledgeAndReturn(alertId: number): Promise<TriggeredAlertRow | null> {
    const ok = await this.acknowledge(alertId);
    if (!ok) return null;
    return this.findById(alertId);
  }
}