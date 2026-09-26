/**
 * OpenClaw plugin entry point.
 *
 * This is the only module in the package that imports `openclaw/plugin-sdk` at
 * runtime. It re-exports the core plugin registration logic from `src/index.ts`
 * so that `src/index.ts` remains free of OpenClaw-specific runtime imports,
 * enabling the package to be consumed without installing `openclaw`.
 *
 * @module platform/openclaw-entry
 */

import { registerTaskguardPlugin } from "../index.js";

export default {
  id: "taskguard",
  name: "TaskGuard",
  description: "通用 YAML DAG 工作流引擎，基于 TaskFlow 持久化编排",
  register: registerTaskguardPlugin,
};
