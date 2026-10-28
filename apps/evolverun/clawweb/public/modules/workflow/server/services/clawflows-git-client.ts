/**
 * ClawFlowsGitClient — isolated temporary-directory git operations for AutoHeal.
 *
 * ClawMind owns packsRoot and the long-lived pack repos. AutoHeal clones a
 * fresh temporary copy for every save/deploy so it never competes with local
 * Pack state. Branch names, tag names and annotations match ClawMind exactly:
 *
 *   saved branch: saved/{workflowId}
 *   deploy branch: wf/{workflowId}
 *   deploy tag:   deploy/{workflowId}/#N
 *   annotation:   version=N\naction=deploy\nworkflowIds={workflowId}
 */
import { executeGit } from "@avernet/clawweb-shared/server/services/git-execution";
import { mkdtemp, rm, mkdir, writeFile, readFile } from "node:fs/promises";
import { existsSync, lstatSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { stringify as stringifyYaml, parse as parseYaml } from "yaml";
import { normalizeWorkflowSpec, type WorkflowSpec } from "../workflow.js";

export type GitAuthor = {
  name: string;
  email: string;
};

export type SaveGitResult = {
  branch: string;
  commit: string;
};

export type DeployGitResult = {
  branch: string;
  tagName: string;
  commit: string;
  deployNumber: number;
};

export type ClawFlowsGitConfig = {
  remoteUrl: string;
  username: string;
  email: string;
  token?: string;
};

function isolatedGitEnvironment(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const isolated = Object.fromEntries(
    Object.entries(env).filter(([key]) => !key.startsWith("GIT_CONFIG_")),
  ) as NodeJS.ProcessEnv;
  return {
    ...isolated,
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_CONFIG_GLOBAL: "/dev/null",
  };
}

async function git(
  cwd: string,
  args: string[],
  options?: { env?: NodeJS.ProcessEnv; timeout?: number; maxBuffer?: number },
): Promise<string> {
  try {
    const result = await executeGit(
      args,
      {
        cwd,
        env: options?.env ?? isolatedGitEnvironment(process.env),
        timeout: options?.timeout ?? 60_000,
        maxBuffer: options?.maxBuffer ?? 16 * 1024 * 1024,
      },
    );
    return result.toString('utf8').trim();
  } catch (err: unknown) {
    // Remote servers and helpers can echo credentials (raw, encoded or Basic
    // auth). Never include their output, Error.message/cause, or argv in errors.
    let commandIndex = 0;
    while (args[commandIndex] === "-c") commandIndex += 2;
    const status = (err as { status?: unknown } | null)?.status;
    const detail = typeof status === "number" ? ` (exit ${status})` : "";
    throw Object.assign(new Error(`git ${args[commandIndex]} failed${detail}`), { code: (err as { code?: string })?.code });
  }
}

function parseDeployTag(tagName: string): { workflowId: string; number: number } | null {
  const match = tagName.match(/^deploy\/(.+)\/#(\d+)$/);
  if (!match) return null;
  return { workflowId: match[1], number: parseInt(match[2], 10) };
}

function httpRemoteUrl(remoteUrl: string): URL | null {
  // SCP-like SSH paths and local/file paths must not receive HTTP credentials.
  if (!/^https?:\/\//i.test(remoteUrl)) return null;
  try {
    return new URL(remoteUrl);
  } catch {
    // URL's own error can contain userinfo from the input.
    throw new Error("Invalid HTTP Git remote URL");
  }
}

function credentialHelper(credentialsPath: string): string {
  // Git runs helpers through a shell even though we use execFile. Quote
  // only the path; the username/token are never part of this command.
  const quotedPath = "'" + credentialsPath.replace(/'/g, "'\\''") + "'";
  return `store --file ${quotedPath}`;
}

async function writeCredentials(authDir: string, contents: string): Promise<string> {
  const credentialsPath = path.join(authDir, "credentials");
  await writeFile(credentialsPath, contents, { mode: 0o600, flag: "wx" });
  return credentialHelper(credentialsPath);
}

function getTempRepoPrefix(): string {
  const configured = process.env.CLAWWEB_TEMP_DIR || process.env.TMPDIR || tmpdir();
  return path.join(configured, "cw-autoheal-");
}

export async function createTempRepo(): Promise<string> {
  const prefix = getTempRepoPrefix();
  const parent = path.dirname(prefix);
  if (!existsSync(parent)) {
    throw new Error(`临时 git 目录的父路径不存在: ${parent} (CLAWWEB_TEMP_DIR/TMPDIR=${process.env.CLAWWEB_TEMP_DIR || process.env.TMPDIR || ""})`);
  }
  try {
    return await mkdtemp(prefix);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`无法创建临时 git 目录: ${message} (prefix=${prefix})`);
  }
}

export async function removeTempRepo(repoDir: string): Promise<void> {
  await rm(repoDir, { recursive: true, force: true });
}

export async function cloneClawflows(
  repoDir: string,
  config: ClawFlowsGitConfig,
): Promise<void> {
  if (!config.remoteUrl) throw new Error("clawflowsRemoteUrl is not configured");

  const remote = httpRemoteUrl(config.remoteUrl);
  let cloneUrl = config.remoteUrl;
  let credentials: string | undefined;
  if (remote) {
    // Reject cleartext authentication before creating files or invoking Git.
    if (remote.protocol !== "https:" && (config.token || remote.password)) {
      throw new Error("Git credentials require HTTPS");
    }
    const token = config.token || decodeURIComponent(remote.password);
    const username = config.username || decodeURIComponent(remote.username);
    remote.username = "";
    remote.password = "";
    cloneUrl = remote.href;
    if (token) {
      // Newlines/NUL cannot be represented by Git's line-based helper protocol.
      if (/[\r\n\0]/.test(username + token)) {
        throw new Error("Git credentials contain unsupported control characters");
      }
      const credentialUrl = new URL(remote.origin);
      credentialUrl.username = encodeURIComponent(username);
      credentialUrl.password = encodeURIComponent(token);
      credentials = `${credentialUrl.href}\n`;
    }
  }

  let repoAuthDir: string | undefined;
  try {
    let bootstrapAuthDir: string | undefined;
    try {
      const cloneOptions: string[] = [];
      if (credentials) {
        // Clone requires an empty destination. Use a private sibling directory
        // so credentials are available BEFORE the very first network request.
        bootstrapAuthDir = await mkdtemp(path.join(path.dirname(path.resolve(repoDir)), "cw-autoheal-auth-"));
        const helper = await writeCredentials(bootstrapAuthDir, credentials);
        cloneOptions.push("-c", "credential.helper=", "-c", `credential.helper=${helper}`);
      }
      await git(repoDir, [...cloneOptions, "clone", "--quiet", cloneUrl, "."], {
        env: isolatedGitEnvironment(process.env),
        timeout: 120_000,
      });
      await git(repoDir, ["remote", "set-url", "origin", cloneUrl]);
      await git(repoDir, ["config", "user.name", config.username || "clawweb"]);
      await git(repoDir, ["config", "user.email", config.email || `${config.username || "clawweb"}@antgroup.com`]);

      if (credentials) {
        // A new owned directory also lets failure cleanup remove partial writes
        // without deleting files that predated this operation.
        repoAuthDir = await mkdtemp(path.join(repoDir, ".git", "cw-autoheal-auth-"));
        const helper = await writeCredentials(repoAuthDir, credentials);
        await git(repoDir, ["config", "--local", "--replace-all", "credential.helper", ""]);
        await git(repoDir, ["config", "--local", "--add", "credential.helper", helper]);
      }
    } finally {
      if (bootstrapAuthDir) await rm(bootstrapAuthDir, { recursive: true, force: true });
    }
  } catch (err) {
    // Includes clone, config, partial credential writes and bootstrap cleanup
    // failures. Successful repos retain credentials until removeTempRepo().
    if (repoAuthDir) await rm(repoAuthDir, { recursive: true, force: true });
    throw err;
  }
}

function specPath(repoDir: string, packId: string, workflowId: string): string {
  return path.join(repoDir, "packs", packId, "workflows", `${workflowId}.yaml`);
}

function manifestPath(repoDir: string, packId: string): string {
  return path.join(repoDir, "packs", packId, "workflow.pack.yaml");
}

/**
 * Resolve where the workflow spec and pack manifest must be written inside a
 * cloned repo, matching the layout the branch base already carries.
 *
 * ClawMind's own deploys commit Pack folders at the repo ROOT ({packId}/workflows/…,
 * {packId}/workflow.pack.yaml). Writing a second copy under packs/{packId}/ when the
 * base tree already carries the root layout would leave two manifests in one commit
 * and trip the engine's readPackSnapshot uniqueness check ("Pack 不唯一或缺失"),
 * so once the baseline has the pack in either layout we overwrite in place.
 */
export function resolvePackPaths(
  repoDir: string,
  packId: string,
  workflowId: string,
): { specPath: string; manifestPath: string } {
  const rootManifest = path.join(repoDir, packId, "workflow.pack.yaml");
  const rootSpec = path.join(repoDir, packId, "workflows", `${workflowId}.yaml`);
  if (existsSync(rootManifest) || existsSync(rootSpec)) {
    return { specPath: rootSpec, manifestPath: rootManifest };
  }
  return { specPath: specPath(repoDir, packId, workflowId), manifestPath: manifestPath(repoDir, packId) };
}

export async function writeWorkflowSpecToRepo(
  repoDir: string,
  packId: string,
  workflowId: string,
  spec: WorkflowSpec,
): Promise<void> {
  const normalized = normalizeWorkflowSpec(spec);
  const { specPath: filePath } = resolvePackPaths(repoDir, packId, workflowId);
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, stringifyYaml(normalized, { lineWidth: 0 }));
}

export async function ensurePackManifest(
  repoDir: string,
  packId: string,
  workflowId: string,
): Promise<void> {
  const { manifestPath: filePath } = resolvePackPaths(repoDir, packId, workflowId);
  const packDir = path.dirname(filePath);
  await mkdir(packDir, { recursive: true });

  let manifest: Record<string, unknown> = { id: packId, workflows: [workflowId] };
  try {
    const existing = await readFile(filePath, "utf-8");
    const parsed = parseYaml(existing) as Record<string, unknown>;
    if (parsed && typeof parsed === "object") {
      manifest = parsed;
      const workflows = Array.isArray(manifest.workflows) ? manifest.workflows : [];
      if (!workflows.includes(workflowId)) {
        manifest.workflows = [...workflows, workflowId];
      }
    }
  } catch {
    // Manifest does not exist or is invalid — write a fresh one.
  }

  await writeFile(filePath, stringifyYaml(manifest, { lineWidth: 0 }));
}

/**
 * Remove the legacy packs/{packId} duplicate when the root-level pack layout is
 * authoritative. Pre-fix publishes wrote their two files under packs/{packId}/ while
 * engine deploys carried the pack at {packId}/ — a branch that has been through both
 * now holds two manifests, and the engine's readPackSnapshot rejects such trees with
 * "Pack 不唯一或缺失". Deleting the legacy copy (only when the root layout exists)
 * keeps the published tree single-manifest.
 */
export async function pruneLegacyPackCopy(repoDir: string, packId: string): Promise<boolean> {
  const rootManifest = path.join(repoDir, packId, "workflow.pack.yaml");
  const legacyDir = path.join(repoDir, "packs", packId);
  if (!existsSync(rootManifest) || !existsSync(legacyDir)) return false;
  await rm(legacyDir, { recursive: true, force: true });
  return true;
}

async function remoteRefExists(repoDir: string, ref: string): Promise<boolean> {
  try {
    await git(repoDir, ["rev-parse", "--verify", "--quiet", `${ref}^{commit}`]);
    return true;
  } catch {
    return false;
  }
}

/**
 * Check out the branch base for a save/deploy commit — before any spec/manifest writes.
 *
 * The pre-fix flow reset the branch from the cloned default-branch HEAD
 * (`checkout -B` without a start point), silently discarding the full Pack content
 * (scripts/assets) that a previous engine deploy had pushed to the branch. Every
 * auto-heal/editor publish then produced a tag whose Pack had no scripts, and the
 * engine's run-time materialization ("never read the editable Pack") failed with
 * `workdir 目录不存在: ~/.clawmind/runtime/{wfId}/{version}/scripts`.
 *
 * Base priority:
 *   1. origin/{branch}         — previous commit on this branch (fast-forward base)
 *   2. fallbackBase (optional) — e.g. origin/wf/{workflowId}, so the first save of
 *                                a deployed workflow still carries the full Pack
 *   3. cloned HEAD             — first-ever publish for the workflow
 *
 * Must run BEFORE the spec/manifest writes: switching branches rewrites the working
 * tree, and the writes must land on top of the final base.
 */
export async function prepareBranchBase(
  repoDir: string,
  branch: string,
  fallbackBase?: string,
): Promise<"branch" | "fallback" | "default"> {
  if (await remoteRefExists(repoDir, `origin/${branch}`)) {
    await git(repoDir, ["checkout", "--quiet", "-B", branch, `origin/${branch}`]);
    return "branch";
  }
  if (fallbackBase && (await remoteRefExists(repoDir, `origin/${fallbackBase}`))) {
    await git(repoDir, ["checkout", "--quiet", "-B", branch, `origin/${fallbackBase}`]);
    return "fallback";
  }
  await git(repoDir, ["checkout", "--quiet", "-B", branch]);
  return "default";
}

export async function commitAndPushSaved(
  repoDir: string,
  workflowId: string,
  note: string,
  author: GitAuthor,
): Promise<SaveGitResult> {
  const branch = `saved/${workflowId}`;

  // The branch base is prepared by prepareBranchBase before the spec/manifest writes
  // (see AutoHealSaveService.save), so the saved snapshot inherits the previous full
  // Pack content. This reset is a no-op when the base was prepared, and a
  // default-HEAD fallback for legacy callers that skip prepareBranchBase. The old
  // --orphan reset discarded the base and published a scripts-less snapshot.
  await git(repoDir, ["checkout", "-B", branch]);

  await git(repoDir, ["add", "."]);

  const message = `[auto-heal] save ${workflowId}: ${note || "auto"}`;
  await git(repoDir, ["commit", "-m", message, "--author", `${author.name} <${author.email}>`]);

  const commit = await git(repoDir, ["rev-parse", "HEAD"]);

  await git(repoDir, ["push", "--force-with-lease", "origin", branch]);

  return { branch, commit };
}

export async function getMaxDeployNumberFromGitTags(repoDir: string, workflowId: string): Promise<number> {
  const prefix = `deploy/${workflowId}/#`;
  let raw: string;
  try {
    raw = await git(repoDir, ["tag", "-l", `${prefix}*`]);
  } catch (error) {
    if (['GIT_OPERATION_TIMEOUT', 'GIT_TIMEOUT', 'GIT_QUEUE_FULL'].includes((error as { code?: string }).code ?? '')) throw error;
    return 0;
  }

  let max = 0;
  for (const line of raw.split("\n")) {
    const tagName = line.trim();
    if (!tagName) continue;
    const parsed = parseDeployTag(tagName);
    if (parsed && parsed.number > max) {
      max = parsed.number;
    }
  }
  return max;
}

export async function commitAndPushDeploy(
  repoDir: string,
  workflowId: string,
  deployNumber: number,
  version: number,
  note: string,
  author: GitAuthor,
): Promise<DeployGitResult> {
  const branch = `wf/${workflowId}`;
  const tagName = `deploy/${workflowId}/#${deployNumber}`;

  // The branch base is prepared by prepareBranchBase before the spec/manifest writes
  // (see AutoHealDeployService.deploy), so the commit inherits the previous full Pack
  // content. This reset is a no-op when the base was prepared, and a default-HEAD
  // fallback for legacy callers that skip prepareBranchBase.
  await git(repoDir, ["checkout", "-B", branch]);
  await git(repoDir, ["add", "."]);

  const message = `[${author.name}] deploy ${workflowId} #${deployNumber} v${version}`;
  await git(repoDir, ["commit", "-m", message, "--author", `${author.name} <${author.email}>`]);

  const annotation = `version=${version}\naction=deploy\nworkflowIds=${workflowId}`;
  await git(repoDir, ["tag", "-a", tagName, "-m", annotation]);

  const commit = await git(repoDir, ["rev-parse", "HEAD"]);

  await git(repoDir, [
    "push",
    "--atomic",
    "--force-with-lease",
    "origin",
    branch,
    `refs/tags/${tagName}`,
  ]);

  return { branch, tagName, commit, deployNumber };
}
