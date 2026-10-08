/**
 * Ed25519 signature verification middleware for internal API endpoints.
 * Configurable via EVOLVETRACE_INTERNAL_PUBLIC_KEY_B64.
 *
 * Security: when no public key is configured AND the environment is not a
 * local dev/development env, the middleware rejects every request with 403.
 * This prevents the /api/internal surface from becoming an unauthenticated
 * write path in production deployments that forget to configure signing.
 * In dev, the passthrough is preserved so the standalone server remains
 * usable without an external signer.
 */
import crypto from "node:crypto";
import type { Request, Response, NextFunction } from "express";
import { getCurrentEnv } from "../env.js";

const MAX_AGE_MS = 5 * 60 * 1000;

export type SignatureConfig = {
  publicKeyB64: string;
  maxAgeMs?: number;
};

export function signatureMiddleware(config?: Partial<SignatureConfig>) {
  const publicKeyB64 = config?.publicKeyB64 ?? process.env.EVOLVETRACE_INTERNAL_PUBLIC_KEY_B64;
  const maxAgeMs = config?.maxAgeMs ?? MAX_AGE_MS;

  if (!publicKeyB64) {
    const env = getCurrentEnv();
    const isDev = env === "dev" || env === "development";
    if (isDev) {
      // Dev fallback: allow unsigned internal calls for local development.
      return (_req: Request, _res: Response, next: NextFunction): void => {
        next();
      };
    }
    // Production: no signing key configured = block all internal requests.
    console.error("[evolvetrace] Internal API signature verification is not configured (EVOLVETRACE_INTERNAL_PUBLIC_KEY_B64 unset). All /api/internal requests will be rejected in non-dev environments.");
    return (_req: Request, res: Response, _next: NextFunction): void => {
      res.status(403).json({ error: "Forbidden", message: "Internal API signing is not configured" });
    };
  }

  let publicKey: crypto.KeyObject;
  try {
    publicKey = crypto.createPublicKey({
      key: Buffer.from(publicKeyB64, "base64"),
      type: "spki",
      format: "der",
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.error(`[evolvetrace] Invalid internal public key: ${msg}`);
    return (_req: Request, res: Response, _next: NextFunction): void => {
      res.status(500).json({ error: "Internal Server Error", message: "Signature verification misconfigured" });
    };
  }

  return (req: Request, res: Response, next: NextFunction): void => {
    const signature = req.headers["x-signature"] as string | undefined;
    const timestamp = req.headers["x-timestamp"] as string | undefined;

    if (!signature || !timestamp) {
      res.status(401).json({ error: "Unauthorized", message: "Missing X-Signature or X-Timestamp header" });
      return;
    }

    const ts = Number(timestamp);
    if (Number.isNaN(ts) || Math.abs(Date.now() - ts) > maxAgeMs) {
      res.status(401).json({ error: "Unauthorized", message: "Request timestamp expired" });
      return;
    }

    const body = typeof req.body === "object" && req.body !== null ? JSON.stringify(req.body) : "";
    const message = `${timestamp}.${body}`;

    try {
      const valid = crypto.verify(null, Buffer.from(message), publicKey, Buffer.from(signature, "base64"));
      if (!valid) {
        res.status(401).json({ error: "Unauthorized", message: "Invalid signature" });
        return;
      }
    } catch {
      res.status(401).json({ error: "Unauthorized", message: "Signature verification failed" });
      return;
    }

    next();
  };
}
