import { Router, type Request, type ErrorRequestHandler } from "express";
import multer from "multer";
import { readFile, rm } from "node:fs/promises";
import { createConfiguredTemporaryDirectory, TemporaryDirectoryError } from "@avernet/clawweb-shared/server/env";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { EvolveRepository, EvolveTaskRow } from "../repositories/evolve-repository.js";
import { MATERIAL_LIMITS, ReferenceMaterials } from "../services/evolve/reference-materials.js";

export function createReferenceMaterialsRouter(input: {
  repo: EvolveRepository | null;
  materials: ReferenceMaterials;
  actor: (req: Request) => string | null;
  canRead: (req: Request, task: EvolveTaskRow) => Promise<boolean>;
}) {
  const router = Router();
  router.get("/reference-materials/options", (_req, res) => {
    res.json({ available: input.materials.available, ...MATERIAL_LIMITS });
  });
  router.post("/reference-materials/uploads", asyncHandler(async (req, res, next) => {
    const actor = input.actor(req);
    if (!actor) { res.status(401).json({ error: "请先登录再上传参考材料" }); return; }
    if (!input.materials.available) { res.status(503).json({ error: "参考材料存储不可用" }); return; }
    const directory = await createConfiguredTemporaryDirectory("clawevolve-materials");
    // Disk storage bounds a single request without retaining the multipart body
    // in memory. The ZIP content is independently validated by the service.
    const upload = multer({ dest: directory, limits: {
      files: 1, fileSize: MATERIAL_LIMITS.maxArchiveBytes, fields: 0, parts: 2,
    } }).single("package");
    try {
      await new Promise<void>((resolve, reject) => upload(req, res, error => error ? reject(error) : resolve()));
      if (!req.file) { res.status(400).json({ error: "请选择参考材料" }); return; }
      res.status(201).json(await input.materials.upload(actor, await readFile(req.file.path)));
    } catch (error) { next(error); }
    finally { await rm(directory, { recursive: true, force: true }); }
  }));
  router.get("/tasks/:taskId/reference-materials", asyncHandler(async (req, res) => {
    const task = await input.repo?.findTask(String(req.params.taskId));
    if (!task) { res.status(404).json({ error: "任务不存在" }); return; }
    if (!await input.canRead(req, task)) { res.status(403).json({ error: "没有访问任务材料的权限" }); return; }
    const value = JSON.parse(task.config_json).reference_materials;
    if (!value) { res.status(404).json({ error: "任务没有参考材料" }); return; }
    res.json(await input.materials.view(value, task.task_id));
  }));
  const errors: ErrorRequestHandler = (error, _req, res, next) => {
    if (res.headersSent) { next(error); return; }
    const status = error instanceof multer.MulterError ? 413 : Number(error?.status) || 500;
    const message = status === 413
      ? "材料包超过上传限制"
      : error instanceof TemporaryDirectoryError
        ? error.message
        : error instanceof Error
          ? error.message
          : "材料处理失败";
    res.status(status).json({ error: message });
  };
  router.use(errors);
  return router;
}
