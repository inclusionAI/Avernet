/**
 * FlowControlApiRepository — HTTP client implementation of IFlowControlRepository.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for flow control.
 * All methods log a warning and return safe defaults.
 *
 * In no-op mode:
 * - acquireSlot always returns true (no concurrency control)
 * - enqueue returns 0 (not queued)
 * - All read methods return empty results
 */

import type { ApiClient } from "../api-client.js";
import type {
  IFlowControlRepository,
  FlowControlSlotInsert,
  FlowControlQueueInsert,
  FlowControlQueueRow,
  FlowControlSlotRow,
} from "../repositories/types.js";

export function isFlowDenied(..._args: any[]): boolean { return false; }
export function purgeDenyList(..._args: any[]): void {}

export class FlowControlApiRepository implements IFlowControlRepository {
  constructor(private api: ApiClient) {}

  async acquireSlot(insert: FlowControlSlotInsert, maxConcurrent: number): Promise<boolean> {
    void insert;
    // maxConcurrent = 0 means unlimited — always return true
    if (maxConcurrent === 0) return true;
    console.warn(
      "[FlowControlApi] acquireSlot is not supported over HTTP API mode " +
        "(no server endpoint). Allowing by default (no concurrency control).",
    );
    return true;
  }

  async releaseSlot(
    instanceId: string, scopeKey: string, flowId: string, nodeId: string | null,
  ): Promise<boolean> {
    void instanceId; void scopeKey; void flowId; void nodeId;
    return true;
  }

  async releaseAllSlotsForFlow(instanceId: string, flowId: string): Promise<number> {
    void instanceId; void flowId;
    return 0;
  }

  async countSlots(instanceId: string, scopeKey: string): Promise<number> {
    void instanceId; void scopeKey;
    return 0;
  }

  async enqueue(insert: FlowControlQueueInsert): Promise<number> {
    void insert;
    console.warn(
      "[FlowControlApi] enqueue is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }

  async markDispatched(id: number): Promise<boolean> {
    void id;
    return false;
  }

  async deleteQueueEntryById(id: number): Promise<boolean> {
    void id;
    return false;
  }

  async markExpired(id: number): Promise<boolean> {
    void id;
    return false;
  }

  async expireStaleEntries(instanceId: string): Promise<number> {
    void instanceId;
    return 0;
  }

  async fetchExpiringItems(instanceId: string): Promise<FlowControlQueueRow[]> {
    void instanceId;
    return [];
  }

  async fetchQueuedItems(
    instanceId: string, scopeKey: string, limit: number,
  ): Promise<FlowControlQueueRow[]> {
    void instanceId; void scopeKey; void limit;
    return [];
  }

  async getScopesWithQueuedItems(instanceId: string): Promise<string[]> {
    void instanceId;
    return [];
  }

  async deleteProcessedQueueEntries(
    instanceId: string, olderThan: number,
  ): Promise<number> {
    void instanceId; void olderThan;
    return 0;
  }

  async releaseOrphanedSlots(instanceId: string): Promise<number> {
    void instanceId;
    return 0;
  }

  async releaseStaleOrphanedSlots(
    instanceId: string, staleSeconds: number,
  ): Promise<{ releasedSlots: number; failedFlows: number }> {
    void instanceId; void staleSeconds;
    return { releasedSlots: 0, failedFlows: 0 };
  }

  async deleteQueueEntriesForFlow(
    instanceId: string, flowId: string,
  ): Promise<number> {
    void instanceId; void flowId;
    return 0;
  }

  async getScopeStatus(
    instanceId: string, scopeKey: string,
  ): Promise<{ running: number; queued: number }> {
    void instanceId; void scopeKey;
    return { running: 0, queued: 0 };
  }

  async getActiveScopeKeys(instanceId: string): Promise<string[]> {
    void instanceId;
    return [];
  }

  async getQueueItems(
    instanceId: string, scopeKey?: string, limit?: number,
  ): Promise<FlowControlQueueRow[]> {
    void instanceId; void scopeKey; void limit;
    return [];
  }

  async getSlots(
    instanceId: string, scopeKey?: string,
  ): Promise<FlowControlSlotRow[]> {
    void instanceId; void scopeKey;
    return [];
  }

  async forceReleaseSlotsForFlows(
    instanceId: string, flowIds: string[],
  ): Promise<{ releasedSlots: number; deletedQueue: number }> {
    void instanceId; void flowIds;
    return { releasedSlots: 0, deletedQueue: 0 };
  }

  async findSlotsGroupedBySession(
    instanceId: string,
  ): Promise<Array<{ session_id: string; flow_ids: string[] }>> {
    void instanceId;
    return [];
  }

  async renewLeases(instanceId: string, newExpiryAt: number): Promise<number> {
    void instanceId; void newExpiryAt;
    return 0;
  }

  async releaseExpiredLeases(
    instanceId: string,
  ): Promise<{ releasedSlots: number; deletedQueue: number }> {
    void instanceId;
    return { releasedSlots: 0, deletedQueue: 0 };
  }

  async countActiveSlots(instanceId: string, scopeKey: string): Promise<number> {
    void instanceId; void scopeKey;
    return 0;
  }

  async deleteLegacyScopeEntries(
    instanceId: string,
  ): Promise<{ deletedSlots: number; deletedQueue: number }> {
    void instanceId;
    return { deletedSlots: 0, deletedQueue: 0 };
  }
}