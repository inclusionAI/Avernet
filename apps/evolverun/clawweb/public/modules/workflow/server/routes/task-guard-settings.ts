import { Router, type Request, type Response } from "express";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import type { Request as AccessRequest } from 'express';
import type { WorkflowAutomationService } from '../contracts/workflow-automation.js';
import type { AutoAnalysisSettings } from "../services/task-guard/auto-analysis-settings.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { requireWorkflowAccess, resolveWorkflowActorId } from "@avernet/clawweb-shared/server/services/workflow-access";

export function createTaskGuardSettingsRouter(input: {
  settings: AutoAnalysisSettings;
  permissionRepo: BotWorkflowPermissionRepository | null;
  automation?: () => WorkflowAutomationService | null;
  authorize?: (request: AccessRequest, workflowId: string, mode: 'view' | 'edit') => Promise<{ actorId: string } | null>;
}): Router {
  const router = Router();
  async function access(req: Request, res: Response, workflowId: string, mode: 'view' | 'edit') {
    if (input.authorize) {
      const actor = await input.authorize(req, workflowId, mode);
      if (!actor) res.status(403).json({ error: '当前账号没有此工作流权限' });
      return actor;
    }
    if (!await requireWorkflowAccess(req, res, input.permissionRepo, workflowId, mode)) return null;
    return { actorId: resolveWorkflowActorId(req) ?? 'admin' };
  }

  router.get("/workflows/:workflowId/auto-analysis", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId ?? "").trim();
    if (!workflowId) { res.status(400).json({ error: "workflowId 为必填项" }); return; }
    if (!await access(req, res, workflowId, "view")) return;
    res.json(await input.settings.get(workflowId));
  }));

  router.put("/workflows/:workflowId/auto-analysis", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId ?? "").trim();
    if (!workflowId) { res.status(400).json({ error: "workflowId 为必填项" }); return; }
    if (typeof req.body?.enabled !== "boolean") {
      res.status(400).json({ error: "enabled 必须是 boolean" }); return;
    }
    const actor = await access(req, res, workflowId, "edit");
    if (!actor) return;
    try {
      const hasPolicy = req.body.autoDraft !== undefined || req.body.dailyTime !== undefined || req.body.expectedVersion !== undefined;
      res.json(hasPolicy ? await input.settings.save(workflowId, req.body, actor.actorId) : await input.settings.set(workflowId, req.body.enabled, actor.actorId));
    } catch (error) {
      const message = error instanceof Error ? error.message : '';
      if (message === 'SETTINGS_CONFLICT') { res.status(409).json({ error: '配置已变化，请刷新后保存' }); return; }
      if (message === 'INVALID_AUTOMATION_POLICY') { res.status(400).json({ error: '自动分析策略无效，请检查时间和版本' }); return; }
      throw error;
    }
  }));

  router.get('/workflows/:workflowId/automation-status', asyncHandler(async (req, res) => {
    const id = String(req.params.workflowId);
    if (!await access(req, res, id, 'view')) return;
    const automation = input.automation?.();
    if (!automation) { res.status(503).json({ error: '自动分析调度尚未接入，手动功能仍可使用' }); return; }
    res.json(await automation.status(id));
  }));
  router.post('/workflows/:workflowId/automation-retry', asyncHandler(async (req, res) => {
    const id = String(req.params.workflowId);
    if (!await access(req, res, id, 'edit')) return;
    const automation = input.automation?.();
    if (!automation) { res.status(503).json({ error: '自动分析调度尚未接入' }); return; }
    await automation.retryFailures(id);
    res.json(await automation.status(id));
  }));
  return router;
}
