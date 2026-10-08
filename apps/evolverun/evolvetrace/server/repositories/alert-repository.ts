/**
 * Stub AlertRepository for Evolvetrace.
 */
import type { IDatabase } from "../db.js";

export class AlertRepository {
  constructor(private db: IDatabase) {}

  async findByFlowId(_flowId: string): Promise<Record<string, unknown>[]> {
    return [];
  }

  async deleteByFlowId(_flowId: string): Promise<boolean> {
    return true;
  }
}
