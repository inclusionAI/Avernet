import { timingSafeEqual } from "node:crypto";
import { Router, json, type Request } from "express";
import type { SessionRecoveryApi } from "../services/session-recovery/contracts.js";
import { SessionRecoveryError } from "../services/session-recovery/errors.js";
import { BotRuntimeError } from "@avernet/clawweb-shared/server/services/bot-runtime";
export type SessionRecoveryRuntime = {
  service: SessionRecoveryApi; triggerToken: string;
};
export function createSessionRecoveryRouter(supplied?: SessionRecoveryRuntime): Router {
  const router = Router();
  const runtime = () => {
    const value = supplied;
    if (!value) throw new SessionRecoveryError(503, "recovery_not_ready", "自愈发送服务未配置");
    return value;
  };
  router.post("/internal/monitoring/session-recoveries", json({ limit: "64kb", inflate: false }), async (req, res, next) => {
    try {
      const deps = runtime(), expected = Buffer.from(deps.triggerToken);
      const actual = Buffer.from(req.get("Authorization")?.replace(/^Bearer\s+/i, "") ?? "");
      if (!expected.length) throw new SessionRecoveryError(503, "recovery_not_ready", "自愈调用凭据未配置");
      if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) throw new SessionRecoveryError(401, "recovery_unauthorized", "自愈调用凭据无效");
      res.set("Cache-Control", "no-store");
      res.status(202).json(await deps.service.create(req.body, { userId: "session-recovery-service", isAdmin: true }));
    } catch (error) { next(error); }
  });
  router.use((error: unknown, req: Request, res: import("express").Response, next: import("express").NextFunction) => {
    if (!/^\/(?:internal\/)?monitoring\/session-recoveries(?:\/|$)/.test(req.path)) { next(error); return; }
    const failure = error instanceof SessionRecoveryError || error instanceof BotRuntimeError ? error
      : (error as { type?: string })?.type === "entity.too.large" ? new SessionRecoveryError(413, "invalid_session_recovery", "请求过大")
      : (error as { type?: string })?.type === "entity.parse.failed" ? new SessionRecoveryError(422, "invalid_session_recovery", "JSON 格式无效")
      : new SessionRecoveryError(503, "recovery_unavailable", "自愈发送服务暂不可用；发送结果可能未知");
    res.status(failure.status).json({ error: failure.code, message: failure.message });
  });
  return router;
}
