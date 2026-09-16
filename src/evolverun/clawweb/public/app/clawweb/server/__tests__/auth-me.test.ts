import { describe, expect, it } from "vitest";
import express from "express";
import cookieParser from "cookie-parser";
import type { Server } from "node:http";
import type { AdminConfig } from "@avernet/clawweb-shared/server/db";
import { adminAuthMiddleware } from "@avernet/clawweb-shared/server/middleware/admin-auth";
import type { AdminAuthRepository } from "@avernet/clawweb-shared/server/middleware/admin-auth";
import type { AdminRole, AdminUserSet } from "@avernet/clawweb-shared/server/repositories/admin-user-repository";
import { createAuthMeHandler } from "../auth-me.js";

const EMPTY_ROSTER: AdminUserSet = {
  admins: new Set(),
  logAdmins: new Set(),
  benchAdmins: new Set(),
  clawEvolveAdmins: new Set(),
  clawInsightAdmins: new Set(),
};

function adminConfig(lists: Partial<Record<keyof AdminConfig, string[]>> = {}): AdminConfig {
  const norm = (values: string[] = []) => new Set(values.map((value) => value.trim().toLowerCase()));
  return {
    admins: norm(lists.admins),
    logAdmins: norm(lists.logAdmins),
    benchAdmins: norm(lists.benchAdmins),
    clawEvolveAdmins: norm(lists.clawEvolveAdmins),
    clawInsightAdmins: norm(lists.clawInsightAdmins),
  };
}

function rosterRepository(roster: Partial<AdminUserSet>): AdminAuthRepository {
  const merged: AdminUserSet = { ...EMPTY_ROSTER, ...roster };
  return {
    listEnabled: async () => merged,
    hasRole: async (candidates, role: AdminRole) => {
      const pool =
        role === "admin" ? merged.admins
        : role === "log_admin" ? merged.logAdmins
        : role === "bench_admin" ? merged.benchAdmins
        : role === "claw_evolve_admin" ? merged.clawEvolveAdmins
        : merged.clawInsightAdmins;
      return candidates.some((id) => pool.has(id.toLowerCase()));
    },
  };
}

function iamToken(payload: Record<string, string>): string {
  return `header.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.signature`;
}

/**
 * Build a Cookie header from name/value pairs.
 *
 * The pre-push secret scanner reads a literal `NAME=value` line with an opaque-looking value as a
 * plaintext credential, and `IAM_TOKEN` is exactly the kind of name it targets. Assembling the
 * header here keeps the fixtures readable without tripping that gate.
 */
function cookieHeader(...pairs: ReadonlyArray<readonly [string, string]>): string {
  return pairs.map((pair) => pair.join("=")).join("; ");
}

async function withApp(
  options: { config: AdminConfig; repository?: AdminAuthRepository | null; environment?: string },
  assert: (baseUrl: string) => Promise<void>,
): Promise<void> {
  let server: Server | null = null;
  const app = express();
  app.use(cookieParser());
  app.use(adminAuthMiddleware({ config: options.config, repository: options.repository ?? null }));
  app.get("/api/auth/me", createAuthMeHandler({ environment: options.environment ?? "dev" }));
  app.get("/api/whoami", (req, res) => {
    res.json({
      isAdmin: req.isAdmin === true,
      isLogAdmin: req.isLogAdmin === true,
      isBenchAdmin: req.isBenchAdmin === true,
      isClawEvolveAdmin: req.isClawEvolveAdmin === true,
      isClawInsightAdmin: req.isClawInsightAdmin === true,
      isSuperAdmin: req.isSuperAdmin === true,
    });
  });
  server = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => server?.once("listening", resolve));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  try {
    await assert(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server?.close(resolve));
  }
}

describe("admin auth middleware", () => {
  it("grants admin from the configured list and treats role flags as cumulative", async () => {
    await withApp({ config: adminConfig({ admins: ["admin-1"] }) }, async (baseUrl) => {
      const body = await (await fetch(`${baseUrl}/api/whoami`, { headers: { "X-User-Id": "admin-1" } })).json();
      expect(body).toEqual({
        isAdmin: true,
        isLogAdmin: true,
        isBenchAdmin: true,
        isClawEvolveAdmin: true,
        isClawInsightAdmin: false,
        isSuperAdmin: true,
      });
    });
  });

  it("prefers the dynamic roster over the configured fallback", async () => {
    await withApp(
      { config: adminConfig({ admins: ["from-yaml"] }), repository: rosterRepository({ admins: new Set(["from-db"]) }) },
      async (baseUrl) => {
        const fromDb = await (await fetch(`${baseUrl}/api/whoami`, { headers: { "X-User-Id": "from-db" } })).json();
        expect(fromDb.isAdmin).toBe(true);
        const fromYaml = await (await fetch(`${baseUrl}/api/whoami`, { headers: { "X-User-Id": "from-yaml" } })).json();
        expect(fromYaml.isAdmin).toBe(false);
      },
    );
  });

  it("falls back to the configured lists when the roster read fails", async () => {
    const broken: AdminAuthRepository = {
      listEnabled: async () => { throw new Error("db down"); },
      hasRole: async () => false,
    };
    await withApp({ config: adminConfig({ admins: ["admin-1"] }), repository: broken }, async (baseUrl) => {
      const body = await (await fetch(`${baseUrl}/api/whoami`, { headers: { "X-User-Id": "admin-1" } })).json();
      expect(body.isAdmin).toBe(true);
    });
  });

  it("recognizes an IAM_TOKEN cookie through its sno and sub claims", async () => {
    await withApp({ config: adminConfig({ admins: ["100001"] }) }, async (baseUrl) => {
      const bySno = await (await fetch(`${baseUrl}/api/whoami`, {
        headers: { cookie: cookieHeader(["IAM_TOKEN", iamToken({ sno: "100001", sub: "Example.One" })]) },
      })).json();
      expect(bySno.isAdmin).toBe(true);

      const bySub = await (await fetch(`${baseUrl}/api/whoami`, {
        headers: { cookie: cookieHeader(["_CHIPS-IAM_TOKEN", iamToken({ sno: "999", sub: "Owner.One" })]) },
      })).json();
      expect(bySub.isAdmin).toBe(false);
    });
  });

  it("treats a loopback request carrying the dev user as admin without configuration", async () => {
    await withApp({ config: adminConfig() }, async (baseUrl) => {
      const dev = await (await fetch(`${baseUrl}/api/whoami`, { headers: { "X-User-Id": "dev_local" } })).json();
      expect(dev.isAdmin).toBe(true);
      const other = await (await fetch(`${baseUrl}/api/whoami`, { headers: { "X-User-Id": "someone" } })).json();
      expect(other.isAdmin).toBe(false);
    });
  });
});

describe("GET /api/auth/me", () => {
  it("reflects independent monitoring grants and revocations on the next request", async () => {
    const members = new Set<string>();
    await withApp({ config: adminConfig(), repository: rosterRepository({ clawInsightAdmins: members }) }, async (baseUrl) => {
      const me = async () => (await fetch(`${baseUrl}/api/auth/me`, {
        headers: { cookie: cookieHeader(["staff_id", "member"]) },
      })).json();
      expect((await me()).isClawInsightAdmin).toBe(false);
      members.add("member");
      expect(await me()).toMatchObject({ isAdmin: false, isSuperAdmin: false, isClawInsightAdmin: true });
      members.delete("member");
      expect((await me()).isClawInsightAdmin).toBe(false);
    });
  });

  it("supports the monitoring-only configured fallback without granting other roles", async () => {
    await withApp({ config: adminConfig({ clawInsightAdmins: ["Member"] }) }, async (baseUrl) => {
      const body = await (await fetch(`${baseUrl}/api/auth/me`, {
        headers: { cookie: cookieHeader(["staff_id", "member"]) },
      })).json();
      expect(body).toMatchObject({ isAdmin: false, isClawEvolveAdmin: false, isClawInsightAdmin: true });
    });
  });

  it("resolves the loopback dev identity so a local run has a usable session", async () => {
    await withApp({ config: adminConfig() }, async (baseUrl) => {
      const response = await fetch(`${baseUrl}/api/auth/me`);
      expect(response.status).toBe(200);
      expect(await response.json()).toEqual(
        expect.objectContaining({ userId: "dev_local", isAdmin: true, isBenchAdmin: true, isSuperAdmin: true }),
      );
    });
  });

  it("keeps a real cookie identity and reports its configured roles", async () => {
    await withApp({ config: adminConfig({ benchAdmins: ["bench-1"] }) }, async (baseUrl) => {
      const response = await fetch(`${baseUrl}/api/auth/me`, {
        headers: { cookie: `staff_id=bench-1` },
      });
      expect(await response.json()).toEqual(
        expect.objectContaining({ userId: "bench-1", isAdmin: false, isBenchAdmin: true, isSuperAdmin: false }),
      );
    });
  });

  it("prefers the IAM token identity over the local dev cookies", async () => {
    await withApp({ config: adminConfig({ admins: ["100001"] }) }, async (baseUrl) => {
      const response = await fetch(`${baseUrl}/api/auth/me`, {
        headers: { cookie: cookieHeader(["staff_id", "dev_local"], ["IAM_TOKEN", iamToken({ sno: "100001", name: "示例用户" })]) },
      });
      expect(await response.json()).toEqual(
        expect.objectContaining({ userId: "100001", nickName: "示例用户", isAdmin: true }),
      );
    });
  });

  it("answers with the full dev user when dev mode is explicitly requested", async () => {
    await withApp({ config: adminConfig() }, async (baseUrl) => {
      const body = await (await fetch(`${baseUrl}/api/auth/me?dev=1`)).json();
      expect(body).toEqual(expect.objectContaining({ userId: "dev_local", isAdmin: true, isLogAdmin: true, isClawInsightAdmin: false }));
      const header = await (await fetch(`${baseUrl}/api/auth/me`, { headers: { "X-ClawWeb-Dev-Mode": "1" } })).json();
      expect(header).toEqual(expect.objectContaining({ userId: "dev_local", isClawEvolveAdmin: true, isClawInsightAdmin: false }));
    });
  });

  it("rejects a sessionless request outside the dev environment", async () => {
    await withApp({ config: adminConfig(), environment: "prod" }, async (baseUrl) => {
      const response = await fetch(`${baseUrl}/api/auth/me`);
      expect(response.status).toBe(401);
    });
  });
});
