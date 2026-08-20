/**
 * Ed25519 signature verification middleware for internal API endpoints.
 * Configurable via EVOLVETRACE_INTERNAL_PUBLIC_KEY_B64.
 */
import crypto from "node:crypto";
import type { Request, Response, NextFunction } from "express";

const MAX_AGE_MS = 5 * 60 * 1000;

export type SignatureConfig = {
  publicKeyB64: string;
  maxAgeMs?: number;
};

export function signatureMiddleware(config?: Partial<SignatureConfig>) {
  const publicKeyB64 = config?.publicKeyB64 ?? process.env.EVOLVETRACE_INTERNAL_PUBLIC_KEY_B64;
  const maxAgeMs = config?.maxAgeMs ?? MAX_AGE_MS;

  if (!publicKeyB64) {
    return (_req: Request, _res: Response, next: NextFunction): void => {
      next();
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
