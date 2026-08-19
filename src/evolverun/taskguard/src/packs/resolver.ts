import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { discoverWorkflowPacks } from "./discovery.js";
import { PACK_MANIFEST_FILENAME } from "./manifest.js";
import type { ResolvedWorkflow, ResolvedWorkflowPack, WorkflowPackCatalog, FailedWorkflow } from "./types.js";
import { normalizeWorkflowSpec, validateWorkflowSemantics } from "../validation/workflow.js";
import type { WorkflowSpec } from "../types.js";
import type { IWorkflowSpecRepository } from "../db/repositories/types.js";
import { resolveConfigPath, type YamlPacksConfig } from "../config/loader.js";

export class WorkflowPackResolverError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowPackResolverError";
  }
}

/**
 * Locate the workflow-packs directory adjacent to the deployed package root.
 *
 * In packed deployments (e.g. the TeClaw tgz extracted to /tmp/clawmind-mcp/package),
 * `dist/esm/platform/mcp-entry.js` and `packs/` sit side-by-side under the same package
 * root. The deployed code has no openclaw.json/plugins config pointing at itself, so
 * `defaultWorkspaceWorkflowsRoot()` would otherwise fall through to the empty
 * `~/.openclaw/workspace/workflows` and load zero packs — breaking every facade command.
 *
 * Walk up from this source file; the first ancestor whose `packs/` subdir actually
 * contains workflow packs (subdirs with a `workflow.pack.yaml`) is the package root.
 * The manifest check avoids false positives like `dist/esm/packs/`, which holds the
 * compiled JS modules of THIS package (resolver.js, discovery.js, ...) not workflow packs.
 * Returns undefined when no such ancestor exists.
 */
function discoverPacksAdjacentToEntry(): string | undefined {
  try {
    let dir = dirname(fileURLToPath(import.meta.url));
    for (let depth = 0; depth < 8 && dir && dir !== dirname(dir); depth++) {
      const candidate = join(dir, "packs");
      if (existsSync(candidate) && statSync(candidate).isDirectory() && hasWorkflowPacks(candidate)) {
        return candidate;
      }
      dir = dirname(dir);
    }
  } catch {
    // import.meta.url unavailable (e.g. non-ESM context) — fall through to other resolvers
  }
  return undefined;
}

/** True if `packsDir` contains at least one subdirectory with a pack manifest. */
function hasWorkflowPacks(packsDir: string): boolean {
  try {
    for (const entry of readdirSync(packsDir)) {
      const entryPath = join(packsDir, entry);
      if (statSync(entryPath).isDirectory() && existsSync(join(entryPath, PACK_MANIFEST_FILENAME))) {
        return true;
      }
    }
  } catch {
    // ignore unreadable dirs
  }
  return false;
}

export function defaultWorkspaceWorkflowsRoot(): string {
  // 主根(单一路径):供需要"写"语义的调用方(migration 写 yaml、pack 目录定位)使用。
  // 取所有候选根里的第一个(优先级最高)。发现/加载用 defaultWorkspaceWorkflowsRoots() 多根并集。
  return defaultWorkspaceWorkflowsRoots()[0];
}

/**
 * 所有默认 pack 搜索根(多根并集发现)。
 *
 * 优先级(返回数组,顺序即优先级;实际发现时跨根去重,同一 pack 经多根可达只加载一次):
 * 1. WORKFLOW_PACKS_ROOT 环境变量(支持冒号分隔多根,最显式控制)
 * 2. application.yaml packs.roots(通用)+ packs.perEngine[当前 engine](按引擎类型专有;engine 取 CLAWMIND_ENGINE env > yaml engine 字段)
 * 3. 各引擎 <engine-home>/workspace/clawmind/packs (统一 canonical; bot 工作区, 自动发现)
 * 4. packs/ 紧邻运行入口的 package 根(tgz 解压部署形态)
 * 5. openclaw.json plugins.load.paths / plugins.entries 的 installPath 下的 packs/
 * 6. ~/openclawExt/clawmind/packs(部署落盘的真实 pack 目录 —— 唯一权威源)
 * 7. ~/openclawExt/clawmind/packs(fallback,与优先级6一致,不再依赖 ~/.openclaw/workspace 软链挂载)
 *
 * 为什么多根:单根 + 软链接挂载在 discovery 的 isDirectory 判定上脆弱(软链接被滤),且 openclawExt
 * 下的真实 pack 目录本就是权威源。多根并集 + realpath 去重,让"真实目录"和"软链接挂载点"任一可达
 * 即可发现,互为冗余,不会因单点缺失而 0 pack(action 全 Unknown)。
 */
export function defaultWorkspaceWorkflowsRoots(): string[] {
  const roots: string[] = [];

  // 优先级1: WORKFLOW_PACKS_ROOT(支持冒号分隔多根)
  const envRoot = process.env.WORKFLOW_PACKS_ROOT?.trim();
  if (envRoot) {
    for (const r of envRoot.split(":")) {
      const t = r.trim();
      if (t) roots.push(t);
    }
  }

  // 优先级2: application.yaml packs.roots + packs.perEngine[当前 engine]
  // （配置文件入口;env 为显式覆盖兜底,故优先级更高。perEngine 只加载当前 engine 的条目。）
  try {
    const configPath = resolveConfigPath();
    if (configPath) {
      const raw = readFileSync(configPath, "utf-8");
      const cfg = parseYaml(raw) as { engine?: string; packs?: YamlPacksConfig } | null;

      // 2a. 通用 roots（所有引擎）
      const yamlRoots = cfg?.packs?.roots;
      if (Array.isArray(yamlRoots)) {
        for (const r of yamlRoots) {
          const t = typeof r === "string" ? r.trim() : "";
          if (t) roots.push(t);
        }
      }

      // 2b. perEngine：只取当前 engine（CLAWMIND_ENGINE env > yaml engine 字段）
      const perEngine = cfg?.packs?.perEngine;
      const engine =
        (process.env.CLAWMIND_ENGINE?.trim() || "") ||
        (typeof cfg?.engine === "string" ? cfg.engine.trim() : "");
      if (engine && perEngine) {
        const v = perEngine[engine];
        const arr = Array.isArray(v) ? v : typeof v === "string" ? [v] : [];
        for (const r of arr) {
          const t = typeof r === "string" ? r.trim() : "";
          if (t) roots.push(t);
        }
      }
    }
  } catch {
    // 忽略不可读/非法配置文件
  }

  // 优先级3: 各引擎 workspace packs (统一 canonical = <engine-home>/workspace/clawmind/packs)
  //   bot 工作区(bot 可写) + 共享存储(引擎可读) + 用户可看;探测各引擎 home,存在即并集。
  //   无需引擎类型判定;与 adjacent/openclawExt 经 realpath 去重,同一 pack 只加载一次。
  //   插在 adjacent 之前 -> workspace 成为 env/packs.roots 未设时的写根(roots[0])。
  const workspacePackRoots = [
    join(homedir(), ".teclaw", "workspace", "clawmind", "packs"),
    join(homedir(), ".claude_code", "workspace", "clawmind", "packs"),
    join(homedir(), ".openclaw", "workspace", "clawmind", "packs"),
    join(homedir(), ".hermes", "workspace", "clawmind", "packs"),
  ];
  for (const ws of workspacePackRoots) {
    if (existsSync(ws) && statSync(ws).isDirectory()) roots.push(ws);
  }

  // 优先级4: adjacent packs(tgz 部署)
  const adjacentPacks = discoverPacksAdjacentToEntry();
  if (adjacentPacks) roots.push(adjacentPacks);

  const openclawHome = process.env.OPENCLAW_HOME?.trim() || join(homedir(), ".openclaw");

  // 优先级5: openclaw.json plugins 推断
  try {
    const openclawJson = join(openclawHome, "openclaw.json");
    if (existsSync(openclawJson)) {
      const raw = JSON.parse(readFileSync(openclawJson, "utf-8"));
      const candidatePaths: string[] = [];

      const loadPaths: string[] = raw?.plugins?.load?.paths ?? [];
      candidatePaths.push(...loadPaths);

      const entries: Record<string, { installPath?: string; sourcePath?: string }> | undefined = raw?.plugins?.entries;
      if (entries) {
        for (const entry of Object.values(entries)) {
          const p = entry.installPath?.trim() || entry.sourcePath?.trim();
          if (p) candidatePaths.push(p);
        }
      }

      for (const p of candidatePaths) {
        const packsDir = join(p, "packs");
        if (existsSync(packsDir)) roots.push(packsDir);
      }
    }
  } catch {
    // ignore parse errors
  }

  // 优先级6: ~/openclawExt/clawmind/packs(部署真实 pack 目录)
  const openclawExtPacks = join(homedir(), "openclawExt", "clawmind", "packs");
  if (existsSync(openclawExtPacks)) roots.push(openclawExtPacks);

  // 优先级7: fallback ~/openclawExt/clawmind/packs(与优先级6一致,不再依赖 ~/.openclaw/workspace 软链挂载)
  const fallback = join(homedir(), "openclawExt", "clawmind", "packs");
  roots.push(fallback);

  // 去重(保留顺序),只保留存在的
  const unique: string[] = [];
  for (const r of roots) {
    if (unique.includes(r)) continue;
    if (existsSync(r)) unique.push(r);
  }
  // 兜底:若全部不存在,仍返回 fallback 路径(保持原行为 —— 调用方依赖拿到一个路径串)
  if (unique.length === 0) return [fallback];
  return unique;
}

export function loadWorkflowSpecFromFile(filepath: string): WorkflowSpec {
  const raw = parseYaml(readFileSync(filepath, "utf-8")) as unknown;
  const spec = normalizeWorkflowSpec(raw);
  validateWorkflowSemantics(spec);
  return spec;
}

export function resolvePackWorkflows(packs: ResolvedWorkflowPack[]): { workflows: ResolvedWorkflow[]; failedWorkflows: FailedWorkflow[] } {
  const resolved: ResolvedWorkflow[] = [];
  const failed: FailedWorkflow[] = [];
  for (const pack of packs) {
    for (const workflowRef of pack.workflows) {
      try {
        const spec = loadWorkflowSpecFromFile(workflowRef.absolutePath);
        if (spec.id !== workflowRef.id) {
          console.error(
            `[taskguard] Workflow id mismatch in pack "${pack.manifest.id}": manifest=${workflowRef.id}, yaml=${spec.id} (skipping)`,
          );
          failed.push({
            id: workflowRef.id,
            packId: pack.manifest.id,
            packVersion: pack.manifest.version,
            absolutePath: workflowRef.absolutePath,
            error: `id mismatch: manifest=${workflowRef.id}, yaml=${spec.id}`,
          });
          continue;
        }
        resolved.push({
          id: spec.id,
          spec,
          digest: workflowRef.digest,
          absolutePath: workflowRef.absolutePath,
          source: pack.source,
          pack: {
            id: pack.manifest.id,
            version: pack.manifest.version,
            root: pack.root,
            digest: pack.digest,
          },
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.error(
          `[taskguard] Failed to load workflow "${workflowRef.id}" from pack "${pack.manifest.id}" (${workflowRef.absolutePath}): ${message}`,
        );
        failed.push({
          id: workflowRef.id,
          packId: pack.manifest.id,
          packVersion: pack.manifest.version,
          absolutePath: workflowRef.absolutePath,
          error: message,
        });
      }
    }
  }
  return { workflows: resolved, failedWorkflows: failed };
}

export function loadWorkflowPackCatalog(
  workspaceWorkflowsRoot: string | string[] = defaultWorkspaceWorkflowsRoots(),
): WorkflowPackCatalog {
  const packs = discoverWorkflowPacks(workspaceWorkflowsRoot);
  const { workflows, failedWorkflows } = resolvePackWorkflows(packs);
  return {
    packs,
    workflows,
    failedWorkflows,
  };
}

export function resolveWorkflowByIdFromPacks(
  workflowId: string,
  workflows: ResolvedWorkflow[],
): ResolvedWorkflow | undefined {
  const matches = workflows.filter((workflow) => workflow.id === workflowId);
  if (matches.length > 1) {
    const sources = matches
      .map((workflow) => `${workflow.pack.id}@${workflow.pack.version} (${workflow.pack.root})`)
      .join(", ");
    throw new WorkflowPackResolverError(`Workflow "${workflowId}" is provided by multiple packs: ${sources}`);
  }
  return matches[0];
}

/**
 * Resolve a workflow by ID with DB-first strategy.
 * If a WorkflowSpecRepository is provided and has a row for the workflowId,
 * parses and validates the spec_json from the DB and returns a ResolvedWorkflow
 * with source.kind = "db". Otherwise falls back to Pack YAML resolution.
 *
 * When `debug` is true, skips DB/API lookup and resolves from Pack YAML only
 * (so local edits to YAML are picked up without deploy).
 * When DB spec validation fails in non-debug mode, the error is thrown — no fallback.
 */
export async function resolveWorkflow(
  workflowId: string,
  dbSpecRepo: IWorkflowSpecRepository | undefined,
  packWorkflows: ResolvedWorkflow[],
  debug?: boolean,
): Promise<ResolvedWorkflow | undefined> {
  if (!debug && dbSpecRepo) {
    const row = await dbSpecRepo.findByWorkflowId(workflowId);
    console.info("[taskguard] resolveWorkflow DB/API lookup result", {
      workflowId,
      hasRow: !!row,
      spec_json_type: row ? typeof row.spec_json : "no-row",
      spec_json_length: row?.spec_json ? String(row.spec_json).length : 0,
      spec_json_preview: row?.spec_json ? String(row.spec_json).substring(0, 200) : String(row?.spec_json),
      row_keys: row ? Object.keys(row) : [],
    });
    if (row) {
      if (!row.spec_json) {
        // API returned a row but spec_json is missing/falsy — likely an auth redirect or malformed response.
        // Fall through to Pack YAML resolution instead of crashing.
        console.warn("[taskguard] resolveWorkflow: row.spec_json is falsy, falling back to Pack YAML", { workflowId, spec_json: row.spec_json, row_keys: Object.keys(row) });
      } else {
        try {
          let raw = JSON.parse(row.spec_json) as unknown;
          // clawweb may store the spec as { content: "<yaml string>" } rather than parsed JSON.
          // If so, parse the YAML content first.
          if (raw && typeof raw === "object" && "content" in (raw as Record<string, unknown>) && !("nodes" in (raw as Record<string, unknown>))) {
            raw = parseYaml((raw as Record<string, unknown>).content as string) as unknown;
          }
          const spec = normalizeWorkflowSpec(raw);
          validateWorkflowSemantics(spec);
          return {
            id: spec.id,
            spec,
            digest: `db:${row.id}:${row.gmt_modified}`,
            absolutePath: "",
            source: { kind: "db" },
            pack: {
              id: row.pack_id ?? "",
              version: "",
              root: "",
              digest: "",
            },
          };
        } catch (dbErr) {
          // DB spec is corrupted (parse/normalize/validate failed) —
          // fall back to local Pack YAML instead of crashing the engine.
          console.warn(
            `[taskguard] resolveWorkflow: DB spec for "${workflowId}" is corrupted, falling back to Pack YAML: ${dbErr instanceof Error ? dbErr.message : dbErr}`,
          );
        }
      }
    }
  }

  return resolveWorkflowByIdFromPacks(workflowId, packWorkflows);
}

export function resolveWorkflowByIdAndPackId(
  workflowId: string,
  workflows: ResolvedWorkflow[],
  packId?: string,
): ResolvedWorkflow | undefined {
  if (!packId) {
    return resolveWorkflowByIdFromPacks(workflowId, workflows);
  }
  const match = workflows.find(
    (workflow) => workflow.id === workflowId && workflow.pack.id === packId,
  );
  return match;
}

export function buildSubworkflowResolver(
  catalog: WorkflowPackCatalog,
  currentPackId?: string,
): (workflowId: string, packId?: string) => WorkflowSpec | undefined {
  return (workflowId: string, packId?: string): WorkflowSpec | undefined => {
    const effectivePackId = packId ?? currentPackId;
    const resolved = resolveWorkflowByIdAndPackId(workflowId, catalog.workflows, effectivePackId);
    return resolved?.spec;
  };
}

export function requireWorkflowFromPacks(
  workflowId: string,
  workflows: ResolvedWorkflow[],
): ResolvedWorkflow {
  const resolved = resolveWorkflowByIdFromPacks(workflowId, workflows);
  if (resolved) return resolved;
  const available = listWorkflowIdsFromPacks(workflows).join(", ");
  throw new WorkflowPackResolverError(
    `Workflow "${workflowId}" pack 未安装或未被发现。Available: ${available || "none"}`,
  );
}

export function listWorkflowIdsFromPacks(workflows: ResolvedWorkflow[]): string[] {
  return Array.from(new Set(workflows.map((workflow) => workflow.id))).sort();
}

export function workflowRegistryFromResolved(workflows: ResolvedWorkflow[]): Record<string, WorkflowSpec> {
  const registry: Record<string, WorkflowSpec> = {};
  for (const workflow of workflows) {
    if (registry[workflow.id]) {
      throw new WorkflowPackResolverError(`Workflow "${workflow.id}" is duplicated in external packs`);
    }
    registry[workflow.id] = workflow.spec;
  }
  return registry;
}

/** Unify gmt_modified formats: MySQL TIMESTAMP string → epoch seconds; SQLite epoch integer → pass through. */
export function toEpoch(val: string | number): number {
  if (typeof val === "number") return val;
  return Math.floor(new Date(val).getTime() / 1000);
}
