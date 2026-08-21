/**
 * ExecutionStepLogRepository — persists and queries execution_step_log records.
 *
 * Records structured step events from dynamic workflow execution:
 * - start/complete/fail/retry/skip: node lifecycle transitions
 * - materialize: dynamic-template nodes created from templates
 * - inject: orchestrator-injected nodes
 * - llm_evaluate/goal_check: LLM-driven evaluation steps
 * - replan/budget_check/budget_warning/budget_exhausted: orchestration & budget events
 *
 * Best-effort writes: DB failure is logged but doesn't throw.
 */
import type { IDatabase, Row } from "../types.js";
import { nowForDb } from "../types.js";
import type {
  IExecutionStepLogRepository,
  ExecutionStepLogRow,
  ExecutionStepLogInsert,
  FindExecutionStepLogOptions,
} from "./types.js";

const INSERT_SQL = `INSERT INTO execution_step_log (
  flow_id, node_id, step_type, timestamp,
  input_summary, output_summary, llm_evaluation, decision_path,
  duration_ms, token_usage, metadata,
  gmt_create, gmt_modified
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`;

function stepToParams(step: ExecutionStepLogInsert, now: number | string): unknown[] {
  return [
    step.flowId,
    step.nodeId,
    step.stepType,
    step.timestamp,
    step.inputSummary ?? null,
    step.outputSummary ?? null,
    step.llmEvaluation ?? null,
    step.decisionPath ?? null,
    step.durationMs ?? null,
    step.tokenUsage ?? null,
    step.metadata ? JSON.stringify(step.metadata) : null,
    now,
    now,
  ];
}

function buildWhereClause(
  flowId: string,
  options?: FindExecutionStepLogOptions,
): { sql: string; params: unknown[] } {
  const conditions = ["flow_id = ?"];
  const params: unknown[] = [flowId];

  if (options?.nodeId) {
    conditions.push("node_id = ?");
    params.push(options.nodeId);
  }
  if (options?.stepType) {
    conditions.push("step_type = ?");
    params.push(options.stepType);
  }

  return { sql: conditions.join(" AND "), params };
}

export class ExecutionStepLogRepository implements IExecutionStepLogRepository {
  constructor(private db: IDatabase) {}

  /** Insert a single execution step log entry. Best-effort. */
  async insertStep(step: ExecutionStepLogInsert): Promise<boolean> {
    try {
      const now = nowForDb(this.db.dbType);
      await this.db.exec(INSERT_SQL, stepToParams(step, now));
      return true;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ExecutionStepLogRepository.insertStep failed: ${msg}`);
      return false;
    }
  }

  /** Query step logs for a flow, with optional filters. Sorted by timestamp ascending. */
  async getStepsByFlow(
    flowId: string,
    options?: FindExecutionStepLogOptions,
  ): Promise<ExecutionStepLogRow[]> {
    try {
      const { sql: where, params } = buildWhereClause(flowId, options);
      const limit = options?.limit ?? 100;
      const offset = options?.offset ?? 0;
      return await this.db.query<ExecutionStepLogRow>(
        `SELECT * FROM execution_step_log WHERE ${where} ORDER BY timestamp ASC LIMIT ? OFFSET ?`,
        [...params, limit, offset],
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ExecutionStepLogRepository.getStepsByFlow failed: ${msg}`);
      return [];
    }
  }

  /** Count step logs for a flow, with optional filters. */
  async getStepCountByFlow(
    flowId: string,
    options?: FindExecutionStepLogOptions,
  ): Promise<number> {
    try {
      const { sql: where, params } = buildWhereClause(flowId, options);
      const rows = await this.db.query<Row>(
        `SELECT COUNT(*) AS cnt FROM execution_step_log WHERE ${where}`,
        params,
      );
      return (rows[0]?.cnt as number) ?? 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ExecutionStepLogRepository.getStepCountByFlow failed: ${msg}`);
      return 0;
    }
  }

  /** Delete step logs older than the given Unix timestamp (cleanup). Returns deleted count. */
  async deleteOlderThan(olderThan: number): Promise<number> {
    try {
      const result = await this.db.exec(
        `DELETE FROM execution_step_log WHERE timestamp < ?`,
        [olderThan],
      );
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] ExecutionStepLogRepository.deleteOlderThan failed: ${msg}`);
      return 0;
    }
  }
}