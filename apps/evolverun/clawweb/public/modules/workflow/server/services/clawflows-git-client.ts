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
    await git(repoDir, ["checkout", "--orphan", branch]);
  } catch (error) {
    if (['GIT_OPERATION_TIMEOUT', 'GIT_TIMEOUT', 'GIT_QUEUE_FULL'].includes((error as { code?: string }).code ?? '')) throw error;
    // Branch may already exist locally (e.g. reused temp dir); force-create it.
    await git(repoDir, ["checkout", "-B", branch]);
  }

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
