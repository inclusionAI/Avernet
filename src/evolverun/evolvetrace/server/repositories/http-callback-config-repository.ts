/**
 * Stub HttpCallbackConfigRepository for Evolvetrace.
 */
import type { IDatabase } from "../db.js";

export type HttpCallbackConfigRow = {
  id: number;
  workflow_id: string;
  config_id: string;
  name: string;
  url: string;
  secret: string;
  method: string;
  headers: string | null;
  notify_on: string | null;
  enabled: number;
  timeout_ms: number;
  max_retries: number;
  retry_delay_ms: number;
  include_node_output: number;
  gmt_create: number;
  gmt_modified: number;
};

export type HttpCallbackConfigInsertInput = {
  configId: string;
  workflowId: string;
  name: string;
  url: string;
  secret?: string;
  enabled?: boolean;
  notifyOn: string[];
  timeoutMs?: number;
  maxRetries?: number;
  retryDelayMs?: number;
  includeNodeOutput?: boolean;
};

export type HttpCallbackConfigUpdateInput = {
  name?: string;
  url?: string;
  secret?: string;
  notifyOn?: string[];
  enabled?: boolean;
  timeoutMs?: number;
  maxRetries?: number;
  retryDelayMs?: number;
  includeNodeOutput?: boolean;
};

function dummyRow(data: HttpCallbackConfigInsertInput): HttpCallbackConfigRow {
  const now = Math.floor(Date.now() / 1000);
  return {
    id: 0,
    workflow_id: data.workflowId,
    config_id: data.configId,
    name: data.name,
    url: data.url,
    secret: data.secret ?? "",
    method: "POST",
    headers: null,
    notify_on: JSON.stringify(data.notifyOn),
    enabled: data.enabled === false ? 0 : 1,
    timeout_ms: data.timeoutMs ?? 5000,
    max_retries: data.maxRetries ?? 3,
    retry_delay_ms: data.retryDelayMs ?? 1000,
    include_node_output: data.includeNodeOutput ? 1 : 0,
    gmt_create: now,
    gmt_modified: now,
  };
}

export class HttpCallbackConfigRepository {
  constructor(private db: IDatabase) {}

  async listByWorkflowId(_workflowId: string): Promise<HttpCallbackConfigRow[]> {
    return [];
  }

  async findByWorkflowId(_workflowId: string): Promise<HttpCallbackConfigRow[]> {
    return [];
  }

  async findByConfigId(_configId: string): Promise<HttpCallbackConfigRow | null> {
    return null;
  }

  async insert(data: HttpCallbackConfigInsertInput): Promise<HttpCallbackConfigRow> {
    return dummyRow(data);
  }

  async create(data: Omit<HttpCallbackConfigRow, "id" | "gmt_create" | "gmt_modified">): Promise<HttpCallbackConfigRow | null> {
    return null;
  }

  async update(configId: string, data: HttpCallbackConfigUpdateInput): Promise<HttpCallbackConfigRow | null> {
    return null;
  }

  async updateWorkflowId(_oldId: string, _newId: string): Promise<void> {
    // Stub: no-op
  }

  async delete(_configId: string): Promise<boolean> {
    return false;
  }

  async deleteByConfigId(_configId: string): Promise<boolean> {
    return false;
  }

  async deleteByWorkflowId(_workflowId: string): Promise<number> {
    return 0;
  }
}
