/**
 * WebhookTriggerApiRepository — HTTP client implementation for webhook trigger CRUD.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for webhook triggers.
 * All methods log a warning and return safe defaults.
 */
import type { ApiClient } from "../api-client.js";
import type { WebhookTrigger } from "../../webhook/types.js";
import type { IWebhookTriggerRepository } from "../repositories/types.js";

type CreateWebhookTriggerInput = {
  triggerId?: string;
  workflowId: string;
  packId?: string;
  secret?: string;
  payloadMapping?: Record<string, string> | null;
  allowedIps?: string[] | null;
  description?: string;
  enabled?: boolean;
};

export class WebhookTriggerApiRepository implements IWebhookTriggerRepository {
  constructor(private api: ApiClient) {}

  async create(input: CreateWebhookTriggerInput): Promise<WebhookTrigger> {
    void input;
    console.warn(
      "[WebhookTriggerApi] create is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    throw new Error("WebhookTriggerApi: create is not supported over HTTP API mode");
  }

  async getByTriggerId(triggerId: string): Promise<WebhookTrigger | null> {
    void triggerId;
    console.warn(
      "[WebhookTriggerApi] getByTriggerId is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async findByWorkflowId(workflowId: string): Promise<WebhookTrigger[]> {
    void workflowId;
    console.warn(
      "[WebhookTriggerApi] findByWorkflowId is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async findAll(): Promise<WebhookTrigger[]> {
    console.warn(
      "[WebhookTriggerApi] findAll is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async update(
    triggerId: string,
    updates: Record<string, unknown>,
  ): Promise<WebhookTrigger | null> {
    void triggerId; void updates;
    console.warn(
      "[WebhookTriggerApi] update is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async delete(triggerId: string): Promise<boolean> {
    void triggerId;
    console.warn(
      "[WebhookTriggerApi] delete is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return false;
  }
}