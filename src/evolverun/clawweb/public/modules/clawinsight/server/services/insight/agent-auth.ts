import crypto from "node:crypto";
import type { Request } from "express";
import { InsightUnauthorizedError } from "./insight-service.js";

declare module "express-serve-static-core" {
  interface Request {
    insightAgentId?: string;
    insightAgentScopes?: string[];
  }
}

export type InsightAgentClient = {
  secret: string;
  scopes: string[];
};

export type InsightAgentAuthOptions = {
  clients: Record<string, InsightAgentClient>;
  maxAgeMs?: number;
  allowLocalUnsigned?: boolean;
  now?: () => number;
};

function bodyDigest(body: unknown): string {
  const content = body && typeof body === "object" ? JSON.stringify(body) : "";
  return crypto.createHash("sha256").update(content).digest("hex");
}

function isLocalRequest(req: Request): boolean {
  const host = req.get("host") ?? "";
  return host.startsWith("localhost") || host.startsWith("127.0.0.1") || host.startsWith("[::1]");
}

export function resolveInsightAgentClients(
  env: Record<string, string | undefined> = process.env,
): Record<string, InsightAgentClient> {
  const raw = env.INSIGHT_AGENT_CLIENTS_JSON?.trim();
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("INSIGHT_AGENT_CLIENTS_JSON 不是合法 JSON");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("INSIGHT_AGENT_CLIENTS_JSON 必须是对象");
  }
  const result: Record<string, InsightAgentClient> = {};
  for (const [agentId, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error(`Agent ${agentId} 配置不合法`);
    }
    const record = value as Record<string, unknown>;
    const secret = String(record.secret ?? "").trim();
    const scopes = Array.isArray(record.scopes)
      ? record.scopes.map((item) => String(item).trim()).filter(Boolean)
      : [];
    if (!agentId.trim() || !secret || scopes.length === 0) {
      throw new Error(`Agent ${agentId} 缺少 secret/scopes`);
    }
    result[agentId.trim()] = { secret, scopes };
  }
  return result;
}

export class InsightAgentAuthorizer {
  private readonly maxAgeMs: number;
  private readonly now: () => number;
  private readonly seenNonces = new Map<string, number>();

  constructor(private readonly options: InsightAgentAuthOptions) {
    this.maxAgeMs = options.maxAgeMs ?? 5 * 60_000;
    this.now = options.now ?? Date.now;
  }

  authorize(req: Request, requiredScope: string): string {
    const configuredClients = Object.keys(this.options.clients).length;
    if (configuredClients === 0 && this.options.allowLocalUnsigned !== false && isLocalRequest(req)) {
      req.insightAgentId = "local-dev";
      req.insightAgentScopes = ["*"];
      return "local-dev";
    }

    const agentId = req.header("X-Agent-Id")?.trim() ?? "";
    const timestampRaw = req.header("X-Agent-Timestamp")?.trim() ?? "";
    const nonce = req.header("X-Agent-Nonce")?.trim() ?? "";
    const suppliedDigest = req.header("X-Agent-Body-SHA256")?.trim().toLowerCase() ?? "";
    const suppliedSignature = req.header("X-Agent-Signature")?.trim().toLowerCase() ?? "";
    const client = this.options.clients[agentId];
    if (!agentId || !timestampRaw || !nonce || !suppliedDigest || !suppliedSignature || !client) {
      throw new InsightUnauthorizedError("Agent 机器鉴权信息不完整或身份无效");
    }
    if (!client.scopes.includes(requiredScope) && !client.scopes.includes("*")) {
      throw new InsightUnauthorizedError(`Agent 缺少接口权限: ${requiredScope}`);
    }

    const timestampNumber = Number(timestampRaw);
    const timestampMs = timestampNumber < 10_000_000_000 ? timestampNumber * 1000 : timestampNumber;
    const now = this.now();
    if (!Number.isFinite(timestampMs) || Math.abs(now - timestampMs) > this.maxAgeMs) {
      throw new InsightUnauthorizedError("Agent 请求时间戳已过期");
    }
    const nonceKey = `${agentId}:${nonce}`;
    this.cleanupNonces(now);
    if (this.seenNonces.has(nonceKey)) {
      throw new InsightUnauthorizedError("Agent 请求 nonce 已使用");
    }

    const actualDigest = bodyDigest(req.body);
    if (
      suppliedDigest.length !== actualDigest.length
      || !crypto.timingSafeEqual(Buffer.from(suppliedDigest), Buffer.from(actualDigest))
    ) {
      throw new InsightUnauthorizedError("Agent 请求 Body 摘要不一致");
    }
    const canonical = [req.method.toUpperCase(), req.path, timestampRaw, nonce, actualDigest].join("\n");
    const expectedSignature = crypto.createHmac("sha256", client.secret).update(canonical).digest("hex");
    if (
      suppliedSignature.length !== expectedSignature.length
      || !crypto.timingSafeEqual(Buffer.from(suppliedSignature), Buffer.from(expectedSignature))
    ) {
      throw new InsightUnauthorizedError("Agent 请求签名无效");
    }
    this.seenNonces.set(nonceKey, now + this.maxAgeMs);
    req.insightAgentId = agentId;
    req.insightAgentScopes = [...client.scopes];
    return agentId;
  }

  private cleanupNonces(now: number): void {
    for (const [key, expiresAt] of this.seenNonces) {
      if (expiresAt <= now) this.seenNonces.delete(key);
    }
  }
}
