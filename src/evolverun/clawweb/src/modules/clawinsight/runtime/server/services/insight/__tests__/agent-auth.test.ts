import crypto from "node:crypto";
import type { Request } from "express";
import { describe, expect, it } from "vitest";
import { InsightAgentAuthorizer } from "../agent-auth.js";

function signedRequest(input: {
  secret: string;
  agentId?: string;
  scopeBody?: Record<string, unknown>;
  nonce?: string;
  timestamp?: string;
  path?: string;
}): Request {
  const agentId = input.agentId ?? "governance-agent";
  const body = input.scopeBody ?? { action: "test" };
  const timestamp = input.timestamp ?? "1786700000000";
  const nonce = input.nonce ?? "nonce-1";
  const path = input.path ?? "/internal/governance/actions";
  const digest = crypto.createHash("sha256").update(JSON.stringify(body)).digest("hex");
  const canonical = ["POST", path, timestamp, nonce, digest].join("\n");
  const signature = crypto.createHmac("sha256", input.secret).update(canonical).digest("hex");
  const headers: Record<string, string> = {
    "x-agent-id": agentId,
    "x-agent-timestamp": timestamp,
    "x-agent-nonce": nonce,
    "x-agent-body-sha256": digest,
    "x-agent-signature": signature,
    host: "clawweb-pre.test",
  };
  return {
    method: "POST",
    path,
    body,
    header(name: string) { return headers[name.toLowerCase()]; },
    get(name: string) { return headers[name.toLowerCase()]; },
  } as unknown as Request;
}

describe("InsightAgentAuthorizer", () => {
  const secret = "unit-test-machine-secret";
  const now = 1_786_700_000_000;

  it("verifies HMAC, scope, timestamp, and nonce", () => {
    const authorizer = new InsightAgentAuthorizer({
      clients: { "governance-agent": { secret, scopes: ["action.write"] } },
      now: () => now,
    });
    const request = signedRequest({ secret });
    expect(authorizer.authorize(request, "action.write")).toBe("governance-agent");
    expect(request.insightAgentScopes).toEqual(["action.write"]);
    expect(() => authorizer.authorize(request, "action.write")).toThrow("nonce 已使用");
  });

  it("rejects missing scope and changed body", () => {
    const authorizer = new InsightAgentAuthorizer({
      clients: { "governance-agent": { secret, scopes: ["rule.read"] } },
      now: () => now,
    });
    const request = signedRequest({ secret, nonce: "nonce-2" });
    expect(() => authorizer.authorize(request, "action.write")).toThrow("缺少接口权限");

    const tampered = signedRequest({ secret, nonce: "nonce-3" });
    tampered.body = { action: "changed" };
    expect(() => authorizer.authorize(tampered, "rule.read")).toThrow("Body 摘要不一致");
  });
});
