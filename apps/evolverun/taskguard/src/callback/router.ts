/**
 * Express router for async-callback HTTP endpoints.
 *
 * Exposes `POST /api/callback/:token` as a public endpoint (no API key required,
 * registered before the API key middleware — same pattern as the approval callback route).
 *
 * The caller authenticates via HMAC-SHA256 signature or x-one-id header,
 * depending on the node's auth configuration.
 *
 * @module callback/router
 */

import type { Request, Response, Router } from "express";
import type { ControllerDeps } from "../controller.js";
import type { IDatabase } from "../db/types.js";
import type { AsyncCallbackConfig } from "../config/types.js";
import {
  createCallbackTokenRegistry,
  authenticateCallback,
  validateCallbackPayload,
  buildCallbackResult,
} from "./index.js";

// ── Types ──

export type CallbackRouterDeps = {
  controllerDeps: ControllerDeps;
  database: IDatabase;
  config: AsyncCallbackConfig;
};

// ── Token format validation ──

const TOKEN_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

// ── Router ──

export function createCallbackRouter(deps: CallbackRouterDeps): Router {
  const { Router: createRouter } = require("express") as { Router: () => Router };
  const router = createRouter();

  // Explicit import to avoid circular dependency at module load time
  // (controller imports from this module's parent directory indirectly).
  let handleAsyncCallbackFn: typeof import("../controller.js").handleAsyncCallback | null = null;

  async function getHandler() {
    if (!handleAsyncCallbackFn) {
      const mod = await import("../controller.js");
      handleAsyncCallbackFn = mod.handleAsyncCallback;
    }
    return handleAsyncCallbackFn;
  }

  /**
   * POST /api/callback/:token
   *
   * Public endpoint for external business systems to call back
   * an async-callback node with results.
   *
   * No API key required — auth is via HMAC signature or x-one-id header.
   */
  router.post("/:token", async (req: Request, res: Response) => {
    // Express 5 types req.params values as `string | string[]`; our route
    // pattern `/:token` always produces a string, so we cast here.
    const token = req.params.token as string;

    // Validate token format
    if (!token || !TOKEN_REGEX.test(token)) {
      res.status(400).json({ success: false, error: "Invalid token format" });
      return;
    }

    // Check if async-callback system is enabled
    if (!deps.config.enabled) {
      res.status(503).json({ success: false, error: "Async callback system is disabled" });
      return;
    }

    // Look up the token in the database
    const registry = createCallbackTokenRegistry(deps.database);
    const record = await registry.findByToken(token);

    if (!record) {
      res.status(404).json({ success: false, error: "Token not found" });
      return;
    }

    if (record.status !== "pending") {
      const statusLabel = record.status === "consumed" ? "已使用" : "已过期";
      res.status(409).json({ success: false, error: `Token ${statusLabel}`, status: record.status });
      return;
    }

    // Extract raw body and headers
    const rawBody = typeof req.body === "string" ? req.body : JSON.stringify(req.body);
    // Express headers can be `string | string[]`; cast to string since our
    // callback callers always send single-valued headers.
    const signatureHeader = typeof req.headers["x-signature-256"] === "string"
      ? req.headers["x-signature-256"] : undefined;
    const xOneIdHeader = typeof req.headers["x-one-id"] === "string"
      ? req.headers["x-one-id"] : undefined;
    const clientIp = (typeof req.headers["x-forwarded-for"] === "string"
      ? req.headers["x-forwarded-for"]
      : typeof req.headers["x-real-ip"] === "string"
        ? req.headers["x-real-ip"]
        : req.ip) ?? "";

    // Authenticate the request
    const authResult = authenticateCallback({
      auth: undefined, // Node-level auth is stored on the node, not the token.
                       // For now, we use the global config default.
      defaultHmacSecret: deps.config.defaultHmacSecret,
      rawBody,
      signatureHeader,
      xOneIdHeader,
    });

    if (!authResult.authenticated) {
      res.status(403).json({ success: false, error: "reason" in authResult ? authResult.reason : "Authentication failed" });
      return;
    }

    // Validate the payload
    const payloadResult = validateCallbackPayload({
      rawBody,
      maxPayloadKb: deps.config.maxCallbackPayloadKb,
    });

    if (!payloadResult.valid) {
      res.status(400).json({ success: false, error: "reason" in payloadResult ? payloadResult.reason : "Invalid payload" });
      return;
    }

    // Build the callback result (strips `status` meta field)
    const { status: callbackStatus, result: nodeResult } = buildCallbackResult(payloadResult.data);

    // Consume the token (single-use, CAS on status='pending')
    const consumed = await registry.consume(token, nodeResult, {
      headers: JSON.stringify(req.headers),
      ip: clientIp,
      userId: authResult.userId,
    });

    if (!consumed) {
      // Token was consumed between our check and now (race condition)
      res.status(409).json({ success: false, error: "Token already consumed" });
      return;
    }

    // Delegate to the controller to resume the workflow
    try {
      const handler = await getHandler();
      const message = await handler(
        deps.controllerDeps,
        record.flowId,
        record.nodeId,
        token,
        { ...nodeResult, status: callbackStatus },
        authResult.userId,
      );

      res.status(200).json({
        success: true,
        message,
        flowId: record.flowId,
        nodeId: record.nodeId,
        status: callbackStatus,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      console.error(`[callback] Error resuming flow for token ${token}:`, message);

      // Try to restore the token to pending so the caller can retry
      // (The token was already consumed, but the controller failed.
      //  We cannot un-consume, so report the error.)
      res.status(500).json({
        success: false,
        error: `Callback processed but flow resume failed: ${message}`,
        flowId: record.flowId,
        nodeId: record.nodeId,
      });
    }
  });

  // Reject all other methods
  router.all("/:token", (req: Request, res: Response) => {
    res.status(405).json({ success: false, error: "Method not allowed" });
  });

  return router;
}