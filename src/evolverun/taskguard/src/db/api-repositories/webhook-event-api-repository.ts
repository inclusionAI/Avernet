/**
 * WebhookEventApiRepository — HTTP client implementation for webhook event logging.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for webhook events.
 * All methods log a warning and return safe defaults.
 */
import crypto from "node:crypto";
import type { ApiClient } from "../api-client.js";
import type { WebhookEvent } from "../../webhook/types.js";
import type { IWebhookEventRepository } from "../repositories/types.js";

type RecordEventInput = {
  eventId: string;
  triggerId: string;
  flowId?: string | null;
  status: string;
  requestMethod: string;
  requestHeaders?: Record<string, string> | null;
  requestBodyHash?: string | null;
  responseCode?: number | null;
  errorMessage?: string | null;
  ipAddress?: string | null;
};

export class WebhookEventApiRepository implements IWebhookEventRepository {
  constructor(private api: ApiClient) {}

  async record(input: RecordEventInput): Promise<WebhookEvent | null> {
    void input;
    console.warn(
      "[WebhookEventApi] record is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return null;
  }

  async findDuplicate(eventId: string, windowHours: number): Promise<WebhookEvent | null> {
    void eventId; void windowHours;
    console.warn(
      "[WebhookEventApi] findDuplicate is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async findByTriggerId(
    triggerId: string, options?: { limit?: number; offset?: number },
  ): Promise<WebhookEvent[]> {
    void triggerId; void options;
    console.warn(
      "[WebhookEventApi] findByTriggerId is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }

  async deleteOlderThan(retentionDays: number): Promise<number> {
    void retentionDays;
    console.warn(
      "[WebhookEventApi] deleteOlderThan is not supported over HTTP API mode " +
        "(no server endpoint). Skipped.",
    );
    return 0;
  }

  static hashBody(body: string): string {
    return crypto.createHash("sha256").update(body).digest("hex");
  }
}