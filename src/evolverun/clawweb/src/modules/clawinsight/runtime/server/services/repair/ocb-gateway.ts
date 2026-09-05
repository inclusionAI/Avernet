import type { RepairAuthorizationScope, RepairTargetEnvironment } from "./contracts.js";
import { RepairError, repairValidation } from "./errors.js";
import { redactPersistableText } from "./redaction.js";

export const OCB_REPAIR_OPERATION_TYPES = ["restart_bot"] as const;
/** OCB is retained only as the authenticated Bot restart control plane. */
export const OCB_RESTART_BASE_URLS: Partial<Record<RepairTargetEnvironment, string>> = {
  pre: "https://agentclaw-pre.alipay.com",
  prod: "https://agentclaw-prod.alipay.com",
};
export type OcbRepairOperationType = typeof OCB_REPAIR_OPERATION_TYPES[number];
export type OcbRepairOperation = { type: "restart_bot"; params?: Record<string, never> };

export type OcbRepairGatewayResult = {
  operation: OcbRepairOperationType;
  result: unknown;
  requiresTargetRefresh: true;
};

export type OcbRepairGatewayConfig = {
  baseUrls: Partial<Record<RepairTargetEnvironment, string>>;
  timeoutMs?: number;
  maxRawResponseBytes?: number;
  maxRequestBytes?: number;
};

type OcbEnvelope = {
  success?: unknown;
  error_code?: unknown;
  message?: unknown;
};

function plainObject(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    repairValidation("invalid_ocb_operation", `${field} 必须是 JSON 对象`);
  }
  return value as Record<string, unknown>;
}

export function parseOcbRepairOperation(value: unknown): OcbRepairOperation {
  const raw = plainObject(value, "operation");
  if (raw.type !== "restart_bot") {
    repairValidation("unsupported_ocb_operation", "Repair 仅通过 OCB 执行 Bot 重启");
  }
  if (Object.keys(raw).some(key => key !== "type" && key !== "params")) {
    repairValidation("invalid_ocb_operation", "OCB operation 包含不支持的字段");
  }
  const params = raw.params == null ? {} : plainObject(raw.params, "operation.params");
  if (Object.keys(params).length > 0) {
    repairValidation("invalid_ocb_operation", "restart_bot 不接受参数");
  }
  return { type: "restart_bot", params: {} };
}

function validateBaseUrl(value: string): URL {
  const url = new URL(value);
  if (!new Set(["http:", "https:"]).has(url.protocol) || url.username || url.password) {
    throw new Error("OCB MANAGEMENT 环境映射必须是无凭据的 HTTP(S) origin");
  }
  return url;
}

function forwardedAuthHeaders(input: Record<string, string>, callerUserId: string): Record<string, string> {
  const cookie = Object.entries(input).find(([name]) => name.toLowerCase() === "cookie")?.[1]?.trim();
  const headerUserId = Object.entries(input).find(([name]) => name.toLowerCase() === "x-user-id")?.[1]?.trim();
  if (!cookie || !headerUserId || headerUserId !== callerUserId) {
    throw new RepairError(401, "repair_ocb_identity_required", "Bot 重启需要当前操作者的登录身份");
  }
  return { cookie, "x-user-id": callerUserId };
}

function assertScope(scope: RepairAuthorizationScope): void {
  if (!scope.actorUserId || !scope.ownerId || !scope.botId) {
    repairValidation("invalid_repair_scope", "Repair frozen authorization scope 不完整");
  }
  if (!new Set<RepairTargetEnvironment>(["pre", "prod"]).has(scope.environment)) {
    repairValidation("invalid_repair_scope", "Repair frozen environment 不合法");
  }
}

function envelopeOf(value: unknown): OcbEnvelope | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as OcbEnvelope : null;
}

function errorStatus(response: Response, body: OcbEnvelope | null): number {
  if (!response.ok) return response.status;
  const status = Number(body?.error_code);
  return Number.isInteger(status) && status >= 400 && status <= 599 ? status : 502;
}

async function limitedResponseText(response: Response, maxBytes: number): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel().catch(() => undefined);
      throw new RepairError(502, "ocb_response_too_large", "OCB 响应超过 Repair gateway 上限");
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks.map(chunk => Buffer.from(chunk))).toString("utf8");
}

export class OcbRepairGateway {
  private readonly baseUrls: Partial<Record<RepairTargetEnvironment, URL>>;
  private readonly timeoutMs: number;
  private readonly maxRawResponseBytes: number;
  private readonly maxRequestBytes: number;

  constructor(config: OcbRepairGatewayConfig) {
    this.baseUrls = Object.fromEntries(Object.entries(config.baseUrls)
      .filter((entry): entry is [RepairTargetEnvironment, string] => Boolean(entry[1]))
      .map(([environment, value]) => [environment, validateBaseUrl(value)]));
    this.timeoutMs = config.timeoutMs ?? 10_000;
    this.maxRawResponseBytes = config.maxRawResponseBytes ?? 1024 * 1024;
    this.maxRequestBytes = config.maxRequestBytes ?? 64 * 1024;
  }

  async execute(input: {
    scope: RepairAuthorizationScope;
    operation: OcbRepairOperation;
    authHeaders: Record<string, string>;
    callerUserId: string;
    callerIsAdmin: boolean;
  }): Promise<OcbRepairGatewayResult> {
    assertScope(input.scope);
    parseOcbRepairOperation(input.operation);
    const callerUserId = input.callerUserId.trim();
    if (!callerUserId) repairValidation("invalid_repair_actor", "Bot 重启缺少当前操作者");
    if (!input.callerIsAdmin && callerUserId !== input.scope.ownerId) {
      throw new RepairError(403, "repair_owner_scope_required", "普通用户只能重启自己的 Bot");
    }
    const baseUrl = this.baseUrls[input.scope.environment];
    if (!baseUrl) {
      throw new RepairError(503, "repair_ocb_not_configured", `Repair 未配置 ${input.scope.environment} OCB Backend`);
    }
    const headers = forwardedAuthHeaders(input.authHeaders, callerUserId);
    const query = new URLSearchParams();
    let path: string;
    let body: unknown;
    if (input.callerIsAdmin) {
      path = "/api/bots/restart-for-others";
      body = { target_user_id: input.scope.ownerId, target_bot_id: input.scope.botId };
    } else {
      path = `/api/bots/${encodeURIComponent(input.scope.botId)}/restart`;
      query.set("owner_id", input.scope.ownerId);
      body = {};
    }
    const result = await this.request({ baseUrl, path, query, body, authHeaders: headers });
    return { operation: "restart_bot", result, requiresTargetRefresh: true };
  }

  private async request(input: {
    baseUrl: URL;
    path: string;
    query: URLSearchParams;
    body: unknown;
    authHeaders: Record<string, string>;
  }): Promise<unknown> {
    const endpoint = new URL(input.path, input.baseUrl);
    endpoint.search = input.query.toString();
    const encodedBody = JSON.stringify(input.body);
    if (Buffer.byteLength(encodedBody, "utf8") > this.maxRequestBytes) {
      repairValidation("ocb_request_too_large", "OCB operation 请求超过 Repair gateway 上限");
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json", ...input.authHeaders },
        body: encodedBody,
        signal: controller.signal,
      });
      const raw = await limitedResponseText(response, this.maxRawResponseBytes);
      let parsed: unknown = null;
      if (raw) {
        try { parsed = JSON.parse(raw) as unknown; } catch { parsed = raw; }
      }
      const envelope = envelopeOf(parsed);
      if (!response.ok || envelope?.success === false) {
        const message = typeof envelope?.message === "string"
          ? envelope.message
          : `OCB restart 返回 HTTP ${response.status}`;
        throw new RepairError(
          errorStatus(response, envelope),
          "ocb_operation_rejected",
          redactPersistableText(message, 2_000),
        );
      }
      return parsed;
    } catch (error) {
      if (error instanceof RepairError) throw error;
      if (controller.signal.aborted) {
        throw new RepairError(504, "ocb_operation_timeout", "OCB restart 调用超时；重启状态未知");
      }
      throw new RepairError(502, "ocb_operation_failed", "OCB restart 调用失败");
    } finally {
      clearTimeout(timeout);
    }
  }
}
