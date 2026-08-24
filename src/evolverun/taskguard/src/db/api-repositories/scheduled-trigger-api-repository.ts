/**
 * ScheduledTriggerApiRepository — HTTP client implementation for scheduled trigger CRUD.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for scheduled triggers.
 * All methods log a warning and return safe defaults.
 */
import type { ApiClient } from "../api-client.js";
import type { ScheduledTrigger, CreateTriggerInput, UpdateTriggerInput } from "../../scheduler/types.js";
import type { IScheduledTriggerRepository } from "../repositories/types.js";

export class ScheduledTriggerApiRepository implements IScheduledTriggerRepository {
  constructor(private api: ApiClient) {}

  async create(input: CreateTriggerInput): Promise<ScheduledTrigger> {
    void input;
    console.warn(
      "[ScheduledTriggerApi] create is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    throw new Error("ScheduledTriggerApi: create is not supported over HTTP API mode");
  }

  async getById(triggerId: string): Promise<ScheduledTrigger | null> {
    void triggerId;
    console.warn(
      "[ScheduledTriggerApi] getById is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async listByWorkflow(workflowId: string): Promise<ScheduledTrigger[]> {
    void workflowId;
    console.warn(
      "[ScheduledTriggerApi] listByWorkflow is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async listEnabled(): Promise<ScheduledTrigger[]> {
    console.warn(
      "[ScheduledTriggerApi] listEnabled is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async findDueTriggers(now: number): Promise<ScheduledTrigger[]> {
    void now;
    console.warn(
      "[ScheduledTriggerApi] findDueTriggers is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async update(
    triggerId: string, input: UpdateTriggerInput,
  ): Promise<ScheduledTrigger | null> {
    void triggerId; void input;
    console.warn(
      "[ScheduledTriggerApi] update is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async updateFireTimes(
    triggerId: string, lastFireTime: number, nextFireTime: number,
  ): Promise<ScheduledTrigger | null> {
    void triggerId; void lastFireTime; void nextFireTime;
    console.warn(
      "[ScheduledTriggerApi] updateFireTimes is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async enable(triggerId: string): Promise<ScheduledTrigger | null> {
    void triggerId;
    console.warn(
      "[ScheduledTriggerApi] enable is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async disable(triggerId: string): Promise<ScheduledTrigger | null> {
    void triggerId;
    console.warn(
      "[ScheduledTriggerApi] disable is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async delete(triggerId: string): Promise<boolean> {
    void triggerId;
    console.warn(
      "[ScheduledTriggerApi] delete is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return false;
  }

  async countRunningFlows(workflowId: string): Promise<number> {
    void workflowId;
    console.warn(
      "[ScheduledTriggerApi] countRunningFlows is not supported over HTTP API mode " +
        "(no server endpoint). Returning 0.",
    );
    return 0;
  }
}