import { existsSync, readdirSync, realpathSync, statSync } from "node:fs";
import { join } from "node:path";
import { digestFile, digestPackDirectory } from "./digest.js";
import { PACK_MANIFEST_FILENAME, readPackManifest } from "./manifest.js";
import type { ResolvedWorkflowPack, ResolvedWorkflowPackAction, WorkflowSourceKind } from "./types.js";

export class WorkflowPackDiscoveryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowPackDiscoveryError";
  }
}

export type DiscoverWorkflowPacksOptions = {
  sourceKind?: WorkflowSourceKind;
};

/**
 * 列出某个 pack 根目录下的候选 pack 目录。
 *
 * 关键:用 statSync(entryPath).isDirectory()(跟随软链接)而非 dirent.isDirectory()。
 * readdirSync 的 Dirent.isDirectory() 对软链接条目按链接本身类型判定,返回 false ——
 * 所以 `~/.openclaw/workspace/workflows/<packId>` 这种指向真实 pack 目录的软链接会被
 * 滤掉,pack 发现 0 个,action 全 Unknown。statSync 跟随链接判定真实目标类型,
 * 软链接挂载的 pack 即可被发现。断链(目标不存在)时 statSync 抛错,catch 跳过。
 */
function candidatePackRoots(workspaceWorkflowsRoot: string): string[] {
  if (!existsSync(workspaceWorkflowsRoot)) return [];

  const roots: string[] = [];
  for (const entry of readdirSync(workspaceWorkflowsRoot, { withFileTypes: true })) {
    const entryPath = join(workspaceWorkflowsRoot, entry.name);
    let isDir: boolean;
    try {
      isDir = statSync(entryPath).isDirectory();
    } catch {
      // 断链或不可读 —— 跳过
      continue;
    }
    if (!isDir) continue;
    if (!existsSync(join(entryPath, PACK_MANIFEST_FILENAME))) continue;
    roots.push(entryPath);
  }
  return roots.sort();
}

/** 规范化一个路径为唯一键(realpath,跟随软链接到真实绝对路径),断链返回原路径。 */
function packRootKey(packRoot: string): string {
  try {
    return realpathSync(packRoot);
  } catch {
    return packRoot;
  }
}

export function resolveWorkflowPack(
  packRoot: string,
  sourceKind: WorkflowSourceKind = "workspace-pack",
): ResolvedWorkflowPack {
  const manifest = readPackManifest(packRoot);
  const workflows = manifest.workflows.map((workflow) => {
    const absolutePath = join(packRoot, workflow.file);
    if (!existsSync(absolutePath)) {
      throw new WorkflowPackDiscoveryError(`workflow file not found: ${workflow.file}`);
    }
    return {
      ...workflow,
      absolutePath,
      digest: digestFile(absolutePath),
    };
  });
  const actions = manifest.actions?.map((action): ResolvedWorkflowPackAction => {
    const absoluteRoot = join(packRoot, action.root);
    if (!existsSync(absoluteRoot)) {
      throw new WorkflowPackDiscoveryError(`action root not found: ${action.root}`);
    }

    const commands = action.commands
      ? Object.fromEntries(Object.entries(action.commands).map(([actionName, script]) => {
          const absolutePath = join(absoluteRoot, script);
          if (!existsSync(absolutePath)) {
            throw new WorkflowPackDiscoveryError(`action command script not found: ${actionName} -> ${action.root}/${script}`);
          }
          return [actionName, { actionName, script, absolutePath }];
        }))
      : undefined;

    return {
      ...action,
      absoluteRoot,
      commands,
    };
  });

  return {
    manifest,
    root: packRoot,
    digest: digestPackDirectory(packRoot),
    source: {
      kind: sourceKind,
      root: packRoot,
    },
    workflows,
    actions,
  };
}

export function discoverWorkflowPacks(
  workspaceWorkflowsRoot: string | string[],
  options: DiscoverWorkflowPacksOptions = {},
): ResolvedWorkflowPack[] {
  const sourceKind = options.sourceKind ?? "workspace-pack";
  const roots = Array.isArray(workspaceWorkflowsRoot) ? workspaceWorkflowsRoot : [workspaceWorkflowsRoot];
  const packs: ResolvedWorkflowPack[] = [];
  const seen = new Set<string>(); // realpath 去重:同一 pack 通过软链接+真实目录可达时只加载一次

  for (const root of roots) {
    for (const packRoot of candidatePackRoots(root)) {
      const key = packRootKey(packRoot);
      if (seen.has(key)) continue;
      try {
        packs.push(resolveWorkflowPack(packRoot, sourceKind));
        seen.add(key);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.error(`[taskguard] Failed to load workflow pack at "${packRoot}": ${message}`);
      }
    }
  }

  return packs;
}
