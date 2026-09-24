import { Router, type Request } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { RequestIdentity } from "../contracts/request-identity.js";
import type { SpaceDirectory } from "../contracts/space-directory.js";

export function spaceRequestIdentity(req: Request): RequestIdentity | null {
  const userId = String(req.header("X-User-Id") ?? "").trim();
  return userId ? { userId, authorization: req.header("Authorization"), cookie: req.header("Cookie") } : null;
}

export function createEvolveSpacesRouter(port?: SpaceDirectory): Router {
  const router = Router();
  router.get("/spaces", asyncHandler(async (req, res) => {
    const identity = spaceRequestIdentity(req);
    if (!identity) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    if (!port) { res.status(503).json({ error: "宿主空间服务未配置" }); return; }
    res.json({ items: await port.listAccessibleSpaces({ identity }) });
  }));
  return router;
}
