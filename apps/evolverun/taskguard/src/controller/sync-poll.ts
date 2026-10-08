/**
 * Detect changes made via ClawWeb (bypassing dispatch).
 * Compares DB spec content with local YAML content.
 * Also detects deletions: workflow exists locally but not in DB.
 *
 * IMPORTANT: sync-poll is a lightweight background task.
 * It writes YAML files only — NO git operations.
 * Git commit/push happens at deploy time, not during sync-poll.
 *
 * Returns:
 *  - changed: workflowIds that need to be synced (new or modified in DB)
 *  - deleted: workflowIds that were deleted from DB and need local cleanup
 */
import { readFileSync, statSync } from "node:fs";
import { readFile, writeFile, mkdir, unlink, rm, readdir } from "node:fs/promises";
import { join, dirname } from "node:path";

export type SyncPollDeps = {
  clawWebBaseUrl: string;
  botId?: string;
  ownerId?: string;
  resolvedWorkflows: unknown[];
  resolvedPacks: unknown[];
  packsRoot: string;
};

export type SyncPollResult = {
  /** WorkflowIds that need to be synced (new or modified in DB). */
  changed: string[];
  /** WorkflowIds that were deleted from DB and need local cleanup. */
  deleted: Array<{ workflowId: string; packId: string }>;
};

/** Find packId for a local workflow. */
function findLocalPackId(deps: SyncPollDeps, workflowId: string): string {
  for (const pack of (deps.resolvedPacks ?? [])) {
    const workflows: any[] = (pack as any).workflows ?? [];
    if (workflows.some((w: any) => w.id === workflowId)) {
      return (pack as any).id ?? (pack as any).manifest?.id ?? workflowId;
    }
  }
  return workflowId;
}

/** Find absolute path for a local workflow YAML. */
function findLocalAbsPath(deps: SyncPollDeps, workflowId: string): string | undefined {
  for (const wf of (deps.resolvedWorkflows ?? [])) {
    if ((wf as any).id === workflowId) {
      return (wf as any).absolutePath;
    }
  }
  return undefined;
}

/** Get local workflow YAML file's mtime in epoch ms. Returns 0 if not found. */
function getLocalMtime(deps: SyncPollDeps, workflowId: string): number {
  const absPath = findLocalAbsPath(deps, workflowId);
  if (!absPath) return 0;
  try {
    return statSync(absPath).mtimeMs;
  } catch {
    return 0;
  }
}

/**
 * Parse a DB timestamp (epoch ms, epoch seconds, MySQL DATETIME, or ISO string)
 * into epoch milliseconds. Returns 0 if unparseable.
 * Same logic as version-commands.ts parseDbTimestamp.
 */
function parseDbTimestampSafe(value: unknown): number {
  if (value == null) return 0;
  if (typeof value === "number") {
    const ms = value > 1e12 ? value : value * 1000;
    const year = new Date(ms).getFullYear();
    return (year >= 2000 && year <= 2100) ? ms : 0;
  }
  const s = String(value).trim();
  if (!s) return 0;
  const isoLike = s.includes("T") ? s : s.replace(" ", "T") + "Z";
  const ms = new Date(isoLike).getTime();
  if (isNaN(ms)) return 0;
  const year = new Date(ms).getFullYear();
  return (year >= 2000 && year <= 2100) ? ms : 0;
}

/** Format epoch ms to local time string with timezone. */
function formatLocalTime(ms: number): string {
  if (ms <= 0) return "";
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  const offsetMin = -d.getTimezoneOffset();
  const sign = offsetMin >= 0 ? "+" : "-";
  const absOffset = Math.abs(offsetMin);
  const tz = `${sign}${pad(Math.floor(absOffset / 60))}:${pad(absOffset % 60)}`;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())} ${tz}`;
}

/**
 * Normalize a workflow spec for content comparison.
 * Strips version/facade (they differ between DB and local) and sorts keys.
 */
function normalizeSpecForCompare(spec: Record<string, unknown>): string {
  const clone = { ...spec };
  delete (clone as any).version;
  delete (clone as any).facade;
  delete (clone as any).updatedAt;
  return JSON.stringify(clone, Object.keys(clone).sort());
}

/**
 * Read and parse a local workflow YAML file.
 * Returns undefined on failure.
 */
function readLocalYamlSpec(absPath: string): Record<string, unknown> | undefined {
  try {
    const content = readFileSync(absPath, "utf-8");
    // Parse YAML without depending on import type — use dynamic require workaround
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const yaml = require("js-yaml");
    return yaml.load(content) as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

/**
 * Detect changes by comparing DB spec content vs local YAML content.
 * NO git operations — purely YAML content comparison + API fetch.
 */
export async function detectClawWebChanges(deps: SyncPollDeps): Promise<SyncPollResult> {
  const result: SyncPollResult = { changed: [], deleted: [] };

  // Build local workflow ID set + spec map
  const localWorkflowIds = new Set<string>();
  const localSpecMap = new Map<string, Record<string, unknown>>();
  // Also track workflows that have load errors — don't try to auto-sync them
  const loadErrorWorkflowIds = new Set<string>();
  for (const wf of (deps.resolvedWorkflows ?? [])) {
    const id = (wf as any).id;
    if ((wf as any).loadError) {
      loadErrorWorkflowIds.add(id);
      continue;
    }
    localWorkflowIds.add(id);
    const absPath = (wf as any).absolutePath;
    if (absPath) {
      const spec = readLocalYamlSpec(absPath);
      if (spec) localSpecMap.set(id, spec);
    }
  }

  try {
    // Fetch all accessible workflows from ClawWeb (lightweight — just IDs + metadata)
    const resp = await fetch(
      `${deps.clawWebBaseUrl}/api/workflows?botOwnerId=${deps.ownerId ?? ""}${deps.botId ? `&botId=${deps.botId}` : ""}`,
    );
    if (!resp.ok) return result;

    const raw = await resp.json();
    const list: Array<Record<string, unknown>> = Array.isArray(raw) ? raw : (raw as any).workflows ?? [];
    const dbWorkflowIds = new Set<string>();

    for (const wf of list) {
      const workflowId = (wf.workflow_id ?? wf.workflowId) as string;
      dbWorkflowIds.add(workflowId);

      if (!localWorkflowIds.has(workflowId)) {
        if (loadErrorWorkflowIds.has(workflowId)) {
          // Broken workflow — don't auto-sync, will be fixed by manual deploy
          continue;
        }
        // New workflow not in local — need to sync
        result.changed.push(workflowId);
        continue;
      }

      // Workflow exists locally — compare actual spec content
      const localSpec = localSpecMap.get(workflowId);
      if (!localSpec) {
        // Can't read/parse local spec (broken YAML) — skip, don't retry indefinitely
        continue;
      }

      try {
        const detailResp = await fetch(
          `${deps.clawWebBaseUrl}/api/workflows/${encodeURIComponent(workflowId)}`,
        );
        if (!detailResp.ok) continue;
        const dbSpec = await detailResp.json() as Record<string, unknown>;

        if (normalizeSpecForCompare(localSpec) !== normalizeSpecForCompare(dbSpec)) {
          // Content differs — check WHO is newer before overwriting.
          // Only pull (DB→local) when DB was updated AFTER local file.
          // This prevents sync-poll from clobbering local edits that haven't been deployed yet.
          // Use updatedAt from detail API, fallback to list API entry (which always has gmt_modified).
          const dbUpdatedRaw = (dbSpec as any).updatedAt ?? (dbSpec as any).gmt_modified ?? wf.updatedAt ?? (wf as any).gmt_modified;
          let dbUpdatedMs = parseDbTimestampSafe(dbUpdatedRaw);
          const localMtimeMs = getLocalMtime(deps, workflowId);

          if (dbUpdatedMs > 0 && localMtimeMs > 0 && dbUpdatedMs > localMtimeMs) {
            // DB is newer → safe to pull
            result.changed.push(workflowId);
          } else if (dbUpdatedMs > 0 && localMtimeMs > 0 && dbUpdatedMs <= localMtimeMs) {
            // Local is newer or same age → don't overwrite, user is editing locally
            console.log(`[sync-poll] ${workflowId}: local is newer (local=${formatLocalTime(localMtimeMs)}, db=${formatLocalTime(dbUpdatedMs)}), skipping`);
          } else {
            // Can't determine timestamps — pull to be safe (DB is source of truth)
            result.changed.push(workflowId);
          }
        }
        // Content matches — skip
      } catch {
        // API failure — don't mark as changed
      }
    }

    // Detect deletions: local has it, DB doesn't
    for (const localId of localWorkflowIds) {
      if (!dbWorkflowIds.has(localId)) {
        const packId = findLocalPackId(deps, localId);
        result.deleted.push({ workflowId: localId, packId });
      }
    }
  } catch {
    // API unreachable — non-fatal
  }

  return result;
}

/**
 * Lightweight sync: write DB spec to local YAML file.
 * NO git operations — just writes the file to disk.
 * Git commit/push happens at deploy time.
 */
export async function handleSyncWrite(
  deps: SyncPollDeps,
  workflowId: string,
): Promise<string> {
  try {
    const detailResp = await fetch(
      `${deps.clawWebBaseUrl}/api/workflows/${encodeURIComponent(workflowId)}`,
    );
    if (!detailResp.ok) return `❌ ${workflowId}: DB fetch failed (${detailResp.status})`;

    const dbSpec = await detailResp.json() as Record<string, unknown>;
    const packId = (dbSpec as any).packId ?? (dbSpec as any).pack_id ?? workflowId;

    // Write YAML to packs/{packId}/workflows/{workflowId}.yaml
    const yamlPath = join(deps.packsRoot, packId, "workflows", `${workflowId}.yaml`);
    await mkdir(dirname(yamlPath), { recursive: true });

    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const yaml = require("js-yaml");
    delete (dbSpec as any).version;
    delete (dbSpec as any).facade;
    await writeFile(yamlPath, yaml.dump(dbSpec, { lineWidth: -1 }), "utf-8");

    return `✅ ${workflowId}: synced from DB (pack=${packId})`;
  } catch (err) {
    return `❌ ${workflowId}: sync failed — ${err instanceof Error ? err.message : err}`;
  }
}

/**
 * Handle deletion of a workflow: remove local YAML file only.
 * NO git operations.
 */
export async function handleSyncDelete(
  packsRoot: string,
  workflowId: string,
  packId: string,
  _options?: {
    botId?: string;
    ownerId?: string;
  },
): Promise<string> {
  const yamlPath = join(packsRoot, packId, "workflows", `${workflowId}.yaml`);
  try {
    await unlink(yamlPath).catch(() => { /* already gone */ });

    // Check if pack directory is now empty
    const workflowsDir = join(packsRoot, packId, "workflows");
    try {
      const remainingFiles = await readdir(workflowsDir).catch(() => [] as string[]);
      const yamlFiles = remainingFiles.filter((f) => f.endsWith(".yaml") || f.endsWith(".yml"));
      if (yamlFiles.length === 0) {
        // Pack is empty — remove entire pack directory
        await rm(join(packsRoot, packId), { recursive: true, force: true });
      }
    } catch { /* best effort */ }

    return `🗑️ 已同步删除: ${workflowId} (pack=${packId})`;
  } catch (err) {
    return `❌ 同步删除失败: ${workflowId}: ${err instanceof Error ? err.message : err}`;
  }
}