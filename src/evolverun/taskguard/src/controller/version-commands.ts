/**
 * Version management command handlers for ClawMind workflow engine.
 *
 * Handles deploy, pull, rollback, history, status, share, unshare, and migration.
 */

/**
 * Get a formatted diff text for a workflow's sync status.
 * Used by handleDetail to inline diff when status is "differs".
 *
 * Compares the DB's CURRENT spec (from workflow_specs table via GET /api/workflows/:id)
 * against the local YAML, not the deploy_history snapshot. This gives an accurate
 * comparison of "what DB actually stores now" vs "what's on disk".
 */
export async function getSyncDiffText(
  deps: VersionCommandDeps,
  workflowId: string,
): Promise<{ deployedVersion: number | undefined; deployedTag: string; diffText: string; dbSpecJson: string; localSpecJson: string } | null> {
  const packId = resolvePackId(deps, workflowId);

  // Fetch DB current spec (workflow_specs table), not deploy_history snapshot
  const dbSpec = await fetchDbSpec(deps, workflowId);
  if (!dbSpec) return null;

  const resolvedWf = (deps.resolvedWorkflows ?? []).find((w) => w.id === workflowId);
  const localSpec = await readRawLocalSpec(resolvedWf);

  const diffText = computeSpecDiff(dbSpec, localSpec);

  // Deploy info from deploy_history (for version/tag display only)
  const apiRepo = createDeployHistoryApiRepo(deps);
  const latestDeploy = apiRepo ? await apiRepo.getLatestDeploy(packId, workflowId) : undefined;
  const deployedTag = latestDeploy ? `deploy/${workflowId}/#${latestDeploy.deployNumber}` : "none";

  return {
    deployedVersion: latestDeploy?.version,
    deployedTag,
    diffText,
    dbSpecJson: JSON.stringify(dbSpec),
    localSpecJson: JSON.stringify(localSpec),
  };
}
import type { IDatabase } from "../db/types.js";
import { execFileSync } from "node:child_process";
import path from "node:path";
import fs from "node:fs/promises";
import fsSync from "node:fs";
import yaml from "js-yaml";
import { gitModule, parseDeployTag } from "../git/index.js";
import { DeployHistoryApiRepository } from "../db/api-repositories/deploy-history-api-repository.js";
import { ApiClient } from "../db/api-client.js";
import { stripEngineDefaults } from "../validation/engine-defaults.js";
import type { IFacadeBindingRepository } from "../db/repositories/types.js";
import type { FailedWorkflow, ResolvedWorkflow, ResolvedWorkflowPack } from "../packs/types.js";
import { quickScanSpecFile } from "../validation/quick-scan.js";
import { readPackManifest, PACK_MANIFEST_FILENAME } from "../packs/manifest.js";
import { digestPackDirectory } from "../packs/digest.js";
import { defaultWorkspaceWorkflowsRoots } from "../packs/resolver.js";

// ── Types ──

export type VersionCommandDeps = {
  db?: IDatabase;
  clawWebBaseUrl?: string;
  signatureKey?: string;
  botId?: string;
  ownerId?: string;
  resolvedWorkflows?: ResolvedWorkflow[];
  resolvedPacks?: ResolvedWorkflowPack[];
  failedWorkflows?: FailedWorkflow[];
  packsRoot?: string;
  /** Git remote URL — all per-pack repos push to the same remote */
  gitRemoteUrl?: string;
  /** Git username for credential-cache */
  gitUsername?: string;
  /** Git token (from env or config) for credential-cache */
  gitToken?: string;
  /** Git email for commit author. Company git servers (e.g. code.alipay.com) reject non-company emails. */
  gitEmail?: string;
  /**
   * Facade binding repository — used by handleDeploy to write facade_bindings rows
   * (with conflict check against other workflows). API mode → FacadeBindingApiRepository,
   * DB mode → FacadeBindingRepository. Absent → deploy skips binding write (warning).
   */
  facadeBindingRepo?: IFacadeBindingRepository;
};

/** Facade command naming rule: kebab/snake-case, lowercase letters/digits (mirrors src/api/routes/facades.ts). */
const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/;

/**
 * 在 failedWorkflows 里命中 load_error 条目。id 主键 + 文件名 basename 兜底:
 * id-mismatch 时 fw.id 已被改成 manifest 值,只能靠文件名认出;basename 防给改过。
 * validate 与 deploy 的 not-found 分支共用,杜绝两路判定漂移。
 */
export function findFailedWorkflow(
  failedWorkflows: FailedWorkflow[] | undefined,
  workflowId: string,
): FailedWorkflow | undefined {
  if (!failedWorkflows?.length) return undefined;
  const wsLower = workflowId.toLowerCase();
  return failedWorkflows.find((f) =>
    f.id === workflowId
    || f.id.toLowerCase() === wsLower
    || path.basename(f.absolutePath, ".yaml") === workflowId
    || path.basename(f.absolutePath, ".yml") === workflowId,
  );
}

// ── Helpers ──

/** Resolve packId for a workflow from local file structure or DB. */
function resolvePackId(deps: VersionCommandDeps, workflowId: string): string {
  const resolvedWorkflows = deps.resolvedWorkflows ?? [];
  for (const pack of (deps.resolvedPacks ?? [])) {
    const workflows = pack.workflows ?? [];
    if (workflows.some((w) => w.id === workflowId)) {
      return pack.manifest?.id ?? workflowId;
    }
  }
  for (const wf of resolvedWorkflows) {
    if (wf.id === workflowId && wf.pack?.id) {
      return wf.pack.id;
    }
  }
  return workflowId;
}

/** Check permission via ClawWeb API. All version management commands use can_edit. Fallback to allow on API failure. */
async function checkEditPermission(
  deps: VersionCommandDeps,
  workflowId: string,
): Promise<{ allowed: boolean; reason?: string }> {
  if (!deps.clawWebBaseUrl || !deps.botId || !deps.ownerId || !deps.signatureKey) {
    // Missing config — cannot check permission, allow by default
    // (permission check is a guard, not a gate; missing config should not block operations)
    console.warn(`[permission] skip edit permission check for ${workflowId}: missing clawWebBaseUrl/botId/ownerId/signatureKey`);
    return { allowed: true };
  }
  try {
    const client = new ApiClient({ baseUrl: deps.clawWebBaseUrl, privateKeyB64: deps.signatureKey });
    const resp = await client.post<{ allowed: boolean; hasRecords: boolean }>(
      "/bot-workflow-permissions/check",
      {
        botId: deps.botId,
        botOwnerId: deps.ownerId,
        workflowId,
        permission: "edit",
      },
    );
    if (!resp.ok) return { allowed: false, reason: `权限检查失败: API 返回 ${resp.status} (${resp.error})` };
    const data = resp.data;
    if (!data) return { allowed: false, reason: "权限检查失败: API 返回空响应" };
    if (!data.hasRecords) return { allowed: true };
    return { allowed: data.allowed, reason: data.allowed ? undefined : `无权限部署 workflow "${workflowId}"` };
  } catch (err) {
    return { allowed: false, reason: `权限检查失败: ${err instanceof Error ? err.message : err}` };
  }
}

/** Write spec to a local YAML file in the pack structure. Strips UI decoration and DB metadata fields. */
async function writeSpecToYaml(packsDir: string, packId: string, workflowId: string, spec: Record<string, unknown>): Promise<void> {
  const yamlDir = path.join(packsDir, packId, "workflows");
  await fs.mkdir(yamlDir, { recursive: true });
  const clean = stripUiFields(stripUndefinedDeep({ ...spec }));
  delete (clean as Record<string, unknown>).updatedAt;   // DB metadata, not part of workflow spec — don't write to YAML
  await fs.writeFile(path.join(yamlDir, `${workflowId}.yaml`), yaml.dump(clean), "utf-8");
}

/**
 * Ensure a pack manifest (workflow.pack.yaml) exists and is up-to-date.
 *
 * The pack discovery code (discoverWorkflowPacks) requires every pack directory
 * to contain a `workflow.pack.yaml` file listing the pack's workflows.
 * Migration/pull/deploy handlers write individual workflow YAMLs but must also
 * maintain this manifest so the pack is discoverable on the next command dispatch.
 */
async function ensurePackManifest(
  packsDir: string,
  packId: string,
  workflowIds?: string[],
): Promise<void> {
  const manifestPath = path.join(packsDir, packId, "workflow.pack.yaml");
  const workflowsDir = path.join(packsDir, packId, "workflows");

  let diskWorkflows: Array<{ id: string; file: string }>;
  try {
    const entries = await fs.readdir(workflowsDir);
    const yamlEntries = entries.filter((e) =>
      (e.endsWith(".yaml") || e.endsWith(".yml")) && !e.startsWith("._"),
    );
    diskWorkflows = yamlEntries.map((e) => ({
      id: path.basename(e, path.extname(e)),
      file: `workflows/${e}`,
    }));
  } catch {
    diskWorkflows = (workflowIds ?? []).map((id) => ({
      id,
      file: `workflows/${id}.yaml`,
    }));
  }

  if (diskWorkflows.length === 0) return;

  let existingManifest: Record<string, unknown> | null = null;
  try {
    const content = await fs.readFile(manifestPath, "utf-8");
    existingManifest = yaml.load(content) as Record<string, unknown>;
  } catch {
    // manifest doesn't exist yet
  }

  const diskWfIds = new Set(diskWorkflows.map((w) => w.id));

  let manifest: Record<string, unknown>;
  if (existingManifest) {
    const existingWorkflows = (existingManifest.workflows as Array<{ id: string; file: string }>) ?? [];
    const merged = new Map<string, { id: string; file: string }>();
    for (const wf of existingWorkflows) {
      if (diskWfIds.has(wf.id)) merged.set(wf.id, wf);
    }
    for (const wf of diskWorkflows) {
      if (!merged.has(wf.id)) merged.set(wf.id, wf);
    }
    manifest = { ...existingManifest, workflows: Array.from(merged.values()) };
  } else {
    // Auto-generate facades: each workflow gets its own facade command
    // so /<workflowId> triggers it directly without requiring DB bindings.
    const facades = diskWorkflows.map((wf) => ({
      command: wf.id,
      defaultWorkflow: wf.id,
    }));
    manifest = { id: packId, version: "1", workflows: diskWorkflows, facades };
  }

  // For existing manifests missing facades, auto-add them
  if (!manifest.facades) {
    const workflows = (manifest.workflows as Array<{ id: string }>) ?? [];
    manifest.facades = workflows.map((wf) => ({
      command: wf.id,
      defaultWorkflow: wf.id,
    }));
  }

  await fs.writeFile(manifestPath, yaml.dump(manifest), "utf-8");
}

/** install-pack / deploy --file 安装决策结果。 */
export type DeployFileInstallPlan =
  | { kind: "noop"; reason: string }
  | { kind: "skip-identical" }
  | { kind: "install" }
  | { kind: "overwrite" }
  | { kind: "blocked"; reason: string };

/** computeInstallPlan 输入。 */
export interface InstallPlanInput {
  sameRealpath: boolean;
  destExists: boolean;
  srcDigest: string;
  destDigest: string | undefined;
  destHasUncommittedChanges: boolean;
  destMtimeMs: number;
  srcMtimeMs: number;
  force: boolean;
}

/**
 * 纯决策:根据源/目标 pack 事实判定安装动作。
 * - sameRealpath → noop; !destExists → install; digest 相等 → skip-identical
 * - 不同 +(未提交改动 OR 目标更新)+ !force → blocked;+ force → overwrite
 * - 不同 + 目标更旧/同齡且干净 → overwrite(不需 force)
 */
export function computeInstallPlan(input: InstallPlanInput): DeployFileInstallPlan {
  const { sameRealpath, destExists, srcDigest, destDigest, destHasUncommittedChanges, destMtimeMs, srcMtimeMs, force } = input;
  if (sameRealpath) return { kind: "noop", reason: "源即目标 pack 目录,跳过复制" };
  if (!destExists) return { kind: "install" };
  if (destDigest !== undefined && srcDigest === destDigest) return { kind: "skip-identical" };
  const destAtRisk = destHasUncommittedChanges || destMtimeMs > srcMtimeMs;
  if (destAtRisk && !force) {
    return { kind: "blocked", reason: destHasUncommittedChanges ? "目标 pack 有未提交 git 改动" : "目标 pack 比源更新" };
  }
  return { kind: "overwrite" };
}

/** 目标 pack 是否为 git 仓库且有未提交改动。非 git → false(回退 mtime 判据)。 */
export function hasUncommittedGitChanges(dir: string): boolean {
  try {
    const out = execFileSync("git", ["-C", dir, "status", "--porcelain"], {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return out.trim().length > 0;
  } catch {
    return false;
  }
}

/** fs.cp filter:排除源 pack 的 .git(dest 的 .git 由 ensureGitRepoForPack 管)。 */
function cpExcludeGit(source: string): boolean {
  return path.basename(source) !== ".git";
}

/**
 * 按 plan 执行 pack 复制。
 * - install:mkdir + fs.cp(排除 .git)
 * - overwrite:保 dest/.git,删 dest 其余条目,再 fs.cp(排除 .git)
 * - noop/skip-identical/blocked:不写盘(blocked 由调用方转文案)
 */
export async function installPackFromSource(
  srcPackDir: string,
  destDir: string,
  plan: DeployFileInstallPlan,
  outWarnings: string[],
): Promise<void> {
  if (plan.kind === "noop" || plan.kind === "skip-identical" || plan.kind === "blocked") {
    if (plan.kind === "noop") outWarnings.push(`📦 ${plan.reason}`);
    if (plan.kind === "skip-identical") outWarnings.push(`📦 目标 pack 内容相同,跳过复制`);
    return;
  }
  if (plan.kind === "install") {
    await fs.mkdir(destDir, { recursive: true });
    await fs.cp(srcPackDir, destDir, { recursive: true, filter: (_src, _dst) => cpExcludeGit(_src) });
    outWarnings.push(`📦 已安装 pack 到 ${destDir}`);
    return;
  }
  // overwrite:保留 dest/.git,删其余,再复制源(排除 .git)
  const entries = await fs.readdir(destDir);
  for (const name of entries) {
    if (name === ".git") continue;
    await fs.rm(path.join(destDir, name), { recursive: true, force: true });
  }
  await fs.cp(srcPackDir, destDir, { recursive: true, filter: (_src, _dst) => cpExcludeGit(_src) });
  outWarnings.push(`📦 已覆盖 pack(${destDir}),保留 .git 历史`);
}

export interface InstallPackResult {
  packId: string;
  destDir: string;
  plan: DeployFileInstallPlan;
  warnings: string[];
  srcPackDir: string;
  blocked?: true;
  blockedMessage?: string;
}

function statMtimeMs(dir: string): number {
  try { return fsSync.statSync(dir).mtimeMs; } catch { return 0; }
}

/**
 * install-pack orchestrator:解析源 pack manifest、决策安装、执行复制。纯 fs,可单测。
 * packId 取源 manifest.id(不靠 yaml 字段推);install/overwrite 复制排除源 .git。
 */
export async function prepareInstallPackSource(
  deps: VersionCommandDeps,
  packDir: string,
  options: { force?: boolean },
): Promise<InstallPackResult> {
  const warnings: string[] = [];
  const packsRoot = deps.packsRoot!;
  const srcPackDir = path.resolve(packDir);
  if (!fsSync.existsSync(srcPackDir)) throw new Error(`pack 目录不存在: ${srcPackDir}`);
  if (!fsSync.existsSync(path.join(srcPackDir, PACK_MANIFEST_FILENAME))) {
    throw new Error(`不是 pack 目录(缺 ${PACK_MANIFEST_FILENAME}): ${srcPackDir}`);
  }
  const manifest = readPackManifest(srcPackDir);
  const packId = manifest.id;
  const destDir = path.join(packsRoot, packId);

  const destExists = fsSync.existsSync(destDir);
  let sameRealpath = false;
  try { sameRealpath = destExists && fsSync.realpathSync(srcPackDir) === fsSync.realpathSync(destDir); }
  catch { /* dest 断链等 → 非同路径 */ }
  const srcDigest = digestPackDirectory(srcPackDir);
  const destDigest = destExists ? digestPackDirectory(destDir) : undefined;
  const destHasUncommittedChanges = destExists && hasUncommittedGitChanges(destDir);
  const destMtimeMs = destExists ? statMtimeMs(destDir) : 0;
  const srcMtimeMs = statMtimeMs(srcPackDir);

  const plan = computeInstallPlan({
    sameRealpath, destExists, srcDigest, destDigest,
    destHasUncommittedChanges, destMtimeMs, srcMtimeMs, force: !!options.force,
  });

  if (plan.kind === "blocked") {
    warnings.push(`⛔ pack "${packId}" 已存在且${plan.reason}`);
    const blockedMessage = [
      `❌ pack "${packId}" 已存在于 ${destDir} 且${plan.reason},拒绝覆盖。`,
      `  源: ${srcPackDir}`,
      `  目标: ${destDir}`,
      ``,
      `  选项:`,
      `  1. 加 --force 强制覆盖(目标 .git 历史保留,可回滚)`,
      `  2. 先提交/丢弃目标 pack 的本地改动`,
      `  3. 换一个 packId(改源 manifest id)`,
    ].join("\n");
    return { packId, destDir, plan, warnings, srcPackDir, blocked: true, blockedMessage };
  }

  await installPackFromSource(srcPackDir, destDir, plan, warnings);
  return { packId, destDir, plan, warnings, srcPackDir };
}

/**
 * install-pack <packDir> [--only <wfId>] [--force] [--move]:
 * 整包装进 deps.packsRoot/<manifest.id>/(排除源 .git),部署 --only workflow 到 DB+git,
 * 走 localAuthoritative(不 checkout origin、不 smartPull,防远端覆盖刚装内容)。
 */
export async function handleInstallPack(
  deps: VersionCommandDeps,
  packDir: string,
  options: { only?: string; force?: boolean; move?: boolean },
): Promise<string> {
  const warnings: string[] = [];
  const install = await prepareInstallPackSource(deps, packDir, { force: options.force });
  if (install.blocked) return install.blockedMessage!;
  warnings.push(...install.warnings);

  // 选部署 workflow
  const manifest = readPackManifest(install.destDir);
  const wfIds = (manifest.workflows ?? []).map((w: any) => w.id).filter(Boolean) as string[];
  let deployWfId: string | undefined;
  if (options.only) {
    if (!wfIds.includes(options.only)) {
      return `❌ workflow "${options.only}" 不在 pack "${install.packId}" 内。pack 内 workflow: ${wfIds.join(", ") || "(无)"}`;
    }
    deployWfId = options.only;
  } else if (wfIds.length === 1) {
    deployWfId = wfIds[0];
  } else if (wfIds.length === 0) {
    return `❌ pack "${install.packId}" 无 workflow(manifest.workflows 为空),仅安装文件。`;
  } else {
    return [
      `📦 pack "${install.packId}" 已安装到 ${install.destDir}(仅装文件,未部署)。`,
      `  pack 内 workflow: ${wfIds.join(", ")}`,
      `  用 install-pack <packDir> --only <wfId> 指定要部署的 workflow。`,
    ].join("\n");
  }

  // 源在别的 discovery 根警告(复制后两份 → multiple-packs)
  if (install.plan.kind === "install" || install.plan.kind === "overwrite") {
    try {
      const srcReal = fsSync.realpathSync(install.srcPackDir);
      const destReal = fsSync.realpathSync(install.destDir);
      if (srcReal !== destReal) {
        const inDiscovery = defaultWorkspaceWorkflowsRoots().some((r) => srcReal.startsWith(fsSync.realpathSync(r) + path.sep));
        if (inDiscovery) {
          warnings.push(`⚠️ 源 ${install.srcPackDir} 在 discovery 根内且 ≠ 目标,复制后两份 pack 会触发 multiple-packs 报错。建议 --move 或手动移走源。`);
        }
      }
    } catch { /* realpath 失败忽略 */ }
  }

  // 读 dest 内该 workflow spec,走 handleDeploy pass-through + localAuthoritative
  const wfRef = (manifest.workflows ?? []).find((w) => w.id === deployWfId);
  const specPath = path.join(install.destDir, wfRef.file);
  let spec: Record<string, unknown>;
  try {
    spec = yaml.load(await fs.readFile(specPath, "utf-8")) as Record<string, unknown>;
  } catch (err) {
    return `❌ 读取 workflow spec 失败 ${specPath}: ${err instanceof Error ? err.message : err}`;
  }
  if (spec.id && spec.id !== deployWfId) {
    warnings.push(`⚠️ workflow id="${spec.id}" 与参数 "${deployWfId}" 不一致,以参数为准`);
    spec.id = deployWfId;
  }

  const deployOut = await handleDeploy(deps, deployWfId, {
    specObj: spec, packId: install.packId,
    force: options.force, localAuthoritative: true,
  });
  const deployFailed = deployOut.startsWith("❌") || deployOut.startsWith("⛔");

  // --move:成功后删源(源≠dest)。部署失败时跳过,避免丢失源。
  if (options.move && !deployFailed && (install.plan.kind === "install" || install.plan.kind === "overwrite")) {
    try {
      const srcReal = fsSync.realpathSync(install.srcPackDir);
      const destReal = fsSync.realpathSync(install.destDir);
      if (srcReal !== destReal) {
        await fs.rm(install.srcPackDir, { recursive: true, force: true });
        warnings.push(`📦 --move:已删除源 ${install.srcPackDir}`);
      }
    } catch (err) {
      warnings.push(`⚠️ --move 删除源失败: ${err instanceof Error ? err.message : err}`);
    }
  } else if (options.move && deployFailed) {
    warnings.push(`⚠️ --move 跳过:部署失败,源未删除(${install.srcPackDir})`);
  }

  const summary = [
    `✅ install-pack "${install.packId}" → ${install.destDir} (${install.plan.kind})`,
    `  部署 workflow: ${deployWfId}`,
    deployOut,
    ...warnings.map((w) => `  ${w}`),
  ];
  return summary.join("\n");
}

/**
 * Repair packs that are missing their workflow.pack.yaml manifest.
 * Returns list of packIds that were repaired.
 */
async function repairMissingPackManifests(packsDir: string, resolvedPacks: any[]): Promise<string[]> {
  const repaired: string[] = [];
  try {
    const entries = await fs.readdir(packsDir);
    for (const entry of entries) {
      const entryPath = path.join(packsDir, entry);
      const stat = await fs.stat(entryPath);
      if (!stat.isDirectory()) continue;
      if (entry.startsWith(".")) continue;

      const manifestPath = path.join(entryPath, "workflow.pack.yaml");
      try {
        await fs.access(manifestPath);
      } catch {
        console.log(`[migration] Repairing missing manifest for pack: ${entry}`);
        await ensurePackManifest(packsDir, entry);
        repaired.push(entry);
      }
    }
  } catch (err) {
    console.warn(`[migration] repairMissingPackManifests failed: ${err instanceof Error ? err.message : err}`);
  }
  return repaired;
}

/**
 * Smart extraction from remote: handles both old single-repo structure
 * (pack content in subdirectories like tech-research/) and new per-pack
 * structure (files at repo root).
 *
 * Old structure (from single packs/.git/ repo):
 *   origin/wf/tech-research:
 *     tech-research/workflow.pack.yaml
 *     tech-research/workflows/tech-research.yaml
 *     tech-research/scripts/
 *     another-workflow/...
 *
 * New structure (from per-pack packs/tech-research/.git/ repo):
 *   origin/wf/tech-research:
 *     workflow.pack.yaml
 *     workflows/tech-research.yaml
 *     scripts/
 *
 * Returns true if content was pulled from remote.
 */
async function smartPullFromRemote(
  packDir: string,
  packId: string,
  branchName: string,
): Promise<boolean> {
  const remoteRef = `origin/${branchName}`;

  // Check if the remote branch was fetched (exists in local refs)
  try {
    execFileSync("git", ["rev-parse", "--verify", remoteRef], {
      cwd: packDir,
      encoding: "utf-8",
      timeout: 5000,
    });
  } catch {
    // Remote branch doesn't exist — first time setup, nothing to pull
    return false;
  }

  // Check top-level structure of the remote branch
  let topLevelEntries: string[];
  try {
    const output = execFileSync("git", ["ls-tree", "--name-only", remoteRef], {
      cwd: packDir,
      encoding: "utf-8",
      timeout: 5000,
    });
    topLevelEntries = output.trim().split("\n").filter(Boolean);
  } catch {
    return false;
  }

  const hasOldStructure = topLevelEntries.includes(packId);

  if (hasOldStructure) {
    // OLD structure: remote has {packId}/ as a subdirectory.
    // Extract only this pack's files, stripping the {packId}/ prefix.
    console.log(`[git] Detected old-structure remote branch (${branchName}), extracting ${packId}/ content`);

    let files: string[];
    try {
      const output = execFileSync("git", ["ls-tree", "-r", "--name-only", remoteRef, "--", packId], {
        cwd: packDir,
        encoding: "utf-8",
        timeout: 10000,
      });
      files = output.trim().split("\n").filter(Boolean);
    } catch (err) {
      console.warn(`[git] ls-tree failed for old-structure extraction: ${err instanceof Error ? err.message : err}`);
      return false;
    }

    const packPrefix = `${packId}/`;
    for (const file of files) {
      if (!file.startsWith(packPrefix)) continue;
      const localPath = file.slice(packPrefix.length); // strip packId/
      if (!localPath) continue;
      const fullPath = path.join(packDir, localPath);

      // Ensure parent directory exists
      await fs.mkdir(path.dirname(fullPath), { recursive: true });

      try {
        const content = execFileSync("git", ["show", `${remoteRef}:${file}`], {
          cwd: packDir,
          encoding: "buffer", // binary-safe
          timeout: 10000,
        });
        await fs.writeFile(fullPath, content);
      } catch (err) {
        console.warn(`[git] failed to extract ${file}: ${err instanceof Error ? err.message : err}`);
      }
    }

    // Commit the extracted and reorganized content
    try {
      await gitModule.addCommit(packDir, ["."], `[migration] extract ${packId} from old-structure remote`);
    } catch {
      // Nothing to commit — OK
    }
    return true;
  } else {
    // NEW structure: files are at root level
    // Check if local repo has any commits (empty repo = 0 commits)
    let localHasCommits = false;
    try {
      execFileSync("git", ["rev-parse", "HEAD"], {
        cwd: packDir,
        encoding: "utf-8",
        timeout: 5000,
      });
      localHasCommits = true;
    } catch {
      localHasCommits = false;
    }

    if (!localHasCommits) {
      // Empty local repo: git pull --rebase fails because there's no HEAD to rebase onto.
      // Use `git reset --hard origin/<branch>` to adopt the remote history as our own,
      // then local untracked files (e.g. scripts/) remain on disk untouched.
      // The next addCommit(["."]) will commit them on top of the remote history,
      // producing a fast-forwardable push instead of a divergent force-push.
      console.log(`[git] Empty local repo, resetting to ${remoteRef} (remote has history)`);
      try {
        execFileSync("git", ["reset", "--hard", remoteRef], {
          cwd: packDir,
          encoding: "utf-8",
          timeout: 10000,
        });
        return true;
      } catch (err) {
        console.warn(`[git] reset --hard ${remoteRef} failed: ${err instanceof Error ? err.message : err}`);
        return false;
      }
    } else {
      // Normal case: local has commits — rebase onto remote
      console.log(`[git] Detected new-structure remote branch (${branchName}), pulling normally`);
      try {
        await gitModule.pullRebase(packDir, branchName);
        return true;
      } catch {
        return false;
      }
    }
  }
}

/**
 * Ensure a per-pack git repo exists and is on the correct branch.
 *
 * Each pack directory (packs/{packId}/) is its own git repository,
 * all pushing to the same remote on branch wf/{workflowId}.
 *
 * This MUST be called before any git operation on a pack.
 */
export async function ensureGitRepoForPack(
  packsDir: string,
  packId: string,
  workflowId: string,
  deps: VersionCommandDeps,
  options?: { localAuthoritative?: boolean },
): Promise<string> {
  const packDir = path.join(packsDir, packId);
  const branchName = `wf/${workflowId}`;

  // Ensure pack directory exists
  await fs.mkdir(packDir, { recursive: true });

  // Init repo if .git doesn't exist
  await gitModule.init(packDir);

  // Set git user identity (required for commit in containers without global gitconfig)
  // Use || (not ??) to fallback on empty strings too — loadConfig() defaults git.username to ""
  // which would pass through ?? since "" !== null/undefined, causing "empty ident name" fatal.
  const gitUser = deps.gitUsername || deps.botId || "clawmind";
  // Email: must be a valid company email on corporate git servers (e.g. code.alipay.com
  // rejects @clawmind.local). Prefer deps.gitEmail (from config), then derive from username.
  const gitEmail = deps.gitEmail || `${gitUser}@antgroup.com`;
  try {
    execFileSync("git", ["config", "user.name", gitUser], { cwd: packDir });
    execFileSync("git", ["config", "user.email", gitEmail], { cwd: packDir });
    console.log(`[git] identity: ${gitUser} <${gitEmail}>`);
  } catch { /* best-effort */ }

  // Set remote + credentials (same for all pack repos)
  let remoteBranchExists = false;
  if (deps.gitRemoteUrl) {
    await gitModule.addRemote(packDir, deps.gitRemoteUrl);
    const token = deps.gitToken ?? "";
    if (token) {
      // Use gitUser (already fallback-resolved) instead of raw deps.gitUsername
      // which may be "" — configureCredential skips when username is falsy
      await gitModule.configureCredential(packDir, gitUser, token);
    }
    // Fetch from remote (this branch only). Returns whether the branch truly
    // exists on the remote — distinguishing "genuinely first-time" from
    // "fetch failed", so smartPullFromRemote doesn't fabricate a divergent root.
    remoteBranchExists = await gitModule.fetchRemote(packDir, branchName);
  }

  // Ensure we're on the workflow's branch
  await gitModule.ensureBranch(packDir, branchName, options);

  // Smart extraction from remote: handles old (subdirectory) and new (root) structures.
  // Only attempt pull when we actually fetched the branch; otherwise we'd risk
  // creating a divergent root commit on top of a fetch failure.
  if (deps.gitRemoteUrl && remoteBranchExists && !options?.localAuthoritative) {
    await smartPullFromRemote(packDir, packId, branchName);
  } else if (deps.gitRemoteUrl && remoteBranchExists && options?.localAuthoritative) {
    console.log(`[deploy] localAuthoritative: skip smartPullFromRemote for ${packId} (local source authoritative)`);
  }

  return packDir;
}

/** Deploy a single workflow spec to ClawWeb via the save API. */
async function deployToClawWeb(deps: VersionCommandDeps, workflowId: string, packId: string, spec: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (!deps.clawWebBaseUrl) throw new Error("clawWebBaseUrl not configured");
  const resp = await fetch(`${deps.clawWebBaseUrl}/api/workflows/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workflowId,
      packId,
      spec,
      botId: deps.botId,
      botOwnerId: deps.ownerId,
      skipDeployHistory: true,
    }),
  });
  if (!resp.ok) {
    const errText = await resp.text();
    throw new Error(`ClawWeb save failed (${resp.status}): ${errText}`);
  }
  // Return the spec as stored by ClawWeb (after its normalization pipeline)
  // so callers can write it to local YAML and stay in sync with DB.
  try {
    const saved = await resp.json() as Record<string, unknown>;
    return saved;
  } catch {
    // If we can't parse the response, return the input spec as fallback
    return spec;
  }
}

/** Get all workflow IDs in a pack from the resolved packs. */
function getPackWorkflowIds(deps: VersionCommandDeps, packId: string): string[] {
  for (const pack of (deps.resolvedPacks ?? [])) {
    if (pack.manifest?.id === packId) {
      const workflows = pack.workflows ?? [];
      return workflows.map((w) => w.id);
    }
  }
  return [];
}

/**
 * Collect the set of facade commands (command + aliases) a workflow should bind,
 * drawn from the pack manifest's `facades[]` entries whose `defaultWorkflow`
 * matches `workflowId`. When the workflow isn't found in any resolved pack
 * (e.g. `--file` deploy of a brand-new YAML not yet in a pack), fall back to
 * treating `workflowId` itself as the command — mirroring `ensurePackManifest`'s
 * auto-generated `{ command: workflowId, defaultWorkflow: workflowId }`.
 *
 * Returns the commands deduplicated and in insertion order, normalized lower-case.
 * Invalid (non kebab/snake-case) commands are skipped with a warning pushed to
 * `outWarnings` rather than aborting the deploy.
 */
function collectDesiredFacadeCommands(
  deps: VersionCommandDeps,
  workflowId: string,
  packId: string,
  outWarnings: string[],
): string[] {
  const collected: string[] = [];
  const seen = new Set<string>();
  const push = (raw: string) => {
    const cmd = raw.trim().toLowerCase();
    if (!cmd || seen.has(cmd)) return;
    if (!COMMAND_PATTERN.test(cmd)) {
      outWarnings.push(`⚠️ 跳过非法 facade command "${raw}"(需为小写 kebab/snake-case)`);
      return;
    }
    seen.add(cmd);
    collected.push(cmd);
  };

  let foundInPack = false;
  for (const pack of (deps.resolvedPacks ?? [])) {
    if (pack.manifest?.id !== packId) continue;
    const facades = pack.manifest?.facades ?? [];
    for (const f of facades) {
      const defaultWf = (f?.defaultWorkflow ?? "").toString();
      if (defaultWf !== workflowId) continue;
      foundInPack = true;
      if (typeof f.command === "string") push(f.command);
      const aliases: unknown[] = Array.isArray(f.aliases) ? f.aliases : [];
      for (const a of aliases) if (typeof a === "string") push(a);
    }
    if (foundInPack) break;
  }

  if (!foundInPack && deps.packsRoot) {
    // resolvedPacks 是 dispatch 时快照,不含 install-pack 新装的 pack。
    // 兜底从 packsRoot/<packId>/workflow.pack.yaml 读 manifest,取 defaultWorkflow=workflowId 的 facade。
    try {
      const packDirOnDisk = path.join(deps.packsRoot, packId);
      if (fsSync.existsSync(path.join(packDirOnDisk, PACK_MANIFEST_FILENAME))) {
        const m = readPackManifest(packDirOnDisk);
        for (const f of (m.facades ?? [])) {
          if ((f.defaultWorkflow ?? "") !== workflowId) continue;
          foundInPack = true;
          push(f.command);
          const aliases: unknown[] = Array.isArray(f.aliases) ? f.aliases : [];
          for (const a of aliases) if (typeof a === "string") push(a);
        }
      }
    } catch {
      // 磁盘 manifest 读失败 → 继续走 workflowId 兜底
    }
  }
  if (!foundInPack) {
    // Fallback: no manifest facade for this workflow — use workflowId as the command
    // (same as ensurePackManifest auto-generates). Invalid workflowId chars are skipped.
    push(workflowId);
  }
  return collected;
}

/** Test-only export of collectDesiredFacadeCommands (see tests/facade-binding-deploy.test.ts). */
export const __collectDesiredFacadeCommandsForTest = collectDesiredFacadeCommands;

/** Get accessible workflows from ClawWeb API. */
async function getAccessibleWorkflows(deps: VersionCommandDeps): Promise<string[]> {
  if (!deps.clawWebBaseUrl) return [];
  try {
    const resp = await fetch(
      `${deps.clawWebBaseUrl}/api/workflows?botOwnerId=${deps.ownerId ?? ""}${deps.botId ? `&botId=${deps.botId}` : ""}`,
    );
    if (!resp.ok) return [];
    const data = await resp.json();
    const list = Array.isArray(data) ? data : (data as any).workflows ?? [];
    return list.map((w: any) => w.workflow_id ?? w.workflowId);
  } catch {
    return [];
  }
}

/** Create a DeployHistoryApiRepository if config is available. */
function createDeployHistoryApiRepo(deps: VersionCommandDeps): DeployHistoryApiRepository | null {
  if (!deps.clawWebBaseUrl || !deps.signatureKey) {
    console.warn(`[deploy-history] Cannot create ApiClient: clawWebBaseUrl=${deps.clawWebBaseUrl ? "✓" : "MISSING"}, signatureKey=${deps.signatureKey ? "✓" : "MISSING"}`);
    return null;
  }
  const client = new ApiClient({ baseUrl: deps.clawWebBaseUrl, privateKeyB64: deps.signatureKey });
  return new DeployHistoryApiRepository(client);
}

/**
 * Fetch the current spec for a workflow from the DB (workflow_specs table).
 * Uses the external API (GET /api/workflows/:workflowId) which returns the
 * actual spec stored in the database, not a deploy_history snapshot.
 *
 * This is the authoritative source for "what the DB currently stores" and
 * should be used for sync comparisons against local YAML.
 */
async function fetchDbSpec(deps: VersionCommandDeps, workflowId: string): Promise<Record<string, unknown> | null> {
  if (!deps.clawWebBaseUrl) return null;
  try {
    const resp = await fetch(`${deps.clawWebBaseUrl}/api/workflows/${encodeURIComponent(workflowId)}`);
    if (!resp.ok) return null;
    const data = await resp.json() as Record<string, unknown>;
    if (!data || !data.id) return null;
    return data;
  } catch {
    return null;
  }
}

/**
 * Fetch updatedAt for a workflow from the list API as fallback.
 * The detail API may not include updatedAt if ClawWeb hasn't deployed
 * the gmt_modified patch yet. The list API always has it.
 */
async function fetchUpdatedAtFromList(
  deps: VersionCommandDeps,
  workflowId: string,
): Promise<unknown> {
  if (!deps.clawWebBaseUrl) return undefined;
  try {
    const url = `${deps.clawWebBaseUrl}/api/workflows?botOwnerId=${deps.ownerId ?? ""}${deps.botId ? `&botId=${deps.botId}` : ""}&pageSize=500`;
    const resp = await fetch(url);
    if (!resp.ok) return undefined;
    const raw = await resp.json();
    const list: Array<Record<string, unknown>> = Array.isArray(raw) ? raw : (raw as any).data ?? (raw as any).workflows ?? [];
    const match = list.find((w) =>
      (w.workflowId ?? w.workflow_id) === workflowId
    );
    return match ? (match.updatedAt ?? match.gmt_modified) : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Compute sync status for a single workflow by comparing local spec vs DB current spec.
 *
 * Compares the actual DB content (workflow_specs table) against the local YAML,
 * NOT the deploy_history snapshot. This gives an accurate picture of whether
 * the local file matches what the DB currently stores.
 *
 * deploy_history is used only for version number and deploy tag display.
 *
 * Status labels:
 * - `local_only` — local spec exists, no DB spec found
 * - `db_only`    — DB spec exists, no local spec
 * - `synced ✅`  — both exist, normalized content is identical
 * - `differs`    — both exist, normalized content differs (user decides direction)
 *
 * @returns Object with syncStatus label, deployed version from DB, and action hint.
 */
// ── Timestamp Comparison ──

/**
 * Which side has newer content for a workflow.
 *
 * Used by deploy (to block stale overwrites), status (to show direction),
 * and the background sync-poll.
 */
export type TimestampDirection =
  | "db_newer"     // DB was updated after local file — deploy would overwrite newer DB changes
  | "local_newer"  // Local file is newer than DB — safe to deploy
  | "same_age"     // Timestamps are equal (within 1s tolerance)
  | "unknown";     // One or both timestamps unavailable

export type TimestampComparison = {
  direction: TimestampDirection;
  dbUpdatedAt?: string;   // ISO 8601 string from DB
  localMtime?: string;    // ISO 8601 string from filesystem
};

/**
 * Compare DB updatedAt vs local YAML file mtime to determine which side is newer.
 *
 * Reuses the same logic as sync-poll's detectClawWebChanges but in a form
 * reusable by interactive commands (deploy, status, pull).
 */
/**
 * Parse a MySQL DATETIME string ("2026-07-18 16:16:17") or ISO string
 * into epoch milliseconds. Returns 0 if unparseable.
 * MySQL DATETIME has no timezone — treat as UTC (ClawWeb containers run UTC).
 */
function parseDbTimestamp(value: unknown): number {
  if (value == null) return 0;
  if (typeof value === "number") {
    // Epoch seconds (< 1e12 ≈ before 2001 in ms) vs epoch milliseconds
    return value > 1e12 ? value : value * 1000;
  }
  const s = String(value).trim();
  if (!s) return 0;
  // MySQL DATETIME: "2026-07-18 16:16:17" → "2026-07-18T16:16:17Z"
  const isoLike = s.includes("T") ? s : s.replace(" ", "T") + "Z";
  const ms = new Date(isoLike).getTime();
  if (isNaN(ms)) return 0;
  return ms;
}

/** Format epoch ms to local time string with timezone offset. */
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

/** Sanity check: reject timestamps outside year 2000–2100. */
function isPlausibleTimestamp(ms: number): boolean {
  if (ms <= 0 || isNaN(ms)) return false;
  const year = new Date(ms).getFullYear();
  return year >= 2000 && year <= 2100;
}

async function compareTimestamps(
  deps: VersionCommandDeps,
  workflowId: string,
  dbSpec?: Record<string, unknown> | null,
): Promise<TimestampComparison> {
  // DB timestamp — updatedAt can be:
  //   epoch ms number (from ClawWeb detail API) — preferred, no parsing ambiguity
  //   ISO string (fallback) — parsed via Date
  //   MySQL DATETIME string — "2026-07-18 16:16:17" (treated as UTC)
  //   epoch seconds number (from older API) — detected by value range
  if (!dbSpec) {
    dbSpec = await fetchDbSpec(deps, workflowId);
  }
  let dbUpdatedMs = 0;
  let updatedAt: unknown = dbSpec ? (dbSpec as Record<string, unknown>).updatedAt ?? (dbSpec as Record<string, unknown>).gmt_modified : undefined;
  // Fallback: if detail API doesn't include updatedAt, try the list API.
  if (updatedAt == null) {
    updatedAt = await fetchUpdatedAtFromList(deps, workflowId);
  }
  dbUpdatedMs = parseDbTimestamp(updatedAt);
  // Sanity guard: if the parsed timestamp is absurd (year outside 2000–2100),
  // treat as unknown instead of producing misleading output like "+058514".
  if (!isPlausibleTimestamp(dbUpdatedMs)) {
    dbUpdatedMs = 0;
  }

  // Local mtime
  const resolvedWf = (deps.resolvedWorkflows ?? []).find((w) => w.id === workflowId);
  const absPath = resolvedWf?.absolutePath;
  let localMtimeMs = 0;
  if (absPath) {
    try {
      localMtimeMs = fsSync.statSync(absPath).mtimeMs;
    } catch {
      localMtimeMs = 0;
    }
  }

  const dbTime = dbUpdatedMs > 0 ? formatLocalTime(dbUpdatedMs) : undefined;
  const localTime = localMtimeMs > 0 ? formatLocalTime(localMtimeMs) : undefined;

  if (dbUpdatedMs > 0 && localMtimeMs > 0) {
    // Tolerance: timestamps from different clocks may differ by up to 1s
    const diff = Math.abs(dbUpdatedMs - localMtimeMs);
    if (diff < 1000) {
      return { direction: "same_age", dbUpdatedAt: dbTime, localMtime: localTime };
    }
    if (dbUpdatedMs > localMtimeMs) {
      return { direction: "db_newer", dbUpdatedAt: dbTime, localMtime: localTime };
    }
    return { direction: "local_newer", dbUpdatedAt: dbTime, localMtime: localTime };
  }

  return { direction: "unknown", dbUpdatedAt: dbTime, localMtime: localTime };
}

export async function computeSyncStatus(
  deps: { clawWebBaseUrl?: string; signatureKey?: string; resolvedWorkflows?: any[]; resolvedPacks?: any[] },
  workflowId: string,
): Promise<{ syncStatus: string; deployedVersion: number | undefined; action: string; newerSide?: TimestampDirection; dbUpdatedAt?: string; localMtime?: string }> {
  const packId = resolvePackId(deps as VersionCommandDeps, workflowId);
  const apiRepo = createDeployHistoryApiRepo(deps as VersionCommandDeps);

  const latestDeploy = apiRepo ? await apiRepo.getLatestDeploy(packId, workflowId) : undefined;
  const deployedVersion = latestDeploy?.version;

  const resolvedWf = (deps.resolvedWorkflows ?? []).find((w) => w.id === workflowId);
  const hasLocal = !!resolvedWf;

  // Fetch the DB's CURRENT spec (workflow_specs table), not deploy_history snapshot
  const dbSpec = await fetchDbSpec(deps as VersionCommandDeps, workflowId);
  const hasDB = !!dbSpec;

  // ── One side missing ──
  if (!hasDB && hasLocal) {
    return { syncStatus: "local_only", deployedVersion: undefined, action: "" };
  }
  if (hasDB && !hasLocal) {
    return { syncStatus: "db_only", deployedVersion, action: "→ pull / migration" };
  }
  if (!hasDB && !hasLocal) {
    return { syncStatus: "(unknown)", deployedVersion: undefined, action: "" };
  }

  // ── Both exist: compare normalized content ──
  // Read the raw spec from the YAML file on disk, bypassing normalizeWorkflowSpec.
  // This is critical because normalizeWorkflowSpec adds default values (triggerRule,
  // retry, outputContract, etc.) that may not exist in the DB's specJson, causing
  // permanent false-positive diffs. By reading the raw YAML (the same content that
  // migration wrote from DB), we compare the actual disk content vs DB current spec.
  const localSpec = await readRawLocalSpec(resolvedWf);

  const dbNorm = normalizeSpec(dbSpec!);
  const localNorm = normalizeSpec(localSpec);

  if (dbNorm === localNorm) {
    return { syncStatus: "synced ✅", deployedVersion, action: "" };
  }

  // Content differs — determine which side is newer
  const ts = await compareTimestamps(deps as VersionCommandDeps, workflowId, dbSpec);
  return {
    syncStatus: "differs",
    deployedVersion,
    action: ts.direction === "db_newer" ? "→ pull first or use --force" : ts.direction === "local_newer" ? "→ deploy to sync" : "",
    newerSide: ts.direction,
    dbUpdatedAt: ts.dbUpdatedAt,
    localMtime: ts.localMtime,
  };
}

/**
 * Read the raw spec from the YAML file on disk, bypassing normalizeWorkflowSpec.
 *
 * When migration/pull writes a spec from DB to local YAML, the content exactly
 * matches what the DB stores. But when the pack resolver loads the YAML, it runs
 * normalizeWorkflowSpec which adds default values (triggerRule, retry, outputContract,
 * etc.) that don't exist in the DB version. Comparing these two produces false-positive
 * diffs that can never be resolved.
 *
 * By reading the raw YAML and parsing it as plain JSON, we compare the actual
 * disk content against the deploy_history snapshot — a true apples-to-apples comparison.
 *
 * Falls back to the in-memory resolvedWf.spec if the file can't be read.
 */
async function readRawLocalSpec(resolvedWf: ResolvedWorkflow | undefined): Promise<Record<string, unknown>> {
  const absPath = resolvedWf?.absolutePath;
  if (!absPath) {
    return (resolvedWf?.spec ?? {}) as Record<string, unknown>;
  }
  try {
    const content = await fs.readFile(absPath, "utf-8");
    const parsed = yaml.load(content) as Record<string, unknown>;
    return parsed ?? {};
  } catch {
    // Fallback to in-memory spec if file read fails
    return (resolvedWf?.spec ?? {}) as Record<string, unknown>;
  }
}

/**
 * deploy 回写对账:对比 raw(用户真实 YAML,全量)与 savedSpec(ClawWeb 回写),
 * 返回 raw 显式写过、但 savedSpec 中消失的"叶子字段路径"(如 nodes[0].executor.saveAs)。
 *
 * 非空意味着 ClawWeb save API 的 normalize 白名单剥了字段——deploy 表面成功,
 * 但 DB/运行缺该字段。本函数把这种"静默成功"变成显式提示(闸 5 兜底)。
 *
 * 精度(不误报):
 *   - 引擎默认字段(triggerRule/retry/outputContract/outputSchema/businessStatus/
 *     alerting)不计——它们由 normalize 注入,raw 没写而 saved 有属正常,不算"丢"。
 *     但 saveAs 不在豁免列——saveAs 是用户显式数据映射,丢了就是真丢。
 *   - 只比 nodes 数组(raw 显式写的字段在 saved 是否消失);其他顶层(version/facade
 *     等)由 deploy 流程单独管理,不在此对账。
 *   - raw 有值、saved 也有值(即便不同)不报——内容差异由 status --diff 管,
 *     本对账只管"字段整个没了"。
 */
export function computeDroppedFields(
  raw: Record<string, unknown>,
  saved: Record<string, unknown>,
): string[] {
  const dropped: string[] = [];
  const ENGINE_DEFAULT_KEYS = new Set([
    "triggerRule", "retry", "outputContract", "outputSchema", "businessStatus", "alerting",
    "validationTemplateId", "validationMinScore", "progressMessage",
  ]);
  const isRecord = (v: unknown): v is Record<string, unknown> =>
    typeof v === "object" && v !== null && !Array.isArray(v);

  const walk = (rv: unknown, sv: unknown, path: string): void => {
    if (Array.isArray(rv) && Array.isArray(sv)) {
      rv.forEach((item, i) => walk(item, (sv as unknown[])[i], `${path}[${i}]`));
      return;
    }
    if (isRecord(rv) && isRecord(sv)) {
      for (const k of Object.keys(rv)) {
        if (rv[k] === undefined) continue;
        if (ENGINE_DEFAULT_KEYS.has(k)) continue; // 引擎默认字段,raw 没写 saved 有属正常
        if (!(k in sv) || sv[k] === undefined) {
          dropped.push(path ? `${path}.${k}` : k);
        } else {
          walk(rv[k], sv[k], path ? `${path}.${k}` : k);
        }
      }
    }
    // 叶子(原始值/数组元素):raw 有 saved 也有,不报
  };

  walk(raw.nodes ?? [], saved.nodes ?? [], "nodes");
  return dropped;
}

/**
 * Common "subtractive" normalization applied before any spec comparison.
 *
 * Strips, in order: undefined values → editor/UI decoration (`_`-prefixed) →
 * engine-injected default-valued fields (`triggerRule`/`retry` that equal the
 * engine defaults produced by `normalizeNode`).
 *
 * The engine-defaults pass is what makes DB-read specs (defaults back-filled by
 * the GET path) comparable to raw local YAMLs (no defaults): both sides converge
 * to "only fields the user explicitly wrote with non-default values".
 *
 * `version`/`facade`/`updatedAt` deletion is left to callers (it is a top-level
 * concern, not recursive).
 */
function stripForCompare<T>(spec: T): T {
  return stripEngineDefaults(stripUiFields(stripUndefinedDeep(spec)));
}

/**
 * Normalize a workflow spec to a canonical JSON string for comparison.
 *
 * Strips fields that are not part of the canonical spec content:
 * - `version` — unreliable, varies per source
 * - `facade`  — ClawWeb decoration (slash command binding), injected by GET endpoint
 *
 * Also recursively sorts object keys and strips undefined values.
 */
function normalizeSpec(spec: Record<string, unknown>): string {
  const clone = sortKeysDeep(stripForCompare({ ...spec }));
  delete (clone as Record<string, unknown>).version;
  delete (clone as Record<string, unknown>).facade;
  delete (clone as Record<string, unknown>).updatedAt;     // DB metadata, not part of spec content
  return JSON.stringify(clone);
}

/**
 * Recursively strip editor/UI decoration fields.
 * ClawWeb's visual editor injects fields like `_x`, `_y` (canvas coordinates)
 * and other underscore-prefixed UI state into the spec. These are not part of
 * the workflow semantics and should be ignored during spec comparison.
 */
function stripUiFields<T>(obj: T): T {
  if (Array.isArray(obj)) {
    return obj.map(stripUiFields) as T;
  }
  if (obj !== null && typeof obj === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(obj as Record<string, unknown>)) {
      if (key.startsWith("_")) continue;
      result[key] = stripUiFields(val);
    }
    return result as T;
  }
  return obj;
}

/**
 * Recursively sort all keys in an object (and nested objects/arrays).
 * Returns a new object — does not mutate the input.
 */
function sortKeysDeep<T>(obj: T): T {
  if (Array.isArray(obj)) {
    return obj.map(sortKeysDeep) as T;
  }
  if (obj !== null && typeof obj === "object") {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(obj as Record<string, unknown>).sort()) {
      sorted[key] = sortKeysDeep((obj as Record<string, unknown>)[key]);
    }
    return sorted as T;
  }
  return obj;
}

/**
 * Deeply strip keys whose values are `undefined`.
 * `JSON.stringify` silently drops top-level keys with `undefined` values,
 * but converts `undefined` inside arrays to `null`. This function ensures
 * consistent handling by removing `undefined` values everywhere before
 * stringification.
 */
function stripUndefinedDeep<T>(obj: T): T {
  if (Array.isArray(obj)) {
    return obj.map(stripUndefinedDeep) as T;
  }
  if (obj !== null && typeof obj === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(obj as Record<string, unknown>)) {
      if (val === undefined) continue;
      result[key] = stripUndefinedDeep(val);
    }
    return result as T;
  }
  return obj;
}

/** Serialize specObj to JSON without the version/updatedAt fields, with undefined and UI decoration fields stripped. */
function serializeSpecWithoutVersion(specObj: Record<string, unknown>): string {
  const clone = stripUiFields(stripUndefinedDeep({ ...specObj }));
  delete (clone as Record<string, unknown>).version;
  delete (clone as Record<string, unknown>).updatedAt;
  return JSON.stringify(clone);
}

/**
 * Full spec diff for status --diff and detail.
 *
 * Compares normalized JSON content field by field. When specs are identical,
 * returns a short message. When they differ, shows:
 *   - Which top-level keys differ (added/removed/changed)
 *   - The actual values that differ (truncated for readability)
 *   - Node-level detail with individual field diffs
 */
export function computeSpecDiff(
  deployed: Record<string, unknown>,
  local: Record<string, unknown>,
): string {
  const deployedNorm = normalizeSpec(deployed);
  const localNorm = normalizeSpec(local);

  if (deployedNorm === localNorm) {
    return "(spec content identical)";
  }

  const diffs: string[] = [];

  // ── Top-level key comparison ──
  // Use normalized versions (undefined stripped, UI fields stripped, engine
  // defaults stripped, keys sorted) for comparison so default-valued fields
  // injected by the DB read path don't surface as false diffs.
  const deployedClean = sortKeysDeep(stripForCompare({ ...deployed }));
  const localClean = sortKeysDeep(stripForCompare({ ...local }));
  delete (deployedClean as Record<string, unknown>).version;
  delete (deployedClean as Record<string, unknown>).facade;
  delete (deployedClean as Record<string, unknown>).updatedAt;
  delete (localClean as Record<string, unknown>).version;
  delete (localClean as Record<string, unknown>).facade;

  const deployedKeys = new Set(Object.keys(deployedClean as Record<string, unknown>));
  const localKeys = new Set(Object.keys(localClean as Record<string, unknown>));
  const allKeys = [...new Set([...deployedKeys, ...localKeys])].sort();

  for (const key of allKeys) {
    if (key === "version" || key === "facade") continue;

    const inDeployed = deployedKeys.has(key);
    const inLocal = localKeys.has(key);

    if (!inDeployed) {
      const val = JSON.stringify((localClean as Record<string, unknown>)[key]);
      diffs.push(`+ ${key}: local only = ${truncate(val, 200)}`);
      continue;
    }
    if (!inLocal) {
      const val = JSON.stringify((deployedClean as Record<string, unknown>)[key]);
      diffs.push(`- ${key}: deployed only = ${truncate(val, 200)}`);
      continue;
    }

    // Both exist — compare normalized values
    const deployedVal = JSON.stringify(sortKeysDeep(stripUndefinedDeep((deployedClean as Record<string, unknown>)[key])));
    const localVal = JSON.stringify(sortKeysDeep(stripUndefinedDeep((localClean as Record<string, unknown>)[key])));
    if (deployedVal !== localVal) {
      diffs.push(`≠ ${key}: differs`);
      diffs.push(`    deployed: ${truncate(deployedVal, 300)}`);
      diffs.push(`    local:    ${truncate(localVal, 300)}`);
    }
  }

  // ── Node-level detail: diff individual nodes by id ──
  const deployedNodes = ((deployedClean.nodes as Array<Record<string, unknown>>) ?? []);
  const localNodes = ((localClean.nodes as Array<Record<string, unknown>>) ?? []);
  const deployedNodeMap = new Map(deployedNodes.map((n) => [n.id as string, stripUiFields(n)]));
  const localNodeMap = new Map(localNodes.map((n) => [n.id as string, stripUiFields(n)]));
  const allNodeIds = [...new Set([...deployedNodeMap.keys(), ...localNodeMap.keys()])].sort();

  let hasNodeDiffs = false;
  for (const nodeId of allNodeIds) {
    const inD = deployedNodeMap.has(nodeId);
    const inL = localNodeMap.has(nodeId);
    if (!inD) {
      diffs.push(`  + node[${nodeId}]: local only`);
      hasNodeDiffs = true;
      continue;
    }
    if (!inL) {
      diffs.push(`  - node[${nodeId}]: deployed only`);
      hasNodeDiffs = true;
      continue;
    }

    const dNorm = JSON.stringify(sortKeysDeep(stripUndefinedDeep(deployedNodeMap.get(nodeId))));
    const lNorm = JSON.stringify(sortKeysDeep(stripUndefinedDeep(localNodeMap.get(nodeId))));
    if (dNorm !== lNorm) {
      hasNodeDiffs = true;
      // Show which node-level fields differ
      const dNode = sortKeysDeep(stripUndefinedDeep(deployedNodeMap.get(nodeId)!)) as Record<string, unknown>;
      const lNode = sortKeysDeep(stripUndefinedDeep(localNodeMap.get(nodeId)!)) as Record<string, unknown>;
      const dKeys = new Set(Object.keys(dNode));
      const lKeys = new Set(Object.keys(lNode));
      const nodeDiffs: string[] = [];

      for (const nk of [...new Set([...dKeys, ...lKeys])].sort()) {
        if (!dKeys.has(nk)) { nodeDiffs.push(`+${nk}`); continue; }
        if (!lKeys.has(nk)) { nodeDiffs.push(`-${nk}`); continue; }
        const dv = JSON.stringify(dNode[nk]);
        const lv = JSON.stringify(lNode[nk]);
        if (dv !== lv) {
          nodeDiffs.push(`≠${nk}`);
        }
      }
      diffs.push(`  ≠ node[${nodeId}]: ${nodeDiffs.join(" ")}`);
    }
  }

  return diffs.length > 0 ? diffs.join("\n") : "(identical)";
}

/** Truncate a string to maxLength, appending "…" if truncated. */
function truncate(s: string, maxLength: number): string {
  if (s.length <= maxLength) return s;
  return s.slice(0, maxLength) + "…";
}

// ── Command Handlers ──

export async function handleDeploy(
  deps: VersionCommandDeps,
  workflowId: string,
  options?: {
    file?: string; force?: boolean; note?: string;
    localAuthoritative?: boolean;
    specObj?: Record<string, unknown>;   // install-pack:pack 已整包装好,直接传 spec,跳过 --file/resolvedWorkflows 解析
    packId?: string;                     // install-pack:packId 取自源 manifest,不靠字段推
  },
): Promise<string> {
  // 1. Permission check (deploy uses can_edit)
  const permResult = await checkEditPermission(deps, workflowId);
  if (!permResult.allowed) return `❌ ${permResult.reason ?? `无权限部署 workflow "${workflowId}"`}`;
  const warnings: string[] = [];

  if (!deps.packsRoot) return `❌ packsRoot not configured`;

  const packsDir = deps.packsRoot;
  let specObj: Record<string, unknown>;
  let packId: string;

  // 2. Resolve spec: install-pack pass-through(已整包装好)、--file、或 resolved workflows
  if (options?.specObj && options?.packId) {
    specObj = options.specObj;
    packId = options.packId;
    // install-pack 路径:pack 已整包复制到 packsRoot,跳过 writeSpecToYaml/ensurePackManifest
  } else if (options?.file) {
    const specPath = path.resolve(options.file);
    if (!fsSync.existsSync(specPath)) {
      return `❌ 文件不存在: ${specPath}`;
    }
    try {
      const content = await fs.readFile(specPath, "utf-8");
      specObj = yaml.load(content) as Record<string, unknown>;
    } catch (err) {
      return `❌ YAML 解析失败: ${err instanceof Error ? err.message : err}`;
    }
    if (specObj.id && specObj.id !== workflowId) {
      warnings.push(`⚠️ 文件中 workflow id="${specObj.id}" 与参数 "${workflowId}" 不匹配，以参数为准`);
      specObj.id = workflowId;
    }
    packId = (specObj as Record<string, unknown>).packId as string ?? (specObj.id as string) ?? workflowId;
    await writeSpecToYaml(packsDir, packId, workflowId, specObj);
    await ensurePackManifest(packsDir, packId);
    warnings.push(`📦 已复制到 ${packsDir}/${packId}/workflows/${workflowId}.yaml`);
  } else {
    const resolvedWf = (deps.resolvedWorkflows ?? []).find((w) => w.id === workflowId);
    if (!resolvedWf) {
      const fw = findFailedWorkflow(deps.failedWorkflows, workflowId);
      if (fw) {
        const lines = [
          `❌ Workflow "${workflowId}" 加载失败(load_error),无法部署。`,
          `  原因: ${fw.error}`,
          `  pack: ${fw.packId}${fw.packVersion ? `@${fw.packVersion}` : ""}`,
          `  文件: ${fw.absolutePath}`,
          ``,
          `  若为 id mismatch:三者须一致——文件名 = manifest workflows[].id = YAML 内 id:,改为 "${fw.id}" 或重命名文件/改 manifest。`,
          `  若为 YAML 解析/结构错:先用 \`validate --file ${fw.absolutePath}\` 修对,再 deploy。`,
        ];
        // D2:失败首屏一次性列出本次 YAML 全节点可疑清单(零级联,scan 失败不阻塞)
        const scan = quickScanSpecFile(fw.absolutePath);
        if (scan.ok && scan.findings.length > 0) {
          lines.push(``, `📋 本次 YAML 同时检测到的其它问题(${scan.findings.length} 处,改主因时可一并处理):`);
          for (const f of scan.findings) {
            lines.push(`  • ${f.node}${f.phase ? `(${f.phase})` : ""} [${f.severity}]: ${f.message}`);
          }
        }
        return lines.join("\n");
      }
      return `❌ Workflow "${workflowId}" 未在本地 packs 中找到。使用 --file <path> 指定 YAML 文件路径，或先 pull。`;
    }
    // 保存=运行 完全一致:deploy 用 normalize 结果(resolvedWf.spec)——和引擎运行同源。
    // 前提:normalize 全量不丢字段(normalizeNode/normalizeWorkflowSpec 末尾的 deepPreserveUnknown),
    // 否则白名单重建会吃 input.defaults/optionalParams 等,保存和运行都丢。
    specObj = resolvedWf.spec as unknown as Record<string, unknown>;
    packId = resolvePackId(deps, workflowId);
    await writeSpecToYaml(packsDir, packId, workflowId, specObj);
    await ensurePackManifest(packsDir, packId);
  }

  // 2b. Diff preview + timestamp check: prevent stale local from overwriting newer DB.
  try {
    const dbSpec = await fetchDbSpec(deps, workflowId);
    if (dbSpec && dbSpec.id) {
      const resolvedWf = (deps.resolvedWorkflows ?? []).find((w) => w.id === workflowId);
      const localSpec = resolvedWf ? await readRawLocalSpec(resolvedWf) : specObj;
      try {
        const dbNorm = normalizeSpec(dbSpec);
        const localNorm = normalizeSpec(localSpec);
        if (dbNorm !== localNorm) {
          // Content differs — check which side is newer
          const ts = await compareTimestamps(deps, workflowId, dbSpec);
          const diffSummary = computeSpecDiff(dbSpec, localSpec).split("\n").slice(0, 3).join("\n");
          const dbTimeHint = ts.dbUpdatedAt ? `DB更新: ${ts.dbUpdatedAt}` : "DB更新时间: 未知";
          const localTimeHint = ts.localMtime ? `本地修改: ${ts.localMtime}` : "本地修改时间: 未知";

          if (ts.direction === "db_newer" && !options?.force) {
            // DB is newer and user didn't explicitly force — block deploy
            return [
              `⛔ 部署被阻止: DB 比本地更新，部署将覆盖 DB 上的新修改。`,
              ``,
              `  ${dbTimeHint}`,
              `  ${localTimeHint}`,
              ``,
              `  DB 与本地的差异:`,
              `  ${diffSummary}`,
              ``,
              `建议操作:`,
              `  1. 先 pull 同步 DB 最新内容:  /workflow pull ${workflowId}`,
              `  2. 确认后重新 deploy`,
              `  3. 如确认要用本地覆盖 DB:  /workflow deploy ${workflowId} --force`,
            ].join("\n");
          } else if (ts.direction === "db_newer" && options?.force) {
            warnings.push(`⚠️ DB 比本地更新，--force 强制覆盖 DB 修改:`);
            warnings.push(`  ${dbTimeHint}`);
            warnings.push(`  ${localTimeHint}`);
            warnings.push(`📋 DB 与本地的差异:`);
            warnings.push(`  ${diffSummary}`);
          } else {
            // Local is newer, same age, or unknown — safe to proceed
            warnings.push(`📋 DB 与本地有差异，将用本地覆盖 DB:`);
            warnings.push(`  ${localTimeHint}`);
            warnings.push(`  ${dbTimeHint}`);
            warnings.push(`  ${diffSummary}`);
          }
        } else {
          warnings.push(`📋 DB 与本地一致`);
        }
      } catch {
        // DB spec can't be normalized — corrupted, just note it
        warnings.push(`📋 DB 记录异常，将用本地覆盖`);
      }
    }
  } catch {
    // fetchDbSpec failed — non-fatal
  }

  // 2c. Facade binding conflict check — BEFORE writing to DB. If any desired
  // command is already bound to a *different* workflow, refuse the deploy up
  // front so we don't leave a half-written spec in the DB. The actual binding
  // write (upsert + orphan cleanup) happens in step 3c after the spec is saved.
  const desiredFacadeCommands = collectDesiredFacadeCommands(deps, workflowId, packId, warnings);
  if (deps.facadeBindingRepo && desiredFacadeCommands.length > 0) {
    for (const cmd of desiredFacadeCommands) {
      let existing: { workflow_id?: string } | null = null;
      try {
        existing = await deps.facadeBindingRepo.findByCommand(cmd);
      } catch (err) {
        warnings.push(`⚠️ facade binding 冲突检查失败 (${cmd}): ${err instanceof Error ? err.message : err}`);
        continue;
      }
      if (existing && existing.workflow_id && existing.workflow_id !== workflowId) {
        return [
          `❌ facade command "${cmd}" 已被 workflow "${existing.workflow_id}" 使用,无法部署 "${workflowId}"。`,
          ``,
          `请改用其它 command,或先调整 "${existing.workflow_id}" 的 facade 后重试。`,
        ].join("\n");
      }
    }
  }

  // 3. DB deploy via ClawWeb API
  let specJson = serializeSpecWithoutVersion(specObj);
  let savedSpec: Record<string, unknown>;
  try {
    savedSpec = await deployToClawWeb(deps, workflowId, packId, specObj);
  } catch (err) {
    return `❌ DB 部署失败: ${err instanceof Error ? err.message : err}`;
  }

  // 3b. Re-write local YAML from DB-stored spec so local matches DB exactly.
  // The save API normalizes spec differently from ClawMind (adds _x/_y, optionalParams, etc).
  // Writing the DB version back ensures sync status = synced after deploy.
  try {
    // 不删 version —— 保存全量(deepPreserveUnknown 已补 version,clawweb save 透传保留)。
    // deploy_history 用 serializeSpecWithoutVersion,version 在那独立管理。
    delete (savedSpec as Record<string, unknown>).facade;
    await writeSpecToYaml(packsDir, packId, workflowId, savedSpec);
    // Update specJson to match DB-stored spec for deploy_history consistency
    specJson = serializeSpecWithoutVersion(savedSpec);
  } catch (err) {
    warnings.push(`⚠️ 部署后同步本地文件失败: ${err instanceof Error ? err.message : err}`);
  }

  // 3b'. 回写对账(闸 5 兜底):raw 用户真实 YAML 显式写过的字段,若未进 savedSpec,
  // 说明 ClawWeb save API 的 normalize 白名单剥了字段——deploy 表面成功但 DB/运行缺字段。
  // 静默零容忍:droppedFields 非空时显式降级提示,不得只报"部署成功"。
  // 引擎默认字段(triggerRule/retry 等)不计入,见 computeDroppedFields 注释。
  try {
    const resolvedWf = (deps.resolvedWorkflows ?? []).find((w) => w.id === workflowId);
    const rawLocal = resolvedWf ? await readRawLocalSpec(resolvedWf) : specObj;
    const dropped = computeDroppedFields(
      rawLocal as Record<string, unknown>,
      savedSpec as Record<string, unknown>,
    );
    if (dropped.length > 0) {
      warnings.push(`⚠️ 以下本地字段未进入 DB(被对端 normalize 剥离),运行时不会生效:`);
      for (const d of dropped) warnings.push(`  - ${d}`);
      warnings.push(`如需这些字段生效,请联系 ClawWeb 侧 normalize 支持,或改用引擎原生路径(如 {{nodeOutput.x.field}})。`);
    }
  } catch (err) {
    warnings.push(`⚠️ deploy 回写对账失败(非致命): ${err instanceof Error ? err.message : err}`);
  }

  // 3c. Write facade_bindings (spec is now safely in the DB). Re-deploy semantics:
  //   - same workflow, same command → repo.upsert UPDATEs remark/pack_id in place
  //     (workflow_id unchanged) — the "再次部署更新逻辑" the user asked to confirm.
  //   - command removed from manifest since last deploy → THIS workflow's stale
  //     binding is deleted (guarded by a hard workflow_id ownership check).
  //   - new command → INSERT. Conflicts with *other* workflows were caught in 2c.
  // Absent facadeBindingRepo → skip with a warning (deploy still succeeds).
  if (!deps.facadeBindingRepo) {
    warnings.push("⚠️ facadeBindingRepo 未配置,跳过 facade binding 写入");
  } else if (desiredFacadeCommands.length === 0) {
    warnings.push("⚠️ 未收集到 facade command,跳过 facade binding 写入");
  } else {
    const desired = new Set(desiredFacadeCommands);
    // Upsert desired commands (idempotent for same-workflow re-deploy):
    // same workflow + same command → repo.upsert UPDATEs in place; new command →
    // INSERT; conflicts with *other* workflows were rejected up front in step 2c.
    for (const cmd of desiredFacadeCommands) {
      try {
        await deps.facadeBindingRepo.upsert({ command: cmd, workflow_id: workflowId, pack_id: packId, remark: undefined });
      } catch (err) {
        warnings.push(`⚠️ facade binding 写入失败 (${cmd}): ${err instanceof Error ? err.message : err}`);
      }
    }

    // Remove THIS workflow's stale bindings — commands it used to own that are no
    // longer in the manifest (typically after a manifest facade rename). Without
    // this, a rename leaves both the old and new command bound, so the old slash
    // command stays live as a hidden alias.
    //
    // ⚠️ SAFETY: we must NEVER delete a binding owned by another workflow. The
    // protection is a hard ownership check right before each delete — it does NOT
    // rely on the query being server-scoped. (An earlier version trusted
    // findByWorkflowId to return only rows for its workflowId; in API mode that
    // server filter is unreliable and once returned the whole table, so the
    // cleanup deleted every other workflow's binding and emptied facade_bindings.
    // The hard `row.workflow_id !== workflowId → skip` guard below makes that
    // impossible regardless of what the query returns.)
    //   - We prefer findByWorkflowId (lightweight, ~rows for one workflow) over
    //     listAll (full table scan) for cost; the guard, not the query selection,
    //     is what keeps deletion safe.
    try {
      const candidates = await deps.facadeBindingRepo.findByWorkflowId(workflowId);
      for (const row of candidates) {
        // Hard ownership guard — the only thing standing between us and a repeat
        // of the table-wipe. Skip (do NOT delete) anything not owned by us.
        if (row.workflow_id !== workflowId) {
          console.warn("[deploy] facade findByWorkflowId returned a row owned by ANOTHER workflow — skipping (not deleted)", {
            workflowId, rowCommand: row.command, rowWorkflowId: row.workflow_id,
          });
          continue;
        }
        if (desired.has(row.command)) continue; // still wanted — keep
        await deps.facadeBindingRepo.deleteByCommand(row.command);
        warnings.push(`🧹 移除旧 facade binding "${row.command}"(已不在 manifest)`);
      }
    } catch (err) {
      // Cleanup is best-effort: never let it fail the deploy. The desired commands
      // above are already upserted, so the workflow is usable; a stale alias left
      // behind is harmless and can be cleaned manually.
      warnings.push(`⚠️ facade binding 旧命令清理失败(非致命): ${err instanceof Error ? err.message : err}`);
    }
  }

  // 4. Version number: always computed from deploy_history MAX(version) + 1.
  // workflow_specs.version is NOT a reliable source of truth — it may be pre-set by
  // migration/web-edit (e.g., version=1) while deploy_history has no matching v1 record,
  // causing ghost versions (user sees v2 when no v1 exists).
  const apiRepo = createDeployHistoryApiRepo(deps);
  let newVersion: number | undefined;
  if (apiRepo) {
    newVersion = (await apiRepo.getLatestVersion(packId, workflowId)) + 1;
  }

  // 5. Git commit + tag + push
  // Compute nextNum: must not collide with existing DB records.
  // Git tags might be out of sync with deploy_history (e.g., migration records
  // that wrote deploy_history without git tags). Take the max of both.
  const packDir = await ensureGitRepoForPack(
    packsDir, packId, workflowId, deps,
    options?.localAuthoritative ? { localAuthoritative: true } : undefined,
  );
  const gitNextNum = await gitModule.nextDeployNumber(packDir, workflowId);
  let nextNum = gitNextNum;
  if (apiRepo) {
    try {
      const dbMaxDeployNum = await apiRepo.getMaxDeployNumber(packId, workflowId);
      nextNum = Math.max(nextNum, dbMaxDeployNum + 1);
      if (nextNum > gitNextNum) {
        console.log(`[deploy] deploy_number adjusted: git=${gitNextNum} → db_max+1=${dbMaxDeployNum + 1}, using ${nextNum}`);
      }
    } catch { /* fallback to git-based number */ }
  }
  const branchName = `wf/${workflowId}`;
  let tagName: string;
  let hasNewCommit = false;
  // Commit (may be no-op if content unchanged after step 3b wrote DB spec back)
  try {
    await gitModule.addCommit(packDir, ["."], `[${deps.botId}/${deps.ownerId}] deploy ${workflowId} #${nextNum} v${newVersion ?? "?"}`);
    hasNewCommit = true;
  } catch (err) {
    const gitErr = err instanceof Error ? err.message : String(err);
    const isNoChange = gitErr.includes("nothing to commit") || gitErr.includes("no changes added") || gitErr.includes("nothing added");
    if (!isNoChange) {
      warnings.push(`⚠️ Git commit 失败: ${gitErr}`);
    }
    // nothing to commit → content unchanged, use existing tag
  }

  if (hasNewCommit) {
    // New content committed → create new tag for this deploy
    tagName = `deploy/${workflowId}/#${nextNum}`;
    try {
      await gitModule.createTag(packDir, tagName, {
        version: newVersion ?? 0,
        action: "deploy",
        workflowIds: [workflowId],
      });
    } catch (err) {
      const gitErr = err instanceof Error ? err.message : String(err);
      if (!gitErr.includes("already exists")) {
        warnings.push(`⚠️ Git tag 失败: ${gitErr}`);
      }
    }
    // Push new commit + tag
    try {
      await gitModule.pushBranch(packDir, branchName, tagName);
    } catch (err) {
      warnings.push(`⚠️ Git push 失败: ${err instanceof Error ? err.message : err}. commit/tag 已创建本地，但未推送远端。`);
    }
  } else {
    // No content change → reuse the latest existing deploy tag for this workflow
    const existingTags = await gitModule.listTags(packDir, `deploy/${workflowId}/#`);
    if (existingTags.length > 0) {
      tagName = existingTags[0].tagName;
      console.log(`[deploy] No content change, reusing existing tag: ${tagName}`);
    } else {
      // No existing tag either — create one as fallback
      tagName = `deploy/${workflowId}/#${nextNum}`;
      try {
        await gitModule.createTag(packDir, tagName, {
          version: newVersion ?? 0,
          action: "deploy",
          workflowIds: [workflowId],
        });
      } catch (err) {
        const gitErr = err instanceof Error ? err.message : String(err);
        if (!gitErr.includes("already exists")) {
          warnings.push(`⚠️ Git tag 失败: ${gitErr}`);
        }
      }
      try {
        await gitModule.pushBranch(packDir, branchName, tagName);
      } catch (err) {
        warnings.push(`⚠️ Git push 失败: ${err instanceof Error ? err.message : err}`);
      }
    }
  }

  // 6. Write to central workflow_deploy_history via API
  // The internal API now auto-retries on 409 (re-computes version from MAX(version)+1).
  // So this insert should almost always succeed.
  if (apiRepo && newVersion !== undefined) {
    try {
      await apiRepo.insert({
        packId, workflowId, deployNumber: nextNum, version: newVersion,
        tagName, action: "deploy", specJson,
        note: options?.note,
        botId: deps.botId, ownerId: deps.ownerId,
      });
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      const is409 = errMsg.includes("409") || errMsg.toLowerCase().includes("conflict");
      if (is409) {
        // 409 means the version already exists in deploy_history (likely from save API's write).
        // This is not a critical error — the deploy itself succeeded, spec is saved.
        // Re-read the actual version from workflow_specs for display.
        console.warn(`[deploy] deploy_history insert 409 for ${workflowId} v${newVersion} — version already recorded, skipping`);
        try {
          const dbSpec = await fetchDbSpec(deps, workflowId);
          const dbVersion = (dbSpec as Record<string, unknown>)?.version;
          if (typeof dbVersion === "number" && dbVersion > 0) {
            newVersion = dbVersion;
          }
        } catch { /* best effort */ }
      } else {
        warnings.push(`⚠️ 中心表写入失败: ${errMsg}`);
      }
    }
  } else if (newVersion !== undefined && deps.clawWebBaseUrl) {
    // Fallback: signatureKey missing but clawWebBaseUrl is available.
    // The save API already proved the server is reachable without signing.
    // Try unsigned POST to internal deploy-history endpoint — on internal
    // deployments the signing check may not be enforced.
    try {
      const unsignedResp = await fetch(`${deps.clawWebBaseUrl}/api/internal/deploy-history`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          packId, workflowId, deployNumber: nextNum, version: newVersion,
          tagName, action: "deploy", specJson,
          note: options?.note,
          botId: deps.botId, ownerId: deps.ownerId,
        }),
      });
      if (!unsignedResp.ok) {
        const errText = await unsignedResp.text().catch(() => "");
        warnings.push(`⚠️ 部署记录写入失败 (unsigned fallback, ${unsignedResp.status}): ${errText.slice(0, 200)}`);
        console.warn(`[deploy] unsigned deploy-history insert failed for ${workflowId}: ${unsignedResp.status} ${errText.slice(0, 300)}`);
      } else {
        console.log(`[deploy] deploy_history inserted via unsigned fallback for ${workflowId} v${newVersion}`);
      }
    } catch (err) {
      warnings.push(`⚠️ 部署记录写入失败: ${err instanceof Error ? err.message : err}`);
    }
  } else if (!deps.clawWebBaseUrl) {
    warnings.push("⚠️ 缺少 clawWebBaseUrl，未写入中心部署记录");
  }

  // Fallback: if we still don't have a version, try from DB spec
  if (newVersion === undefined) {
    try {
      const dbSpec = await fetchDbSpec(deps, workflowId);
      newVersion = (dbSpec as Record<string, unknown>)?.version as number | undefined;
    } catch { /* best effort */ }
  }

  const result = [
    `✅ 部署成功: ${workflowId} v${newVersion ?? "?"} (${tagName})`,
    ...warnings,
  ];
  return result.join("\n");
}

export async function handlePull(
  deps: VersionCommandDeps,
  workflowId?: string,
  options?: { skipPermissionCheck?: boolean },
): Promise<string> {
  // auto-pull on run reuses the execute-permission gate already performed by the
  // caller (src/index.ts dispatch). It must not re-run the edit-permission check
  // here, otherwise a bot allowed to execute but not to edit would be blocked from
  // the best-effort local-pack materialization and fall back to a DB-spec run that
  // fails mid-way on missing pack resources. The CLI `pull` path leaves this unset.
  if (workflowId && !options?.skipPermissionCheck) {
    const permResult = await checkEditPermission(deps, workflowId);
    if (!permResult.allowed) return `❌ ${permResult.reason ?? `无权限 pull workflow "${workflowId}"`}`;
  }

  if (!deps.packsRoot) return `❌ packsRoot not configured`;
  if (!deps.clawWebBaseUrl) return `❌ clawWebBaseUrl not configured`;

  const apiRepo = createDeployHistoryApiRepo(deps);
  const workflows = workflowId ? [workflowId] : await getAccessibleWorkflows(deps);
  const results: string[] = [];

  for (const wfId of workflows) {
    const warnings: string[] = [];

    // ── Step 1: Fetch spec from DB (critical) ──
    let spec: Record<string, unknown>;
    let packId: string;
    let deployedVersion: number | undefined;

    try {
      const resp = await fetch(`${deps.clawWebBaseUrl}/api/workflows/${wfId}`);
      if (!resp.ok) {
        results.push(`❌ ${wfId}: DB 查询失败 (${resp.status})`);
        continue;
      }
      const data = await resp.json() as { spec: Record<string, unknown>; packId?: string };
      spec = data.spec ?? data;
      const dbPackId = (data.packId ?? (spec as Record<string, unknown>).packId ?? wfId) as string;
      packId = dbPackId || wfId;

      const latestDeploy = apiRepo ? await apiRepo.getLatestDeploy(packId, wfId) : undefined;
      deployedVersion = latestDeploy?.version;
    } catch (err) {
      results.push(`❌ ${wfId}: DB 查询异常 — ${err instanceof Error ? err.message : err}`);
      continue;
    }

    // ── Step 1b: Quick check — if local spec matches DB, skip all git operations ──
    const resolvedWf = (deps.resolvedWorkflows ?? []).find((w) => w.id === wfId);
    if (resolvedWf) {
      try {
        const localSpec = await readRawLocalSpec(resolvedWf);
        const dbNorm = normalizeSpec(spec);
        const localNorm = normalizeSpec(localSpec);
        if (dbNorm === localNorm) {
          results.push(`✅ ${wfId} (db=v${deployedVersion ?? "none"}, pack=${packId}) — already synced`);
          continue;
        }
      } catch { /* comparison failed — proceed with pull */ }
    }

    // ── Step 2: Write YAML + manifest to disk (critical — this IS the pull) ──
    const packsDir = deps.packsRoot;
    try {
      await writeSpecToYaml(packsDir, packId, wfId, spec);
      await ensurePackManifest(packsDir, packId, [wfId]);
    } catch (err) {
      results.push(`❌ ${wfId}: 写入本地文件失败 — ${err instanceof Error ? err.message : err}`);
      continue;
    }

    // ── Step 3: Git commit + sync tag + push (best-effort) ──
    // Pull is not a deploy — use "sync/" tag prefix (not "deploy/") and
    // do NOT insert into deploy_history.
    try {
      const packDir = await ensureGitRepoForPack(packsDir, packId, wfId, deps);
      const branchName = `wf/${wfId}`;
      let hasNewCommit = false;
      try {
        await gitModule.addCommit(
          packDir,
          ["."],
          `[pull] ${wfId} from DB (db=v${deployedVersion ?? "none"}, pack=${packId})`,
        );
        hasNewCommit = true;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        const isNoChange = msg.includes("nothing to commit") || msg.includes("no changes added");
        if (!isNoChange) {
          warnings.push(`⚠️ Git commit 失败: ${msg}`);
        }
      }
      if (hasNewCommit) {
        if (deployedVersion !== undefined) {
          try {
            await gitModule.createTag(packDir, `sync/${wfId}/v${deployedVersion}`, {
              version: deployedVersion,
              action: "pull",
              workflowIds: [wfId],
            });
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            if (!msg.includes("already exists")) {
              warnings.push(`⚠️ Git tag 失败: ${msg}`);
            }
          }
        }
        try {
          const pushTag = deployedVersion !== undefined ? `sync/${wfId}/v${deployedVersion}` : false;
          await gitModule.pushBranch(packDir, branchName, pushTag);
        } catch (err) {
          warnings.push(`⚠️ Git push 失败: ${err instanceof Error ? err.message : err}`);
        }
      }
    } catch (err) {
      warnings.push(`⚠️ Git 操作失败: ${err instanceof Error ? err.message : err}. YAML 已写入。`);
    }

    const result = [`✅ ${wfId} (db=v${deployedVersion ?? "none"}, pack=${packId})`, ...warnings];
    results.push(result.join("\n"));
  }

  return results.join("\n") || "没有可 pull 的 workflow";
}

export async function handleRollback(
  deps: VersionCommandDeps,
  workflowId: string,
  options?: { version?: number; deployNumber?: number; pack?: boolean; tag?: string; note?: string },
): Promise<string> {
  const permResult = await checkEditPermission(deps, workflowId);
  if (!permResult.allowed) return `❌ ${permResult.reason ?? `无权限 rollback workflow "${workflowId}"`}`;
  const warnings: string[] = [];

  if (!deps.packsRoot) return `❌ packsRoot not configured`;

  const packId = resolvePackId(deps, workflowId);
  const packsDir = deps.packsRoot;
  const apiRepo = createDeployHistoryApiRepo(deps);
  if (!apiRepo) return `❌ 缺少 clawWebBaseUrl/signatureKey，无法读取中心部署记录`;
  const branchName = `wf/${workflowId}`;

  const targetVersion = options?.version;
  const targetDeployNumber = options?.deployNumber;

  if (!targetVersion && !targetDeployNumber) {
    return `❌ 必须指定 --version <N> 或 --deploy-number <N>。使用 history 命令查看可用版本。`;
  }

  // Resolve target snapshot by version or deploy-number
  let targetSnapshot: { specJson: string; deployNumber: number; tagName: string; action: string; version?: number; fromDeployNumber?: number } | undefined;

  if (targetDeployNumber !== undefined) {
    targetSnapshot = await apiRepo.findByDeployNumber(packId, workflowId, targetDeployNumber);
    if (!targetSnapshot) {
      return `❌ 未找到 ${workflowId} #${targetDeployNumber} 的部署记录`;
    }
  } else {
    targetSnapshot = await apiRepo.findByVersion(packId, workflowId, targetVersion!);
    if (!targetSnapshot) {
      // Version not found — list available versions to help the user
      const historyRows = await apiRepo.listHistory(workflowId, 20) as Array<{ deployNumber: number; version: number; action: string }>;
      const versions = [...new Set(historyRows.map(r => r.version))].sort((a, b) => a - b);
      const versionList = versions.map(v => `v${v}`).join(", ");
      return `❌ 未找到 ${workflowId} v${targetVersion} 的部署记录。可用版本: ${versionList || "无"}\n提示: 也可用 --deploy-number <N> 指定部署号回退。`;
    }
  }

  const specJson = targetSnapshot.specJson;
  const specObj = JSON.parse(specJson);

  // Use the resolved version from the snapshot (works for both --version and --deploy-number paths)
  const resolvedVersion = targetSnapshot.version ?? targetVersion ?? 0;

  // Validate the snapshot before applying — corrupted snapshots (e.g., empty nodes)
  // would fail when written to DB via save API.
  if (!specObj.id || !Array.isArray(specObj.nodes) || specObj.nodes.length === 0) {
    const verDesc = targetDeployNumber !== undefined ? `#${targetDeployNumber}` : `v${resolvedVersion}`;
    return `❌ ${workflowId} ${verDesc} 的部署记录 spec 不完整（缺少 id 或 nodes 为空），无法回退。请选择其他版本。`;
  }

  if (options?.pack) {
    const packWorkflows = getPackWorkflowIds(deps, packId);
    const wfIds = packWorkflows.length > 0 ? packWorkflows : [workflowId];

    for (const wfId of wfIds) {
      const wfSnapshot = targetDeployNumber !== undefined
        ? await apiRepo.findByDeployNumber(packId, wfId, targetDeployNumber)
        : await apiRepo.findByVersion(packId, wfId, resolvedVersion);
      if (wfSnapshot) {
        const wfSpec = JSON.parse(wfSnapshot.specJson);
        const savedSpec = await deployToClawWeb(deps, wfId, packId, wfSpec);
        // Write DB-stored spec back to local YAML for sync consistency
        delete (savedSpec as Record<string, unknown>).version;
        delete (savedSpec as Record<string, unknown>).facade;
        await writeSpecToYaml(packsDir, packId, wfId, savedSpec);
      }
    }

    await ensurePackManifest(packsDir, packId);

    const packDir = await ensureGitRepoForPack(packsDir, packId, workflowId, deps);
    let nextNum = await gitModule.nextDeployNumber(packDir, workflowId);
    if (apiRepo) {
      try {
        const dbMaxDeployNum = await apiRepo.getMaxDeployNumber(packId, workflowId);
        nextNum = Math.max(nextNum, dbMaxDeployNum + 1);
      } catch { /* fallback */ }
    }
    const tagName = `deploy/${workflowId}/#${nextNum}`;

    // Restore entire directory from target tag (scripts + yaml together)
    if (targetSnapshot.tagName) {
      try {
        await gitModule.checkoutPaths(packDir, targetSnapshot.tagName, ["."]);
      } catch (err) {
        console.warn(`[rollback] checkoutPaths from tag ${targetSnapshot.tagName} failed: ${err instanceof Error ? err.message : err}`);
      }
    }

    try {
      await gitModule.addCommit(packDir, ["."], `[${deps.botId}/${deps.ownerId}] rollback ${packId} --pack #${targetSnapshot.deployNumber} → v${resolvedVersion} (#${nextNum})`);
    } catch (err) {
      const gitErr = err instanceof Error ? err.message : String(err);
      const isNoChange = gitErr.includes("nothing to commit") || gitErr.includes("no changes added") || gitErr.includes("nothing added");
      if (!isNoChange) {
        warnings.push(`⚠️ Git commit 失败: ${gitErr}`);
      }
    }
    try {
      await gitModule.createTag(packDir, tagName, {
        version: resolvedVersion,
        action: "rollback",
        workflowIds: wfIds,
        fromTag: targetSnapshot.tagName || `deploy/${workflowId}/#${targetSnapshot.deployNumber}`,
      });
    } catch (err) {
      const gitErr = err instanceof Error ? err.message : String(err);
      if (!gitErr.includes("already exists")) {
        warnings.push(`⚠️ Git tag 失败: ${gitErr}`);
      }
    }
    try {
      await gitModule.pushBranch(packDir, branchName, tagName);
    } catch (err) {
      warnings.push(`⚠️ Git push 失败: ${err instanceof Error ? err.message : err}`);
    }

    for (const wfId of wfIds) {
      const wfSnapshot = targetDeployNumber !== undefined
        ? await apiRepo.findByDeployNumber(packId, wfId, targetDeployNumber)
        : await apiRepo.findByVersion(packId, wfId, resolvedVersion);
      if (wfSnapshot) {
        try {
          await apiRepo.insert({
            packId, workflowId: wfId, deployNumber: nextNum, version: resolvedVersion,
            tagName, action: "rollback", fromDeployNumber: targetSnapshot.deployNumber,
            specJson: wfSnapshot.specJson, note: options?.note,
            botId: deps.botId, ownerId: deps.ownerId,
          });
        } catch (err) {
          const errMsg = err instanceof Error ? err.message : String(err);
          const is409 = errMsg.includes("409") || errMsg.toLowerCase().includes("conflict");
          if (!is409) {
            warnings.push(`⚠️ ${wfId} 中心表写入失败: ${errMsg}`);
          }
        }
      }
    }

    return [`✅ Pack 回退成功: ${packId} → v${resolvedVersion} (${tagName})`, ...warnings].join("\n");
  }

  // Default: rollback single workflow
  const savedSpec = await deployToClawWeb(deps, workflowId, packId, specObj);
  // Write DB-stored spec back to local YAML for sync consistency
  delete (savedSpec as Record<string, unknown>).version;
  delete (savedSpec as Record<string, unknown>).facade;
  await writeSpecToYaml(packsDir, packId, workflowId, savedSpec);

  const packDir = await ensureGitRepoForPack(packsDir, packId, workflowId, deps);
  let nextNum = await gitModule.nextDeployNumber(packDir, workflowId);
  // Also check DB deploy_history for max deploy_number — per-pack repos may be fresh
  // and not have remote tags, so git-based nextDeployNumber can return a too-low value.
  if (apiRepo) {
    try {
      const dbMaxDeployNum = await apiRepo.getMaxDeployNumber(packId, workflowId);
      nextNum = Math.max(nextNum, dbMaxDeployNum + 1);
    } catch { /* fallback to git-based number */ }
  }
  const tagName = `deploy/${workflowId}/#${nextNum}`;

  // Restore entire pack directory from target tag (scripts + yaml together)
  // In the per-pack repo model, the repo root IS the pack directory,
  // so checkoutPaths "." restores everything.
  if (targetSnapshot.tagName) {
    try {
      await gitModule.checkoutPaths(packDir, targetSnapshot.tagName, ["."]);
    } catch (err) {
      console.warn(`[rollback] checkoutPaths from tag ${targetSnapshot.tagName} failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  try {
    await gitModule.addCommit(
      packDir,
      ["."],
      `[${deps.botId}/${deps.ownerId}] rollback ${workflowId} → v${resolvedVersion} (#${nextNum})`,
    );
  } catch (err) {
    const gitErr = err instanceof Error ? err.message : String(err);
    const isNoChange = gitErr.includes("nothing to commit") || gitErr.includes("no changes added") || gitErr.includes("nothing added");
    if (!isNoChange) {
      warnings.push(`⚠️ Git commit 失败: ${gitErr}`);
    }
  }
  try {
    await gitModule.createTag(packDir, tagName, {
      version: resolvedVersion,
      action: "rollback",
      workflowIds: [workflowId],
      fromTag: targetSnapshot.tagName || `deploy/${workflowId}/#${targetSnapshot.deployNumber}`,
    });
  } catch (err) {
    const gitErr = err instanceof Error ? err.message : String(err);
    if (!gitErr.includes("already exists")) {
      warnings.push(`⚠️ Git tag 失败: ${gitErr}`);
    }
  }
  try {
    await gitModule.pushBranch(packDir, branchName, tagName);
  } catch (err) {
    warnings.push(`⚠️ Git push 失败: ${err instanceof Error ? err.message : err}`);
  }

  if (apiRepo) {
    try {
      await apiRepo.insert({
        packId, workflowId, deployNumber: nextNum, version: resolvedVersion,
        tagName, action: "rollback", fromDeployNumber: targetSnapshot.deployNumber,
        specJson, note: options?.note, botId: deps.botId, ownerId: deps.ownerId,
      });
    } catch (err) {
      warnings.push(`⚠️ 中心表写入失败: ${err instanceof Error ? err.message : err}`);
    }
  } else if (deps.clawWebBaseUrl) {
    // Fallback: no signatureKey but server is reachable — try unsigned insert
    try {
      const unsignedResp = await fetch(`${deps.clawWebBaseUrl}/api/internal/deploy-history`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          packId, workflowId, deployNumber: nextNum, version: resolvedVersion,
          tagName, action: "rollback", fromDeployNumber: targetSnapshot.deployNumber,
          specJson, note: options?.note, botId: deps.botId, ownerId: deps.ownerId,
        }),
      });
      if (!unsignedResp.ok) {
        warnings.push(`⚠️ 回退记录写入失败 (unsigned, ${unsignedResp.status})`);
      }
    } catch { /* best effort */ }
  }

  const sourceDesc = targetSnapshot.action === "edit"
    ? `v${resolvedVersion} (来源: web编辑)`
    : `v${resolvedVersion} (来源: ${targetSnapshot.tagName || "#" + targetSnapshot.deployNumber})`;
  const result = [
    `✅ 回退成功: ${workflowId} → ${sourceDesc} (${tagName})`,
    ...warnings,
  ];
  return result.join("\n");
}

const ACTION_LABELS: Record<string, string> = {
    deploy: "部署 (本地→DB)",
    edit: "编辑 (web端修改)",
    rollback: "回退 (回退到历史版本)",
    migration: "迁移 (DB→本地对齐)",
    pull: "拉取 (DB→本地)",
  };

export async function handleHistory(
  deps: VersionCommandDeps,
  workflowId: string,
  limit?: number,
  detailVersion?: number,
  detailDeployNumber?: number,
): Promise<string> {
  const apiRepo = createDeployHistoryApiRepo(deps);
  if (!apiRepo) return `❌ 缺少 clawWebBaseUrl/signatureKey，无法读取中心部署记录`;

  const packId = resolvePackId(deps, workflowId);

  // Detail mode: by --version N (deploy/edit records only)
  if (detailVersion !== undefined) {
    const snapshot = await apiRepo.findByVersion(packId, workflowId, detailVersion);
    if (!snapshot) {
      return `❌ 未找到 ${workflowId} v${detailVersion} 的部署/编辑记录（rollback 记录不算，version 只看 deploy/edit）`;
    }
    let specObj: Record<string, unknown>;
    try {
      specObj = JSON.parse(snapshot.specJson);
    } catch {
      return `❌ v${detailVersion} 的 specJson 解析失败`;
    }
    const nodeCount = Array.isArray(specObj.nodes) ? specObj.nodes.length : 0;
    const isValid = specObj.id && nodeCount > 0;
    const lines = [
      `📋 ${workflowId} v${detailVersion} 版本详情:`,
      `  deploy: #${snapshot.deployNumber}  action: ${snapshot.action}  tag: ${snapshot.tagName || "(none)"}`,
      `  nodes: ${nodeCount}  ${isValid ? "✅" : "⚠️ spec 不完整（缺少 id 或 nodes 为空）"}`,
      ``,
      `--- spec ---`,
      yaml.dump(stripUiFields(stripUndefinedDeep(specObj))),
    ];
    return lines.join("\n");
  }

  // Detail mode: by --deploy-number N (any record type)
  if (detailDeployNumber !== undefined) {
    const record = await apiRepo.findByDeployNumber(packId, workflowId, detailDeployNumber);
    if (!record) {
      return `❌ 未找到 ${workflowId} 部署号 #${detailDeployNumber} 的记录`;
    }
    let specObj: Record<string, unknown>;
    try {
      specObj = JSON.parse(record.specJson);
    } catch {
      return `❌ #${detailDeployNumber} 的 specJson 解析失败`;
    }
    const nodeCount = Array.isArray(specObj.nodes) ? specObj.nodes.length : 0;
    const isValid = specObj.id && nodeCount > 0;
    const fromStr = record.fromDeployNumber ? `  from: #${record.fromDeployNumber}` : "";
    const lines = [
      `📋 ${workflowId} #${detailDeployNumber} 部署记录详情:`,
      `  version: v${record.version}  action: ${record.action} (${ACTION_LABELS[record.action] ?? record.action})  tag: ${record.tagName || "(none)"}${fromStr}`,
      `  nodes: ${nodeCount}  ${isValid ? "✅" : "⚠️ spec 不完整"}`,
      ``,
      `--- spec ---`,
      yaml.dump(stripUiFields(stripUndefinedDeep(specObj))),
    ];
    return lines.join("\n");
  }

  // List mode: show all deploy history summary
  const rows = await apiRepo.listHistory(workflowId, limit ?? 10) as Array<{ deployNumber: number; version: number; action: string; fromDeployNumber?: number; gmtCreate: number }>;

  if (rows.length === 0) {
    return `📋 ${workflowId}: 无部署历史`;
  }

  const lines = rows.map((r) => {
    const from = r.fromDeployNumber ? ` ← #${r.fromDeployNumber}` : "";
    // The history API serializes the DB deploy_history row, whose timestamp column
    // is `gmt_create` (snake_case). Earlier code read `gmtCreate` (camelCase) and
    // did a naive `* 1000`, which yielded NaN (→ "Invalid Date") when the field was
    // absent OR when the API returned a MySQL DATETIME / ISO string instead of epoch
    // seconds. Reuse parseDbTimestamp (handles epoch sec/ms, MySQL DATETIME, ISO) and
    // formatLocalTime (returns "" on unparseable) so we never render "Invalid Date".
    const raw = (r as any).gmt_create ?? (r as any).gmtCreate ?? (r as any).created_at ?? (r as any).createdAt;
    const ts = formatLocalTime(parseDbTimestamp(raw));
    const label = ACTION_LABELS[r.action] ?? r.action;
    return `  #${r.deployNumber}  v${r.version}  ${label}${from}  ${ts}`;
  });

  return [
    `📋 ${workflowId} 部署历史:`,
    ...lines,
    ``,
    `操作类型: deploy=部署  edit=编辑  rollback=回退  migration=迁移  pull=拉取`,
    `查看详情: --version N 或 --deploy-number N`,
    `回退: --version N (回退到该版本) 或 --deploy-number N (按部署号回退)`,
  ].join("\n");
}

export async function handleStatus(
  deps: VersionCommandDeps,
  workflowId?: string,
  diff?: boolean,
  gitDiff?: boolean,
): Promise<string> {
  if (!workflowId) {
    return handleStatusAll(deps);
  }

  const { syncStatus, deployedVersion, action, newerSide, dbUpdatedAt, localMtime } = await computeSyncStatus(deps, workflowId);

  const packId = resolvePackId(deps, workflowId);
  const resolvedWf = (deps.resolvedWorkflows ?? []).find((w) => w.id === workflowId);
  const apiRepo = createDeployHistoryApiRepo(deps);
  const latestDeploy = apiRepo ? await apiRepo.getLatestDeploy(packId, workflowId) : undefined;
  const deployedTag = latestDeploy ? `deploy/${workflowId}/#${latestDeploy.deployNumber}` : "none";
  const localPath = resolvedWf ? (resolvedWf.absolutePath ?? `packs/${packId}/workflows/${workflowId}.yaml`) : "(not found locally)";

  // Format status line with direction hint when content differs
  let statusLine = syncStatus;
  if (syncStatus === "differs" && newerSide) {
    const dirLabel = newerSide === "db_newer" ? "DB更新" : newerSide === "local_newer" ? "本地更新" : newerSide === "same_age" ? "同龄" : "未知";
    statusLine = `${syncStatus} (${dirLabel})`;
  }

  const lines = [
    `Workflow: ${workflowId} (pack=${packId})`,
    `  Deployed: v${deployedVersion ?? "none"} (git=${deployedTag})`,
    `  Status:   ${statusLine}`,
    `  Local:    ${localPath}`,
  ];

  // Show timestamp details when content differs
  if (syncStatus === "differs") {
    if (dbUpdatedAt) lines.push(`  DB updated:    ${dbUpdatedAt}`);
    if (localMtime) lines.push(`  Local mtime:   ${localMtime}`);
    if (newerSide === "db_newer") {
      lines.push(`  ⚠️ DB 比本地更新 — deploy 需加 --force，或先 pull 同步`);
    } else if (newerSide === "local_newer") {
      lines.push(`  ℹ️ 本地比 DB 更新 — deploy 将覆盖 DB`);
    }
  }

  // Show tracked files (scripts/, etc.) from git
  const packsDir = deps.packsRoot;
  if (packsDir) {
    const packDir = path.join(packsDir, packId);
    const gitDir = path.join(packDir, ".git");
    if (fsSync.existsSync(gitDir)) {
      try {
        // List tracked files relative to pack dir
        const tracked = execFileSync("git", ["ls-files"], {
          cwd: packDir,
          encoding: "utf-8",
          timeout: 5000,
        }).trim().split("\n").filter(Boolean);
        const scriptFiles = tracked.filter(f => !f.endsWith(".yaml") && !f.endsWith(".yml") && !f.includes("workflow.pack."));
        const yamlFiles = tracked.filter(f => f.endsWith(".yaml") || f.endsWith(".yml"));
        lines.push(`  Tracked:  ${yamlFiles.length} yaml, ${scriptFiles.length} other files`);
        if (scriptFiles.length > 0) {
          lines.push(`  Scripts:  ${scriptFiles.slice(0, 10).join(", ")}${scriptFiles.length > 10 ? ` ... (+${scriptFiles.length - 10})` : ""}`);
        }

        // Check for uncommitted changes (including untracked)
        const statusOut = execFileSync("git", ["status", "--porcelain"], {
          cwd: packDir,
          encoding: "utf-8",
          timeout: 5000,
        }).trim().split("\n").filter(Boolean);
        if (statusOut.length > 0) {
          const modified = statusOut.filter(l => l.startsWith(" M") || l.startsWith("M ")).length;
          const untracked = statusOut.filter(l => l.startsWith("??")).length;
          const deleted = statusOut.filter(l => l.startsWith(" D") || l.startsWith("D ")).length;
          const parts: string[] = [];
          if (modified) parts.push(`${modified} modified`);
          if (untracked) parts.push(`${untracked} untracked`);
          if (deleted) parts.push(`${deleted} deleted`);
          if (parts.length) lines.push(`  Uncommitted: ${parts.join(", ")}`);
        }
      } catch { /* git not available */ }
    }
  }

  // --diff: YAML diff (DB vs local) — lightweight, no git network ops
  const shouldShowYamlDiff = diff || syncStatus === "differs";
  if (shouldShowYamlDiff) {
    const dbSpec = await fetchDbSpec(deps, workflowId);
    if (dbSpec) {
      const localSpec = await readRawLocalSpec(resolvedWf);
      lines.push("", "--- db (current)", "+++ local (yaml)", computeSpecDiff(dbSpec, localSpec));
      lines.push("", `DB deploy info: v${deployedVersion ?? "none"} | git=${deployedTag}`);
    } else {
      lines.push("", "⚠️ 无法获取 DB 当前 spec（API 不可达或 workflow 不存在）");
    }
  }

  // --git-diff: git diff (remote vs local) — heavy: involves git fetch over network
  // Separate from --diff so users can choose lightweight YAML diff without paying
  // for network I/O. Use --git-diff when you need to compare scripts and other
  // non-YAML tracked files against the remote branch.
  if (gitDiff && packsDir) {
    const packDir = path.join(packsDir, packId);
    const gitDir = path.join(packDir, ".git");
    const branchName = `wf/${workflowId}`;
    if (fsSync.existsSync(gitDir)) {
      try {
        // Fetch latest from remote first
        if (deps.gitRemoteUrl) {
          await gitModule.fetchRemote(packDir, branchName);
        }
        const remoteRef = `origin/${branchName}`;
        try {
          execFileSync("git", ["rev-parse", "--verify", remoteRef], {
            cwd: packDir, encoding: "utf-8", timeout: 5000,
          });
          // Remote exists — diff remote vs working tree
          const diffOut = execFileSync("git", ["diff", remoteRef, "--", "."], {
            cwd: packDir, encoding: "utf-8", timeout: 10000,
          }).trim();
          if (diffOut) {
            const diffFiles = execFileSync("git", ["diff", "--name-status", remoteRef, "--", "."], {
              cwd: packDir, encoding: "utf-8", timeout: 5000,
            }).trim().split("\n").filter(Boolean);
            const changedScripts = diffFiles.filter(l => !l.endsWith(".yaml") && !l.endsWith(".yml"));
            const changedYaml = diffFiles.filter(l => l.endsWith(".yaml") || l.endsWith(".yml"));
            lines.push("", `--- remote (${remoteRef})`, "+++ local (working tree)");
            if (changedYaml.length) lines.push(`  YAML changes: ${changedYaml.join(", ")}`);
            if (changedScripts.length) lines.push(`  Script changes: ${changedScripts.join(", ")}`);
            const diffLines = diffOut.split("\n");
            const truncated = diffLines.length > 50
              ? [...diffLines.slice(0, 50), `... (${diffLines.length - 50} more lines)`]
              : diffLines;
            lines.push(...truncated);
          } else {
            lines.push("", `Local matches remote (${remoteRef}) — no differences`);
          }
        } catch {
          lines.push("", "⚠️ 远端分支不存在，无法对比 git 差异");
        }
      } catch {
        lines.push("", "⚠️ Git diff 失败");
      }
    }
  }

  if (action) lines.push(`  Action:   ${action}`);

  return lines.join("\n");
}

async function handleStatusAll(deps: VersionCommandDeps): Promise<string> {
  const lines = ["Workflows status:"];

  for (const wf of (deps.resolvedWorkflows ?? [])) {
    const wfId = wf.id;
    const { syncStatus, deployedVersion, action, newerSide } = await computeSyncStatus(deps, wfId);
    const localPath = wf.absolutePath ?? "";
    const pathHint = localPath ? ` | ${localPath}` : "";
    const dirHint = syncStatus === "differs" && newerSide
      ? ` (${newerSide === "db_newer" ? "DB更新" : newerSide === "local_newer" ? "本地更新" : newerSide})`
      : "";
    lines.push(`- ${wfId} | db=v${deployedVersion ?? "none"} | ${syncStatus}${dirHint} ${action}${pathHint}`);
  }

  return lines.join("\n");
}

export async function handleShare(
  deps: VersionCommandDeps,
  workflowId: string,
  to: string,
): Promise<string> {
  const [targetOwnerId, targetBotId] = to.split("/");
  if (!targetOwnerId || !targetBotId) {
    return `❌ --to 格式错误: "${to}". 应为 <ownerId>/<botId>`;
  }

  const permResult = await checkEditPermission(deps, workflowId);
  if (!permResult.allowed) return `❌ ${permResult.reason ?? `无权限 share workflow "${workflowId}"`}`;

  if (!deps.clawWebBaseUrl) return `❌ clawWebBaseUrl not configured`;

  try {
    const resp = await fetch(`${deps.clawWebBaseUrl}/api/workflows/${workflowId}/bot-permissions`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        permissions: [{ bot_id: targetBotId, bot_owner_id: targetOwnerId, can_view: 1, can_execute: 1, can_edit: 0 }],
      }),
    });
    if (!resp.ok) {
      const errText = await resp.text();
      return `❌ Share 授权失败: ${errText}`;
    }
  } catch (err) {
    return `❌ Share 授权失败: ${err instanceof Error ? err.message : err}`;
  }

  return `✅ Shared ${workflowId} to ${to} (已授权 can_execute，目标 bot 下次同步时自动拉取)`;
}

export async function handleUnshare(
  deps: VersionCommandDeps,
  workflowId: string,
  from: string,
): Promise<string> {
  const [targetOwnerId, targetBotId] = from.split("/");
  if (!targetOwnerId || !targetBotId) {
    return `❌ --from 格式错误: "${from}". 应为 <ownerId>/<botId>`;
  }

  const permResult = await checkEditPermission(deps, workflowId);
  if (!permResult.allowed) return `❌ ${permResult.reason ?? `无权限 unshare workflow "${workflowId}"`}`;

  if (!deps.clawWebBaseUrl) return `❌ clawWebBaseUrl not configured`;

  try {
    const resp = await fetch(`${deps.clawWebBaseUrl}/api/workflows/${workflowId}/bot-permissions`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bot_id: targetBotId, bot_owner_id: targetOwnerId }),
    });
    if (!resp.ok) {
      const errText = await resp.text();
      return `❌ Unshare 撤权失败: ${errText}`;
    }
  } catch (err) {
    return `❌ Unshare 撤权失败: ${err instanceof Error ? err.message : err}`;
  }

  return `✅ Unshared ${workflowId} from ${from} (已撤销权限)`;
}

/**
 * Full alignment: sync all DB workflows into local packs structure.
 *
 * Called on every startup to ensure DB ≥ local. Priority: DB > Git > local.
 *
 * For each DB workflow:
 * - Not present locally → write from DB
 * - Present locally but differs from DB → overwrite with DB version
 * - Present locally and identical → skip
 *
 * Also repairs packs that are missing their workflow.pack.yaml manifest.
 * Uses sync/ tags (not deploy/), does NOT insert into deploy_history.
 */
export async function handleMigration(deps: VersionCommandDeps): Promise<string> {
  if (!deps.packsRoot) return `❌ packsRoot not configured`;
  if (!deps.clawWebBaseUrl) return `❌ clawWebBaseUrl not configured`;

  const packsDir = deps.packsRoot;
  const apiRepo = createDeployHistoryApiRepo(deps);

  // 1. Query workflow summaries from ClawWeb
  let summaries: Array<{ workflowId: string; packId: string | null }>;
  try {
    const resp = await fetch(
      `${deps.clawWebBaseUrl}/api/workflows?botOwnerId=${deps.ownerId ?? ""}${deps.botId ? `&botId=${deps.botId}` : ""}`,
    );
    if (!resp.ok) return `❌ Migration 失败: ClawWeb API 返回 ${resp.status}`;
    const data = await resp.json();
    const list = Array.isArray(data) ? data : (data as any).workflows ?? [];
    summaries = list.map((w: any) => ({
      workflowId: w.workflow_id ?? w.workflowId,
      packId: w.pack_id ?? w.packId ?? null,
    }));
  } catch (err) {
    return `❌ Migration 失败: ClawWeb API 不可达 — ${err instanceof Error ? err.message : err}`;
  }

  if (summaries.length === 0) return "⚠️ 无可对齐的 workflow (DB 中无 workflow)";

  // 2. Fetch full specs from DB
  type DbWorkflow = { workflowId: string; packId: string; spec: Record<string, unknown>; deployedVersion: number | undefined };
  const dbWorkflows: DbWorkflow[] = [];

  for (const summary of summaries) {
    try {
      const detailResp = await fetch(
        `${deps.clawWebBaseUrl}/api/workflows/${encodeURIComponent(summary.workflowId)}`,
      );
      if (!detailResp.ok) {
        console.warn(`[migration] Failed to fetch spec for ${summary.workflowId}: ${detailResp.status}`);
        continue;
      }
      const spec = await detailResp.json() as Record<string, unknown>;
      const packId = summary.packId ?? (spec.id as string | undefined) ?? summary.workflowId;
      const latestDeploy = apiRepo ? await apiRepo.getLatestDeploy(packId, summary.workflowId) : undefined;
      dbWorkflows.push({
        workflowId: summary.workflowId,
        packId,
        spec,
        deployedVersion: latestDeploy?.version,
      });
    } catch (err) {
      console.warn(`[migration] Failed to fetch spec for ${summary.workflowId}: ${err instanceof Error ? err.message : err}`);
    }
  }

  if (dbWorkflows.length === 0) {
    return "⚠️ Migration: DB workflow spec 全部获取失败";
  }

  // 3. Compare each DB workflow vs local, classify action
  const toWrite: DbWorkflow[] = [];     // need to write (missing or differs)
  const alreadySynced: string[] = [];    // local == DB, skip

  for (const dbWf of dbWorkflows) {
    const resolvedWf = (deps.resolvedWorkflows ?? []).find((w) => w.id === dbWf.workflowId);
    if (!resolvedWf) {
      // Not present locally → must write
      toWrite.push(dbWf);
      continue;
    }

    // Compare by normalized content (read raw YAML to avoid normalizeWorkflowSpec defaults)
    const localSpec = await readRawLocalSpec(resolvedWf);
    const dbNorm = normalizeSpec(dbWf.spec);
    const localNorm = normalizeSpec(localSpec);

    if (dbNorm === localNorm) {
      alreadySynced.push(dbWf.workflowId);
    } else {
      toWrite.push(dbWf);
    }
  }

  if (toWrite.length === 0) {
    // Nothing to write — but maybe packs need manifest repair
    const repaired = await repairMissingPackManifests(packsDir, deps.resolvedPacks ?? []);
    if (repaired.length > 0) {
      // LOCAL-ONLY repair: ensurePackManifest just fabricated a minimal skeleton
      // (id/version/workflows only — no actions/skills/rich facades) so the pack
      // stays discoverable. We deliberately do NOT commit/push it to the shared
      // per-pack remote. Pushing a fabricated manifest would poison everyone:
      // pack-level config (actions/skills, real facade commands like `poa`) lives
      // ONLY in the manifest with zero redundancy in workflow specs or the DB, so
      // a pushed skeleton permanently overwrites the real manifest on every machine
      // that later pulls it. The real manifest is recovered on the next deploy/pull,
      // whose smartPullFromRemote fetches the full manifest from the wf/<workflowId>
      // branch and overwrites this skeleton before any commit.
      for (const repPackId of repaired) {
        console.warn(
          `[migration] Pack "${repPackId}" 缺失 workflow.pack.yaml,已本地补最小骨架(未推送远端)。` +
          `actions/skills/facades 缺失,下次 deploy/pull 会从远端拉回完整 manifest。`,
        );
      }
      return `📋 Migration 本地修复: 为 ${repaired.length} 个 pack 补了最小 manifest 骨架(未推送,避免投毒): ${repaired.join(", ")}`;
    }
    return `✅ Migration 跳过: ${alreadySynced.length} 个 workflow 已与 DB 同步`;
  }

  console.log(`[migration] ${toWrite.length} workflow(s) need sync (${alreadySynced.length} already synced)`);

  // 4. Group by packId
  const packGroups = new Map<string, DbWorkflow[]>();
  for (const wf of toWrite) {
    if (!packGroups.has(wf.packId)) packGroups.set(wf.packId, []);
    packGroups.get(wf.packId)!.push(wf);
  }

  const results: string[] = [];

  // 5. For each pack: rebuild from git, then align workflow YAMLs to DB.
  //
  // Migration is a READ-ONLY consumer of git: it pulls the full pack (scripts,
  // config, skills, manifest) from the remote wf/<workflowId> branch to rebuild
  // a lost local pack dir, then overwrites the workflow YAML with the DB's
  // current spec. It must NEVER commit or push back to the remote.
  //
  // Why no commit/push: in this data model DB is the source of truth for
  // workflow YAMLs and git is the source of truth for the rest of the pack
  // (scripts/config/skills). Pushing from migration only created churn:
  //   - `addCommit(["."])` swept up the whole working tree, including scripts
  //     that a neighbouring workflow's branch checkout had swapped into the
  //     shared pack dir, so each restart committed a *different* pack snapshot
  //     and pushed it — scripts toggled in/out across align commits (see the
  //     `a876a88` +1566-line align commit in the wf/privacy-odps-approval-v1
  //     history).
  //   - even YAML-only align commits stacked on top of a good deploy commit
  //     as redundant noise, and a non-fast-forward was force-disabled so it
  //     failed loudly instead of clobbering — but the ff-able payload still
  //     polluted the remote every restart.
  // Git writes belong to `deploy` (local → remote) and `rollerback`; migration
  // only realigns the local working copy. Nothing downstream reads migration's
  // sync tags or commits (deploy_history is the version source via
  // getLatestDeploy), so dropping them is safe.
  for (const [packId, wfs] of packGroups.entries()) {
    for (const wf of wfs) {
      try {
        // Rebuild the full pack from git FIRST (scripts/config/skills/manifest
        // checked out from wf/<workflowId>), THEN overwrite the workflow YAML
        // with the DB spec. Ordering matters: checkout may restore a stale YAML
        // from an older branch state, and the DB overwrite right after is what
        // guarantees the local YAML ends up matching DB, not the stale git copy.
        await ensureGitRepoForPack(packsDir, packId, wf.workflowId, deps);
      } catch (err) {
        console.warn(`[migration] git rebuild failed for ${wf.workflowId}: ${err instanceof Error ? err.message : err}`);
      }
      await writeSpecToYaml(packsDir, packId, wf.workflowId, wf.spec);
    }

    await ensurePackManifest(packsDir, packId, wfs.map((w) => w.workflowId));

    results.push(`✅ ${packId}: ${wfs.length} synced (db=v${wfs.map(w => w.deployedVersion ?? "?").join(",")})`);
  }

  if (alreadySynced.length > 0) {
    results.push(`⏭️ ${alreadySynced.length} already synced: ${alreadySynced.join(", ")}`);
  }

  return [`📋 Migration 完成 (${toWrite.length} aligned, ${alreadySynced.length} already synced):`, ...results].join("\n");
}