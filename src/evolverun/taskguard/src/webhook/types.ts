/**
 * Type definitions for the Webhook trigger system.
 */

// ── Trigger Adapter Interface ──

export type TriggerConfig = {
  triggerId: string;
  workflowId: string;
  packId?: string;
  payloadMapping?: Record<string, string>;
  secret?: string;
  allowedIps?: string[];
  enabled: boolean;
};

export type TriggerEvent = {
  source: string;
  rawPayload: unknown;
  headers?: Record<string, string>;
  metadata?: Record<string, unknown>;
};

export type TriggerResult = {
  accepted: boolean;
  flowId?: string;
  status: "accepted" | "rejected" | "duplicated" | "error";
  statusCode: number;
  errorMessage?: string;
};

export interface ITriggerAdapter {
  readonly type: string;
  initialize(config: TriggerConfig): Promise<void>;
  shutdown(): Promise<void>;
  handleTrigger(event: TriggerEvent): Promise<TriggerResult>;
}

// ── DB Row Types ──

export type WebhookTrigger = {
  id: number;
  trigger_id: string;
  workflow_id: string;
  pack_id: string | null;
  secret: string | null;
  payload_mapping: string | null;
  allowed_ips: string | null;
  enabled: number;
  description: string | null;
  gmt_create: number;
  gmt_modified: number | null;
};

export type WebhookEvent = {
  id: number;
  event_id: string;
  trigger_id: string;
  flow_id: string | null;
  status: string;
  request_method: string;
  request_headers: string | null;
  request_body_hash: string | null;
  response_code: number | null;
  error_message: string | null;
  ip_address: string | null;
  gmt_create: number;
  gmt_modified: number;
};