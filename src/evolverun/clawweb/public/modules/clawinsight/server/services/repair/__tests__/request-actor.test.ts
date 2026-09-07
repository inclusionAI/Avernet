import type { Request } from "express";
import { describe, expect, it } from "vitest";
import { resolveRepairRequestActor } from "../request-actor.js";

function request(headers: Record<string, string>, cookies?: Record<string, string>): Request {
  const normalized = Object.fromEntries(
    Object.entries(headers).map(([name, value]) => [name.toLowerCase(), value]),
  );
  const getHeader = (name: string) => normalized[name.toLowerCase()];
  return { cookies, get: getHeader, header: getHeader } as unknown as Request;
}

describe("resolveRepairRequestActor", () => {
  it("uses the same user-header priority as other ClawWeb APIs", async () => {
    await expect(resolveRepairRequestActor(request({
      host: "clawweb.stable.alipay.net:5173",
      "x-staff-id": "staff-owner",
      "x-user-id": "fallback-owner",
    }, { staff_id: "cookie-owner" }))).resolves.toEqual({
      userId: "staff-owner", source: "request",
    });
  });

  it("accepts X-User-Id in a non-loopback environment", async () => {
    await expect(resolveRepairRequestActor(request({
      host: "clawweb.stable.alipay.net:5173",
      "x-user-id": "header-owner",
    }))).resolves.toEqual({ userId: "header-owner", source: "request" });
  });

  it("falls back to the staff_id cookie", async () => {
    await expect(resolveRepairRequestActor(request({
      host: "clawweb.stable.alipay.net:5173",
    }, { staff_id: "cookie-owner" }))).resolves.toEqual({
      userId: "cookie-owner", source: "request",
    });
  });

  it("uses dev_local only on a real loopback host", async () => {
    await expect(resolveRepairRequestActor(request({ host: "127.0.0.1:3001" }))).resolves.toEqual({
      userId: "dev_local", source: "local_dev",
    });
    await expect(resolveRepairRequestActor(request({ host: "localhost.attacker.example" }))).resolves.toBeNull();
  });
});
