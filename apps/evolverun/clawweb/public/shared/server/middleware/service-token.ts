/**
 * Static service-token auth middleware for service-to-service write endpoints
 * (e.g. POST /api/internal/bot-workflow-permissions/clone, called by backend).
 *
 * Pattern reference: bcsfuse's SimpleTokenAuthProvider (shared static Bearer
 * token), hardened with the "unconfigured = reject" rule from evolvetrace's
 * signature middleware:
 *
 * - Token source: env var `CLAWWEB_SERVICE_AUTH_TOKEN` (each deployment env
 *   supplies its own value; never commit a token to the repo).
 * - Accepted credentials: `Authorization: Bearer <token>` or `X-Service-Token`.
 * - Non-dev environment with NO token configured → every request is rejected
 *   with 403. A missing secret must fail closed, never open.
 * - Dev environment with no token → passthrough (local development only).
 * - Any verification failure collapses into the same 401 without details.
 * - Logs record only a token fingerprint (`sha256(token)[:16]`), never the
 *   token itself.
 */
import crypto from "node:crypto";
import type { NextFunction, Request, RequestHandler, Response } from "express";
import { getCurrentEnv } from "../env.js";

export type ServiceTokenOptions = {
  /** Explicit token; defaults to env `CLAWWEB_SERVICE_AUTH_TOKEN`. */
  token?: string;
  /** Env detection override; defaults to `getCurrentEnv()`. */
  isDev?: boolean;
};

export type ServiceTokenMiddleware = RequestHandler;

function tokenFingerprint(token: string): string {
  return crypto.createHash("sha256").update(token).digest("hex").slice(0, 16);
}

export function createServiceTokenMiddleware(options: ServiceTokenOptions = {}): ServiceTokenMiddleware {
  const token = (options.token ?? process.env.CLAWWEB_SERVICE_AUTH_TOKEN ?? "").trim();
  const isDev = options.isDev ?? getCurrentEnv() === "dev";

  if (!token) {
    if (isDev) {
      // Dev fallback: allow unsigned service calls for local development.
      return (_req: Request, _res: Response, next: NextFunction): void => {
        next();
      };
    }
    // Production: no token configured = block all service calls. Failing
    // closed here keeps an unauthenticated write path from appearing the
    // day someone forgets the env var in a deployment.
    console.error(
      "[service-token] CLAWWEB_SERVICE_AUTH_TOKEN is not configured; all service-token protected requests will be rejected in non-dev environments.",
    );
    return (_req: Request, res: Response, _next: NextFunction): void => {
      res.status(403).json({ error: "Forbidden", message: "Service token authentication is not configured" });
    };
  }

  if (token.length < 32) {
    console.warn(
      `[service-token] Configured token is shorter than 32 bytes (fp=${tokenFingerprint(token)}); rotate to a stronger secret.`,
    );
  }

  return (req: Request, res: Response, next: NextFunction): void => {
    const authHeader = (req.headers["authorization"] as string | undefined)?.trim() ?? "";
    const altHeader = (req.headers["x-service-token"] as string | undefined)?.trim() ?? "";

    const presented =
      authHeader.toLowerCase().startsWith("bearer ") ? authHeader.slice(7).trim() : altHeader;

    if (!presented) {
      // Missing credentials: same 401 as an invalid token — do not reveal
      // which check failed.
      res.status(401).json({ error: "Unauthorized", message: "Authentication required" });
      return;
    }

    if (presented !== token) {
      console.warn(
        `[service-token] Rejected credential (presented fp=${tokenFingerprint(presented)}).`,
      );
      res.status(401).json({ error: "Unauthorized", message: "Authentication required" });
      return;
    }

    next();
  };
}