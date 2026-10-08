import { existsSync } from "node:fs";
import { join } from "node:path";
import { defaultWorkspaceWorkflowsRoot, defaultWorkspaceWorkflowsRoots } from "./resolver.js";
import { PACK_MANIFEST_FILENAME } from "./manifest.js";

/**
 * 由 packId 解析 pack 根目录(canonical)。
 *
 * 取代旧的 `~/.openclaw/workspace/workflows/<packId>` 软链挂载路径(该路径在
 * 86d590d canonical 改造后已废弃)。遍历 canonical 多根,返回第一个真实存在
 * manifest 的目录;兜底返回 canonical 写入根下(供 DB 来源 workflow 的 cwd 兜底,
 * 解 spawn ENOENT / 找不到 pack 脚本)。
 */
export function resolvePackRootFromId(packId: string): string | undefined {
  for (const root of defaultWorkspaceWorkflowsRoots()) {
    const candidate = join(root, packId);
    if (existsSync(join(candidate, PACK_MANIFEST_FILENAME))) return candidate;
  }
  return join(defaultWorkspaceWorkflowsRoot(), packId);
}
