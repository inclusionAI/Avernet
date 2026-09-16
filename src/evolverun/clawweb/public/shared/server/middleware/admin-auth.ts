/**
 * Admin authentication middleware for ClawWeb.
 *
 * Reads the admin roster from the dynamic repository (falling back to config) and injects
 * `req.isAdmin` / `req.isLogAdmin` / `req.isBenchAdmin` / `req.isClawEvolveAdmin` /
 * `req.isClawInsightAdmin` / `req.isSuperAdmin` into every request. It never blocks a request: the bypass logic lives in
 * the individual route handlers.
 *
 * Identity candidates are collected in this order:
 * 1. `X-User-Id` header (direct override)
 * 2. `staff_id` cookie
 * 3. `IAM_TOKEN` / `_CHIPS-IAM_TOKEN` cookie — the JWT payload yields `sno` (工号) and `sub` (账号)
 *
 * A candidate list may hold either identifier; a match on any candidate grants the role.
 * When `auth.admins` is empty and no roster rows are enabled, `req.isAdmin` is always false.
 */
import type { NextFunction, Request, Response } from "express";
import type { AdminConfig } from "../db.js";
import type { AdminRole, AdminUserSet } from "../repositories/admin-user-repository.js";

/** Narrow view of `AdminUserRepository` so hosts and tests can supply any equivalent source. */
export type AdminAuthRepository = {
  listEnabled(): Promise<AdminUserSet>;
  hasRole(candidates: readonly string[], role: AdminRole): Promise<boolean>;
};

export type AdminAuthOptions = {
  /** Static fallback config, used when the repository is unavailable. */
  config: AdminConfig;
  /** Optional dynamic roster, reloaded on every request so runtime changes apply immediately. */
  repository?: AdminAuthRepository | null;
  /**
   * Identity used by loopback development runs. A loopback request carrying this identity is an
   * admin regardless of configuration, which is what makes a local run usable without SSO.
   */
  devUserId?: string;
};

type IamTokenPayload = { sno?: string; name?: string; sub?: string };

function decodeJwtPayload(token: string): IamTokenPayload | null {
  const payloadPart = token.split(".")[1];
  if (!payloadPart) return null;
  try {
    return JSON.parse(Buffer.from(payloadPart, "base64url").toString("utf8")) as IamTokenPayload;
  } catch {
    return null;
  }
}

export function getRequestCookie(req: Request, name: string): string | undefined {
  const cookies = req.cookies as Record<string, string> | undefined;
  if (cookies?.[name]) return cookies[name];

  const rawCookie = req.get("cookie") ?? "";
  for (const part of rawCookie.split(";")) {
    const [key, ...valueParts] = part.trim().split("=");
    if (key === name) return valueParts.join("=");
  }
  return undefined;
}

/** Collect every identifier this request can be recognized by, trimmed and lowercased. */
export function collectIdentityCandidates(req: Request): string[] {
  const candidates: string[] = [];

  const headerUserId = (req.headers["x-user-id"] as string | undefined)?.trim();
  if (headerUserId) candidates.push(headerUserId);

  const staffId = req.cookies?.staff_id?.trim();
  if (staffId) candidates.push(staffId);

  const iamToken = getRequestCookie(req, "IAM_TOKEN") ?? getRequestCookie(req, "_CHIPS-IAM_TOKEN");
  if (iamToken) {
    const payload = decodeJwtPayload(iamToken);
    if (payload?.sno) candidates.push(payload.sno);
    if (payload?.sub) candidates.push(payload.sub);
  }

  return [...new Set(candidates.map((id) => id.toLowerCase()).filter(Boolean))];
}

function isLoopbackHost(req: Request): boolean {
  const host = req.get("host") ?? "";
  return host.includes("localhost") || host.includes("127.0.0.1");
}

async function resolveAdminSets(options: AdminAuthOptions): Promise<AdminUserSet> {
  if (options.repository) {
    try {
      return await options.repository.listEnabled();
    } catch (err) {
      console.warn(
        `[adminAuth] Failed to load dynamic admin sets: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }
  return {
    admins: new Set([...options.config.admins].map((id) => id.toLowerCase())),
    logAdmins: new Set([...options.config.logAdmins].map((id) => id.toLowerCase())),
    benchAdmins: new Set([...options.config.benchAdmins].map((id) => id.toLowerCase())),
    clawEvolveAdmins: new Set([...options.config.clawEvolveAdmins].map((id) => id.toLowerCase())),
    clawInsightAdmins: new Set([...options.config.clawInsightAdmins].map((id) => id.toLowerCase())),
  };
}

export function adminAuthMiddleware(options: AdminAuthOptions) {
  const devUserId = (options.devUserId ?? "dev_local").toLowerCase();
  return async (req: Request, _res: Response, next: NextFunction): Promise<void> => {
    const { admins, logAdmins, benchAdmins, clawEvolveAdmins, clawInsightAdmins } = await resolveAdminSets(options);
    const candidates = collectIdentityCandidates(req);
    const isLocalDevAdmin = isLoopbackHost(req) && candidates.includes(devUserId);

    req.isAdmin = isLocalDevAdmin || candidates.some((id) => admins.has(id));
    // Role flags are cumulative: a generic admin also passes the narrower checks.
    req.isLogAdmin = req.isAdmin || candidates.some((id) => logAdmins.has(id));
    req.isBenchAdmin = req.isAdmin || candidates.some((id) => benchAdmins.has(id));
    req.isClawEvolveAdmin = req.isAdmin || candidates.some((id) => clawEvolveAdmins.has(id));
    // Monitoring access is an independent allowlist, not inherited from other roles.
    req.isClawInsightAdmin = candidates.some((id) => clawInsightAdmins.has(id));

    req.isSuperAdmin = options.repository
      ? await options.repository.hasRole(candidates, "admin")
      : candidates.some((id) => admins.has(id));
    next();
  };
}
