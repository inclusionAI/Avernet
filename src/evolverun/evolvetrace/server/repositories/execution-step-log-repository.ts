/**
 * Stub ExecutionStepLogRepository for Evolvetrace.
 */
import type { IDatabase } from "../db.js";

export type ExecutionStepLogRow = {
  id: number;
  flow_id: string;
  node_id: string;
  step_type: string;
  timestamp: number;
  decision_path: string | null;
  input_summary: string | null;
  output_summary: string | null;
  metadata: string | null;
  llm_evaluation: string | null;
  token_usage: string | null;
};

export type GetStepsOptions = {
  stepType?: string;
  nodeId?: string;
  limit?: number;
  offset?: number;
};

export type GetStepCountOptions = {
  stepType?: string;
  nodeId?: string;
};

export class ExecutionStepLogRepository {
  constructor(private db: IDatabase) {}

  async getStepsByFlow(_flowId: string, _opts: GetStepsOptions = {}): Promise<ExecutionStepLogRow[]> {
    return [];
  }

  async getStepCountByFlow(_flowId: string, _opts?: GetStepCountOptions): Promise<number> {
    return 0;
  }
}
