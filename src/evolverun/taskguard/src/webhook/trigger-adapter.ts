/**
 * HTTP Trigger Adapter — implements ITriggerAdapter for webhook requests.
 *
 * Orchestrates signature validation, IP whitelist check,
 * idempotency check, payload mapping, workflow trigger,
 * and event logging.
 */
import type { ITriggerAdapter, TriggerConfig, TriggerEvent, TriggerResult } from "./types.js";
import { verifySignature } from "./signature-validator.js";
import { isIpAllowed, extractClientIp } from "./ip-whitelist.js";
import { mapPayload } from "./payload-mapper.js";
import type { WebhookTriggerRepository } from "../db/repositories/webhook-trigger-repository.js";
import type { WebhookEventRepository } from "../db/repositories/webhook-event-repository.js";
import type { WebhookConfig } from "../config/types.js";
import type { WebhookTrigger } from "./types.js";

export type WorkflowLauncher = (opts: {
  workflowId: string;
  packId?: string;
  params: Record<string, string>;
  executionMode: string;
  chatInjectLevel?: import("../inject-level.js").InjectLevel;
}) => Promise<string | null>;

/** Coerce an unknown payload value into a valid InjectLevel, or undefined. */
function normalizeInjectLevel(value: unknown): import("../inject-level.js").InjectLevel | undefined {
  if (value === "perf" || value === "simple" || value === "full") return value;
  return undefined;
}

export class HttpTriggerAdapter implements ITriggerAdapter {
  readonly type = "http";
  private config: TriggerConfig | null = null;
  private triggerRow: WebhookTrigger | null = null;

  constructor(
    private webhookConfig: WebhookConfig,
    private triggerStore: WebhookTriggerRepository,
    private eventStore: WebhookEventRepository,
    private launchWorkflow: WorkflowLauncher,
  ) {}

  async initialize(config: TriggerConfig): Promise<void> {
    this.config = config;
  }

  async shutdown(): Promise<void> {
    this.config = null;
    this.triggerRow = null;
  }

  /**
   * Process a webhook trigger event.
   * This is the main entry point called from the route handler.
   */
  async handleTrigger(event: TriggerEvent): Promise<TriggerResult> {
    if (!this.config) {
      return { accepted: false, status: "error", statusCode: 500, errorMessage: "Adapter not initialized" };
    }

    const source = event.source; // "http"
    const rawPayload = event.rawPayload as Record<string, unknown>;
    const headers = event.headers ?? {};
    const metadata = event.metadata ?? {};
    const clientIp = (metadata.clientIp as string) ?? extractClientIp(headers);

    // Step 1: IP whitelist check
    if (this.config.allowedIps && this.config.allowedIps.length > 0) {
      if (!isIpAllowed(clientIp, this.config.allowedIps)) {
        return {
          accepted: false,
          status: "rejected",
          statusCode: 403,
          errorMessage: `IP ${clientIp} not allowed`,
        };
      }
    }

    // Step 2: Signature validation
    const rawBody = typeof event.rawPayload === "string"
      ? event.rawPayload
      : JSON.stringify(event.rawPayload);
    const signatureHeader = headers["x-signature-256"];

    if (!verifySignature(rawBody, this.config.secret ?? null, signatureHeader)) {
      if (this.config.secret) {
        return {
          accepted: false,
          status: "rejected",
          statusCode: 401,
          errorMessage: "Invalid or missing signature",
        };
      }
    }

    // Step 3: Idempotency check
    const requestId = headers["x-request-id"];
    if (requestId && this.webhookConfig.idempotencyWindowHours > 0) {
      const duplicate = await this.eventStore.findDuplicate(
        requestId,
        this.webhookConfig.idempotencyWindowHours,
      );
      if (duplicate) {
        return {
          accepted: false,
          status: "duplicated",
          statusCode: 200,
          errorMessage: "Request already processed",
          flowId: duplicate.flow_id ?? undefined,
        };
      }
    }

    // Step 4: Payload mapping
    const params = this.config.payloadMapping
      ? mapPayload(this.config.payloadMapping, rawPayload, headers)
      : {};

    // Step 5: Inject reserved params
    params.triggerSource = "webhook";
    params.triggerId = this.config.triggerId;

    // Optional per-trigger chatInject level override (top-level payload field).
    const triggerChatInjectLevel = normalizeInjectLevel(rawPayload.chatInjectLevel);

    // Step 6: Launch workflow
    let flowId: string | null = null;
    try {
      flowId = await this.launchWorkflow({
        workflowId: this.config.workflowId,
        packId: this.config.packId,
        params,
        executionMode: "private",
        chatInjectLevel: triggerChatInjectLevel,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return {
        accepted: false,
        status: "error",
        statusCode: 500,
        errorMessage: `Workflow launch failed: ${msg}`,
      };
    }

    if (!flowId) {
      return {
        accepted: false,
        status: "error",
        statusCode: 500,
        errorMessage: "Workflow launch returned no flow ID",
      };
    }

    return {
      accepted: true,
      status: "accepted",
      statusCode: 202,
      flowId,
    };
  }

  /**
   * Load a trigger configuration from the database by triggerId.
   * Returns null if not found or disabled.
   */
  async loadTrigger(triggerId: string): Promise<TriggerConfig | null> {
    const row = await this.triggerStore.getByTriggerId(triggerId);
    if (!row || row.enabled !== 1) return null;

    this.triggerRow = row;

    const config: TriggerConfig = {
      triggerId: row.trigger_id,
      workflowId: row.workflow_id,
      packId: row.pack_id ?? undefined,
      secret: row.secret ?? undefined,
      payloadMapping: row.payload_mapping ? JSON.parse(row.payload_mapping) : undefined,
      allowedIps: row.allowed_ips ? JSON.parse(row.allowed_ips) : undefined,
      enabled: row.enabled === 1,
    };

    await this.initialize(config);
    return config;
  }

  /** Get the loaded trigger row (for event logging). */
  getTriggerRow(): WebhookTrigger | null {
    return this.triggerRow;
  }
}