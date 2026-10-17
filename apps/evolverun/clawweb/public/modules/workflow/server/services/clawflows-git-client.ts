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
import { execFileSync } from "node:child_process";
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
  return {
    ...env,
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_CONFIG_GLOBAL: "/dev/null",
  };
}

function git(
  cwd: string,
  args: string[],
  options?: { env?: NodeJS.ProcessEnv; timeout?: number; maxBuffer?: number },
): string {
  try {
    const result = execFileSync(
      "git",
      args,
      {
        cwd,
        env: options?.env ?? isolatedGitEnvironment(process.env),
        encoding: "utf-8",
        timeout: options?.timeout ?? 60_000,
        maxBuffer: options?.maxBuffer ?? 16 * 1024 * 1024,
      },
    );
    return result.trim();
  } catch (err: unknown) {
    const stderr = typeof (err as { stderr?: string }).stderr === "string"
      ? (err as { stderr: string }).stderr.trim()
      : "";
    const stdout = typeof (err as { stdout?: string }).stdout === "string"
      ? (err as { stdout: string }).stdout.trim()
      : "";
    const message = (err as { message?: string }).message ?? String(err);
    throw new Error(`git ${args.join(" ")} in ${cwd}: ${stderr || stdout || message}`);
  }
}

function parseDeployTag(tagName: string): { workflowId: string; number: number } | null {
  const match = tagName.match(/^deploy\/(.+)\/#(\d+)$/);
  if (!match) return null;
  return { workflowId: match[1], number: parseInt(match[2], 10) };
}

function getHostFromRemoteUrl(remoteUrl: string): string {
  const hostMatch = remoteUrl.match(/https?:\/\/([^/]+)/);
  return hostMatch?.[1] ?? "";
}

async function configureGitCredential(
  repoDir: string,
  remoteUrl: string,
  username: string,
  token: string | undefined,
): Promise<void> {
  const host = getHostFromRemoteUrl(remoteUrl);
  if (!host || !token) return;

  git(repoDir, ["config", "credential.helper", "cache --timeout 86400"]);
  execFileSync(
    "git",
    ["credential", "approve"],
    {
      cwd: repoDir,
      input: `protocol=https\nhost=${host}\nusername=${username}\npassword=${token}\n`,
      encoding: "utf-8",
      timeout: 5_000,
      env: isolatedGitEnvironment(process.env),
    },
  );
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

  // Configure credentials before cloning so that the clone operation can
  // authenticate against the remote (e.g. code.alipay.com).
  if (config.token) {
    await configureGitCredential(repoDir, config.remoteUrl, config.username, config.token);
  }

  git(repoDir, ["clone", "--quiet", config.remoteUrl, "."], {
    env: isolatedGitEnvironment(process.env),
    timeout: 120_000,
  });

  git(repoDir, ["config", "user.name", config.username || "clawweb"]);
  git(repoDir, ["config", "user.email", config.email || `${config.username || "clawweb"}@antgroup.com`]);
}

function specPath(repoDir: string, packId: string, workflowId: string): string {
  return path.join(repoDir, "packs", packId, "workflows", `${workflowId}.yaml`);
}

function manifestPath(repoDir: string, packId: string): string {
  return path.join(repoDir, "packs", packId, "workflow.pack.yaml");
}

export async function writeWorkflowSpecToRepo(
  repoDir: string,
  packId: string,
  workflowId: string,
  spec: WorkflowSpec,
): Promise<void> {
  const normalized = normalizeWorkflowSpec(spec);
  const filePath = specPath(repoDir, packId, workflowId);
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, stringifyYaml(normalized, { lineWidth: 0 }));
}

export async function ensurePackManifest(
  repoDir: string,
  packId: string,
  workflowId: string,
): Promise<void> {
  const filePath = manifestPath(repoDir, packId);
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

export async function commitAndPushSaved(
  repoDir: string,
  workflowId: string,
  note: string,
  author: GitAuthor,
): Promise<SaveGitResult> {
  const branch = `saved/${workflowId}`;

  try {
    git(repoDir, ["checkout", "--orphan", branch]);
  } catch {
    // Branch may already exist locally (e.g. reused temp dir); force-create it.
    git(repoDir, ["checkout", "-B", branch]);
  }

  git(repoDir, ["add", "."]);

  const message = `[auto-heal] save ${workflowId}: ${note || "auto"}`;
  git(repoDir, ["commit", "-m", message, "--author", `${author.name} <${author.email}>`]);

  const commit = git(repoDir, ["rev-parse", "HEAD"]);

  git(repoDir, ["push", "--force-with-lease", "origin", branch]);

  return { branch, commit };
}

export function getMaxDeployNumberFromGitTags(repoDir: string, workflowId: string): number {
  const prefix = `deploy/${workflowId}/#`;
  let raw: string;
  try {
    raw = git(repoDir, ["tag", "-l", `${prefix}*`]);
  } catch {
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

  git(repoDir, ["checkout", "-B", branch]);
  git(repoDir, ["add", "."]);

  const message = `[${author.name}] deploy ${workflowId} #${deployNumber} v${version}`;
  git(repoDir, ["commit", "-m", message, "--author", `${author.name} <${author.email}>`]);

  const annotation = `version=${version}\naction=deploy\nworkflowIds=${workflowId}`;
  git(repoDir, ["tag", "-a", tagName, "-m", annotation]);

  const commit = git(repoDir, ["rev-parse", "HEAD"]);

  git(repoDir, [
    "push",
    "--atomic",
    "--force-with-lease",
    "origin",
    branch,
    `refs/tags/${tagName}`,
  ]);

  return { branch, tagName, commit, deployNumber };
}
