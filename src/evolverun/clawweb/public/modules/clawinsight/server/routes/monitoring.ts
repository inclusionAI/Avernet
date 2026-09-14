import { randomUUID } from "node:crypto";
import { json, Router, type ErrorRequestHandler, type Request } from "express";
import { MonitoringError } from "../services/monitoring/contracts.js";
import { createMonitoringRuntime, type MonitoringRuntime } from "../services/monitoring/monitoring-runtime.js";

const MAX_BYTES = 128 * 1024;
const scoped = (req: Request) => /^\/(?:internal\/monitoring|monitoring)(?:\/|$)/.test(req.path);
const statusCodes = { INVALID_EVENT: 400, BOT_NOT_ALLOWED: 403,
  EVENT_CONFLICT: 409, PAYLOAD_TOO_LARGE: 413, NOT_READY: 503, BOT_NOT_FOUND: 404 } as const;
export const monitoringErrorResponse: ErrorRequestHandler = (error, req, res, next) => {
  if (!scoped(req)) { next(error); return; }
  const parser = error as { type?: string };
  const failure = error instanceof MonitoringError ? error : parser.type === "entity.too.large"
    ? new MonitoringError("PAYLOAD_TOO_LARGE", "上报内容超过 128 KiB。")
    : ["entity.parse.failed", "encoding.unsupported", "charset.unsupported", "request.size.invalid"].includes(parser.type ?? "")
      ? new MonitoringError("INVALID_EVENT", "JSON 格式或编码无效。")
      : new MonitoringError("NOT_READY", "监控服务暂不可用。");
  res.set("Cache-Control", "no-store");
  res.status(statusCodes[failure.code]).json({ error: {
    code: `MONITORING_${failure.code}`, message: failure.message, requestId: randomUUID(),
  } });
};

/** Mounted inside the existing Insight router, before governance routes and error handling. */
export function createMonitoringRouter(runtime: MonitoringRuntime = createMonitoringRuntime()): Router {
  const router = Router();
  router.use((req, res, next) => {
    if (!scoped(req)) { next("router"); return; }
    res.set("Cache-Control", "no-store");
    if (!runtime.service) { next(new MonitoringError("NOT_READY", "监控模块未启用或配置无效。")); return; }
    next();
  });
  const parseJson = json({ limit: MAX_BYTES, inflate: false });
  router.use("/internal/monitoring", (req, res, next) => {
    try {
      // Reporting relies on deployment-side internal network access restrictions.
      // No application-level reporting token is required by draft.3.
      if (req.method !== "POST") { next(); return; }
      if (!req.is("application/json")) throw new MonitoringError("INVALID_EVENT", "仅接受 application/json。");
      const encoding = req.get("content-encoding");
      if (encoding && encoding.toLowerCase() !== "identity") throw new MonitoringError("INVALID_EVENT", "请上报未压缩的 JSON。");
      const length = req.get("content-length");
      if (length !== undefined && Number(length) > MAX_BYTES) throw new MonitoringError("PAYLOAD_TOO_LARGE", "上报内容超过 128 KiB。");
      if (req.body !== undefined) {
        // Existing internal Host parses JSON first. For identity Content-Length requests the original
        // byte count remains verifiable. Never pretend reserializing an already-parsed body measures it.
        if (!length || !/^\d+$/.test(length) || req.get("transfer-encoding")) {
          throw new MonitoringError("NOT_READY", "现有 Host 已消费请求体；请使用带 Content-Length 的非压缩 JSON 上报。");
        }
        next();
      } else parseJson(req, res, next);
    } catch (error) { next(error); }
  });
  router.post("/internal/monitoring/diagnosis-events", async (req, res) => {
    const ack = await runtime.service!.reportDiagnosis(req.body, req.get("Idempotency-Key"));
    res.status(ack.duplicate ? 200 : 201).json(ack);
  });
  router.post("/internal/monitoring/bot-checks", async (req, res) => {
    res.json(await runtime.service!.reportCheck(req.body));
  });
  router.get("/monitoring/bots", (req, res) => {
    if (Object.keys(req.query).length) throw new MonitoringError("INVALID_EVENT", "不支持此查询参数。");
    res.json(runtime.service!.bots());
  });
  router.get("/monitoring/bots/:botId/status", async (req, res) => {
    if (Object.keys(req.query).length) throw new MonitoringError("INVALID_EVENT", "不支持此查询参数。");
    res.json(await runtime.service!.status(String(req.params.botId)));
  });
  router.get("/monitoring/bots/:botId/diagnoses", async (req, res) => {
    res.json(await runtime.service!.diagnoses(String(req.params.botId), req.query));
  });
  router.use(monitoringErrorResponse);
  return router;
}
