/**
 * Express router for webhook receive endpoint.
 *
 * POST /api/webhooks/:triggerId — receives webhook requests,
 * validates them, and triggers the associated workflow.
 */
import express, { type Request, type Response, type NextFunction } from "express";
import crypto from "node:crypto";
import type { WebhookConfig } from "../config/types.js";
import type { WebhookTriggerRepository } from "../db/repositories/webhook-trigger-repository.js";
import type { WebhookEventRepository } from "../db/repositories/webhook-event-repository.js";
import type { WorkflowLauncher } from "./trigger-adapter.js";
import { HttpTriggerAdapter } from "./trigger-adapter.js";
import { verifySignature } from "./signature-validator.js";
import { isIpAllowed, extractClientIp } from "./ip-whitelist.js";
import { mapPayload } from "./payload-mapper.js";
import { WebhookEventRepository as EventRepo } from "../db/repositories/webhook-event-repository.js";

const TRIGGER_ID_REGEX = /^[a-zA-Z0-9_-]{1,64}$/;

/** Coerce an unknown body value into a valid InjectLevel, or undefined. */
function normalizeInjectLevel(value: unknown): import("../inject-level.js").InjectLevel | undefined {
  if (value === "perf" || value === "simple" || value === "full") return value;
  return undefined;
}

export type WebhookRouterDeps = {
  config: WebhookConfig;
  triggerStore: WebhookTriggerRepository;
  eventStore: WebhookEventRepository;
  launchWorkflow: WorkflowLauncher;
};

export function createWebhookRouter(deps: WebhookRouterDeps): express.Router {
  const router = express.Router();

  // Raw body capture for signature verification (must be before json parsing for this route)
  router.use((req: Request, _res: Response, next: NextFunction) => {
    // Body is already parsed by the main app's express.json() middleware.
    // We'll re-serialize for signature verification (this is standard practice).
    next();
  });

  // POST /api/webhooks/:triggerId — receive webhook
  router.post("/:triggerId", async (req: Request, res: Response) => {
    const triggerId = req.params.triggerId as string;
    const method = req.method;

    // Validate triggerId format
    if (!TRIGGER_ID_REGEX.test(triggerId)) {
      res.status(400).json({ error: "Invalid trigger ID format" });
      return;
    }

    // Get raw body for signature verification
    const rawBody = JSON.stringify(req.body);
    const headers: Record<string, string> = {};
    for (const [k, v] of Object.entries(req.headers)) {
      headers[k.toLowerCase()] = Array.isArray(v) ? v.join(", ") : (v ?? "");
    }

    // Extract client IP
    const clientIp = extractClientIp(headers, req.socket.remoteAddress);

    try {
      // Look up trigger configuration
      const trigger = await deps.triggerStore.getByTriggerId(triggerId);
      if (!trigger) {
        res.status(404).json({ error: "Trigger not found" });
        return;
      }

      if (trigger.enabled !== 1) {
        res.status(404).json({ error: "Trigger is disabled" });
        return;
      }

      // Step 1: IP whitelist check
      const allowedIps = trigger.allowed_ips ? JSON.parse(trigger.allowed_ips) : [];
      if (allowedIps.length > 0 && !isIpAllowed(clientIp, allowedIps)) {
        await logEvent(deps, triggerId, "rejected", method, headers, rawBody, 403, `IP ${clientIp} not allowed`, clientIp);
        res.status(403).json({ error: "Forbidden", message: "IP not allowed" });
        return;
      }

      // Step 2: Signature validation
      const signatureHeader = headers["x-signature-256"];
      if (trigger.secret && !verifySignature(rawBody, trigger.secret, signatureHeader)) {
        await logEvent(deps, triggerId, "rejected", method, headers, rawBody, 401, "Invalid or missing signature", clientIp);
        res.status(401).json({ error: "Unauthorized", message: !signatureHeader ? "Missing signature header" : "Invalid signature" });
        return;
      }

      // Step 3: Idempotency check
      const requestId = headers["x-request-id"];
      if (requestId && deps.config.idempotencyWindowHours > 0) {
        const duplicate = await deps.eventStore.findDuplicate(requestId, deps.config.idempotencyWindowHours);
        if (duplicate) {
          res.status(200).json({ status: "duplicated", message: "Request already processed", flowId: duplicate.flow_id });
          return;
        }
      }

      // Step 4: Payload mapping
      const body = req.body as Record<string, unknown>;
      const payloadMapping = trigger.payload_mapping ? JSON.parse(trigger.payload_mapping) : {};
      const params = mapPayload(payloadMapping, body, headers);
      params.triggerSource = "webhook";
      params.triggerId = triggerId;

      // Optional per-trigger chatInject level override (top-level body field).
      const triggerChatInjectLevel = normalizeInjectLevel(body.chatInjectLevel);

      // Step 5: Launch workflow
      let flowId: string | null = null;
      let errorMessage: string | undefined;
      let statusCode = 202;
      let eventStatus = "accepted";

      try {
        flowId = await deps.launchWorkflow({
          workflowId: trigger.workflow_id,
          packId: trigger.pack_id ?? undefined,
          params,
          executionMode: "private",
          chatInjectLevel: triggerChatInjectLevel,
        });

        if (!flowId) {
          statusCode = 500;
          eventStatus = "error";
          errorMessage = "Workflow launch returned no flow ID";
        }
      } catch (error) {
        statusCode = 500;
        eventStatus = "error";
        errorMessage = error instanceof Error ? error.message : String(error);
      }

      // Step 6: Log event (best-effort)
      await logEvent(
        deps, triggerId, eventStatus, method, headers, rawBody,
        statusCode, errorMessage, clientIp, requestId, flowId,
      );

      if (eventStatus === "error") {
        res.status(statusCode).json({ error: "Workflow launch failed", message: errorMessage });
        return;
      }

      res.status(202).json({ status: "accepted", flowId, triggerId });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[webhook] Error processing trigger ${triggerId}: ${msg}`);
      res.status(500).json({ error: "Internal server error" });
    }
  });

  // Reject all other methods
  router.all("/:triggerId", (_req: Request, res: Response) => {
    res.status(405).json({ error: "Method not allowed", message: "Use POST to trigger webhooks" });
  });

  return router;
}

async function logEvent(
  deps: WebhookRouterDeps,
  triggerId: string,
  status: string,
  method: string,
  headers: Record<string, string>,
  rawBody: string,
  responseCode: number,
  errorMessage: string | null,
  ipAddress: string,
  eventId?: string,
  flowId?: string | null,
): Promise<void> {
  try {
    // Redact sensitive headers for logging
    const redacted = { ...headers };
    if (redacted["x-signature-256"]) redacted["x-signature-256"] = "[REDACTED]";
    if (redacted["x-api-key"]) redacted["x-api-key"] = "[REDACTED]";
    if (redacted["authorization"]) redacted["authorization"] = "[REDACTED]";

    const requestId = eventId ?? headers["x-request-id"] ?? crypto.randomUUID();
    await deps.eventStore.record({
      eventId: requestId,
      triggerId,
      flowId: flowId ?? null,
      status,
      requestMethod: method,
      requestHeaders: redacted,
      requestBodyHash: EventRepo.hashBody(rawBody),
      responseCode,
      errorMessage,
      ipAddress,
    });
  } catch {
    // Best-effort: don't block the response on event logging failure
  }
}