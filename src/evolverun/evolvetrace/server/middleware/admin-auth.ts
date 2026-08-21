/**
 * Simplified auth middleware for Evolvetrace.
 * Identifies the caller via X-Evolvetrace-User-Id header or evolvetrace_user_id cookie.
 * Admin status is determined by ADMIN_USER_IDS env var.
 */
import type { Request, Response, NextFunction } from "express";

export type AdminConfig = {
  admins: Set<string>;
};

declare global {
  namespace Express {
    interface Request {
      userId?: string;
      isAdmin?: boolean;
      isLogAdmin?: boolean;
    }
  }
}

function getRequestCookie(req: Request, name: string): string | undefined {
  const cookies = req.cookies as Record<string, string> | undefined;
  if (cookies?.[name]) return cookies[name];
  const rawCookie = req.get("cookie") ?? "";
  for (const part of rawCookie.split(";")) {
    const [key, ...valueParts] = part.trim().split("=");
    if (key === name) return valueParts.join("=");
  }
  return undefined;
}

export function adminAuthMiddleware(config: AdminConfig) {
  const { admins } = config;
  return (req: Request, _res: Response, next: NextFunction): void => {
    const userId =
      (req.headers["x-evolvetrace-user-id"] as string | undefined)?.trim() ??
      req.cookies?.evolvetrace_user_id?.trim() ??
      getRequestCookie(req, "evolvetrace_user_id")?.trim();
    if (userId) {
      req.userId = userId;
      req.isAdmin = admins.has(userId);
      req.isLogAdmin = req.isAdmin;
    } else {
      // Simplified auth fallback: treat anonymous local requests as the default dev admin.
      // This keeps the standalone Evolvetrace usable without an external SSO provider.
      req.userId = "dev_local";
      req.isAdmin = true;
      req.isLogAdmin = true;
    }
    next();
  };
}
