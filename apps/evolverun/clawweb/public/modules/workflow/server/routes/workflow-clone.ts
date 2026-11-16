import { Router, type Request, type Response } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { resolveWorkflowActorId } from "@avernet/clawweb-shared/server/services/workflow-access";
import type { WorkflowCloneService } from "../services/workflow-clone-service.js";
import { WorkflowCloneError } from "../services/workflow-clone-service.js";
import { workflowsCache } from "./workflows-list.js";

export function createWorkflowCloneRouter(service: WorkflowCloneService): Router {
  const router = Router();

  function writeError(response: Response, error: unknown): void {
    if (error instanceof WorkflowCloneError) {
      response.status(error.status).json({ error: "Workflow Clone Failed", code: error.code, message: error.message });
      return;
    }
    console.error("[workflow-clone] request failed:", error);
    response.status(500).json({ error: "Internal Server Error", code: "INTERNAL_ERROR" });
  }

  function requireActor(request: Request, response: Response): string | null {
    const actorId = resolveWorkflowActorId(request);
    if (!actorId) {
      response.status(401).json({ error: "Unauthorized", code: "IDENTITY_REQUIRED", message: "User identity required" });
      return null;
    }
    return actorId;
  }

  router.get("/clone-candidates", asyncHandler(async (request: Request, response: Response) => {
    if (!requireActor(request, response)) return;
    try {
      response.set("Cache-Control", "no-store");
      response.json({ data: await service.candidates() });
    } catch (error) {
      writeError(response, error);
    }
  }));

  router.post("/clone/preview", asyncHandler(async (request: Request, response: Response) => {
    if (!requireActor(request, response)) return;
    try {
      response.set("Cache-Control", "no-store");
      response.json(await service.preview(request.body?.sourceWorkflowId));
    } catch (error) {
      writeError(response, error);
    }
  }));

  router.post("/clone", asyncHandler(async (request: Request, response: Response) => {
    const actorId = requireActor(request, response);
    if (!actorId) return;
    try {
      const result = await service.clone(request.body ?? {}, actorId);
      workflowsCache.invalidate();
      response.status(201).json(result);
    } catch (error) {
      writeError(response, error);
    }
  }));

  return router;
}
