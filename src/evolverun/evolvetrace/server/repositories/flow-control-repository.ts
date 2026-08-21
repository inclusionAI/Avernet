/**
 * Stub FlowControlRepository for Evolvetrace.
 */
import type { IDatabase } from "../db.js";

export class FlowControlRepository {
  constructor(private db: IDatabase) {}

  async findByFlowId(_flowId: string): Promise<Record<string, unknown> | null> {
    return null;
  }

  async releaseAllSlotsForFlowByFlowId(_flowId: string): Promise<number> {
    return 0;
  }

  async deleteQueueEntriesForFlowByFlowId(_flowId: string): Promise<number> {
    return 0;
  }
}
