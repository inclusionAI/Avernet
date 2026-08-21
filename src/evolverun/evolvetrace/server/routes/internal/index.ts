/**
 * Internal API routes — aggregates core internal sub-routers for ClawMind.
 * Simplified for Evolvetrace: only runs, node-executions, events, facades,
 * bot-workflow-permissions are mounted.
 */
import { Router } from "express";
import type { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import type { NodeExecutionRepository } from "../../repositories/node-execution-repository.js";
import type { FlowEventRepository } from "../../repositories/event-repository.js";
import type { FacadeBindingRepository } from "../../repositories/facade-binding-repository.js";
import type { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import type { BotWorkflowPermissionRepository } from "../../repositories/bot-workflow-permission-repository.js";

import { createInternalRunsRouter } from "./runs.js";
import { createInternalNodeExecutionsRouter } from "./node-executions.js";
import { createInternalEventsRouter } from "./events.js";
import { createInternalFacadesRouter } from "./facades.js";
import { createInternalBotWorkflowPermissionsRouter } from "./bot-workflow-permissions.js";

export type InternalRepos = {
  flowRunRepo: FlowRunRepository | null;
  nodeExecRepo: NodeExecutionRepository | null;
  eventRepo: FlowEventRepository | null;
  facadeBindingRepo: FacadeBindingRepository | null;
  workflowSpecRepo: WorkflowSpecRepository | null;
  botWorkflowPermissionRepo: BotWorkflowPermissionRepository | null;
};

export function createInternalRouter(repos: InternalRepos): Router {
  const router = Router();

  router.use("/runs", createInternalRunsRouter(repos.flowRunRepo, repos.nodeExecRepo));
  router.use("/node-executions", createInternalNodeExecutionsRouter(repos.nodeExecRepo));
  router.use("/events", createInternalEventsRouter(repos.eventRepo));
  router.use("/facades", createInternalFacadesRouter(repos.facadeBindingRepo));
  router.use("/bot-workflow-permissions", createInternalBotWorkflowPermissionsRouter(repos.botWorkflowPermissionRepo));

  // Health check
  router.get("/health", (_req, res) => {
    res.json({ status: "ok", signer: "verified" });
  });

  return router;
}
