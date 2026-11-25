/**
 * ClawFlowsGitClient — isolated temporary-directory git operations for AutoHeal.
 *
 * ClawMind owns packsRoot and the long-lived pack repos. AutoHeal clones a
 * fresh temporary repository for every operation so it never competes with
 * local Pack state. Saves initialize an empty repo and fetch only wf; the clone
 * primitive remains for callers that explicitly need a checkout. Ref names:
 *
 *   saved Pack branch: wf/{workflowId}
 *   deploy tag:   deploy/{workflowId}/#N
 *   annotation:   version=N\naction=deploy\nworkflowIds={workflowId}
 */
import { executeGit } from "@avernet/clawweb-shared/server/services/git-execution";
import { createConfiguredTemporaryDirectory } from "@avernet/clawweb-shared/server/env";
import { packVersionGitEnvironment } from "./pack-version-git-env.js";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { closeSync, constants, existsSync, fstatSync, lstatSync, openSync, readFileSync, readSync, realpathSync } from "node:fs";
import { TextDecoder } from "node:util";
import path from "node:path";
import { stringify as stringifyYaml, parse as parseYaml, parseDocument, isSeq, isAlias, isNode, visit } from "yaml";
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
    throw Object.assign(new Error(`git ${args[commandIndex]} failed${detail}`), { code: (err as { code?: string })?.code, status });
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

export async function createTempRepo(): Promise<string> {
  return createConfiguredTemporaryDirectory("cw-autoheal");
}

export async function removeTempRepo(repoDir: string): Promise<void> {
  await rm(repoDir, { recursive: true, force: true });
}

export async function cloneClawflows(repoDir: string, config: ClawFlowsGitConfig): Promise<void> {
  return initializeClawflows(repoDir, config, false);
}

/** Initialize a read-only repository without cloning any default branch. */
export async function initClawflowsReadRepo(repoDir: string, config: ClawFlowsGitConfig, objectFormat: "sha1" | "sha256" = "sha1"): Promise<void> {
  return initializeClawflows(repoDir, config, true, objectFormat);
}

async function initializeClawflows(
  repoDir: string,
  config: ClawFlowsGitConfig,
  readOnly: boolean,
  objectFormat: "sha1" | "sha256" = "sha1",
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

  const readEnv = readOnly ? packVersionGitEnvironment() : undefined;
  const run = (args: string[], options?: { env?: NodeJS.ProcessEnv; timeout?: number; maxBuffer?: number }) =>
    git(repoDir, args, { ...options, env: readEnv ?? options?.env });
  let repoAuthDir: string | undefined;
  try {
    let bootstrapAuthDir: string | undefined;
    try {
      const cloneOptions: string[] = [];
      if (credentials && !readOnly) {
        // Clone requires an empty destination. Use a private sibling directory
        // so credentials are available BEFORE the very first network request.
        bootstrapAuthDir = await mkdtemp(path.join(path.dirname(path.resolve(repoDir)), "cw-autoheal-auth-"));
        const helper = await writeCredentials(bootstrapAuthDir, credentials);
        cloneOptions.push("-c", "credential.helper=", "-c", `credential.helper=${helper}`);
      }
      if (readOnly) {
        await run(["init", "--quiet", ...(objectFormat === "sha256" ? ["--object-format=sha256"] : [])]);
        await run(["remote", "add", "--", "origin", cloneUrl]);
      } else {
        await run([...cloneOptions, "clone", "--quiet", cloneUrl, "."], {
          env: isolatedGitEnvironment(process.env),
          timeout: 120_000,
        });
        await run(["remote", "set-url", "origin", cloneUrl]);
      }
      await run(["config", "user.name", config.username || "clawweb"]);
      await run(["config", "user.email", config.email || `${config.username || "clawweb"}@antgroup.com`]);

      if (credentials) {
        // A new owned directory also lets failure cleanup remove partial writes
        // without deleting files that predated this operation.
        repoAuthDir = await mkdtemp(path.join(repoDir, ".git", "cw-autoheal-auth-"));
        const helper = await writeCredentials(repoAuthDir, credentials);
        await run(["config", "--local", "--replace-all", "credential.helper", ""]);
        await run(["config", "--local", "--add", "credential.helper", helper]);
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

// Paths must remain inside the cloned tree, and must never follow a symlink.
function assertSafePath(repoDir: string, target: string): void {
  const relative = path.relative(repoDir, target);
  if (!relative || relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) {
    throw new Error("Invalid pack path");
  }
  let current = repoDir;
  for (const part of relative.split(path.sep)) {
    current = path.join(current, part);
    try {
      const stat = lstatSync(current);
      if (stat.isSymbolicLink()) throw new Error("Symlink in pack path");
      if (stat.isDirectory()) {
        try { lstatSync(path.join(current, ".git")); throw new Error("Nested Git repository (gitlink) in pack path"); }
        catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }
}

function validId(id: string): boolean {
  return /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(id) && !id.includes("..") && !id.endsWith(".") && !id.endsWith(".lock");
}

function workflowFile(entry: unknown): string {
  const file = typeof entry === "string" ? `workflows/${entry}.yaml`
    : entry && typeof entry === "object" && !Array.isArray(entry) ? (entry as Record<string, unknown>).file : undefined;
  if (typeof file !== "string" || !file || file !== file.trim() || file.includes("\\") || file.includes("\0")
    || /^[A-Za-z]:/.test(file) || file.split("/").some(part => !part || part === "." || part === "..")) {
    throw new Error("Invalid workflow path");
  }
  return file;
}

function readObject(file: string, label: string): Record<string, unknown> {
  let value: unknown;
  try { value = parseYaml(readFileSync(file, "utf8")); }
  catch { throw new Error(`Invalid ${label}`); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`Invalid ${label}`);
  return value as Record<string, unknown>;
}

// Validate the optional TaskGuard manifest fields before disregarding a
// different Pack. A syntactically valid YAML object with damaged metadata is
// not evidence that it is safe to treat the candidate as an unrelated Pack.
function validateCandidateMetadata(value: Record<string, unknown>, workflowIds: Set<string>): void {
  const record = (item: unknown): Record<string, unknown> => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("not an object");
    return item as Record<string, unknown>;
  };
  const required = (item: unknown): string => {
    if ((typeof item !== "string" && typeof item !== "number") || !String(item).trim()) throw new Error("required string");
    return String(item).trim();
  };
  const optional = (item: unknown) => { if (item !== undefined) required(item); };
  optional(value.title); optional(value.description);
  if (value.actions !== undefined) {
    if (!Array.isArray(value.actions)) throw new Error("actions");
    for (const item of value.actions) {
      const action = record(item);
      required(action.id); required(action.type); actionResourcePath(action.root);
      if (action.commands !== undefined) {
        for (const [name, script] of Object.entries(record(action.commands))) {
          if (!name.trim()) throw new Error("action name");
          actionResourcePath(script);
        }
      }
    }
  }
  if (value.facades !== undefined) {
    if (!Array.isArray(value.facades)) throw new Error("facades");
    const command = (item: unknown) => {
      const name = required(item);
      if (!/^[a-z0-9][a-z0-9_-]{1,63}$/.test(name) || name === "workflow") throw new Error("facade command");
    };
    for (const item of value.facades) {
      const facade = record(item);
      command(facade.command);
      if (!workflowIds.has(required(facade.defaultWorkflow))) throw new Error("facade workflow");
      if (facade.aliases !== undefined) {
        if (!Array.isArray(facade.aliases)) throw new Error("facade aliases");
        facade.aliases.forEach(command);
      }
      if (facade.help !== undefined) {
        const help = record(facade.help);
        optional(help.title); optional(help.summary);
        if (help.examples !== undefined) {
          if (!Array.isArray(help.examples)) throw new Error("facade examples");
          help.examples.forEach(required);
        }
      }
    }
  }
  if (value.skills !== undefined) {
    const skills = record(value.skills);
    if (skills.required !== undefined && (!Array.isArray(skills.required)
      || skills.required.some(item => typeof item !== "string" || !item.trim()))) throw new Error("skills");
  }
  if (value.compat !== undefined) {
    const compat = record(value.compat);
    optional(compat.minRuntimeVersion);
    if (compat.schemaVersion !== undefined && (typeof compat.schemaVersion !== "number"
      || !Number.isInteger(compat.schemaVersion) || compat.schemaVersion < 1)) throw new Error("schema version");
  }
}

// At most three candidate manifests are inspected; do not follow symlinks or
// allocate beyond the same 1 MiB per-file limit used by the snapshot reader.
const MANIFEST_LIMIT = 1024 * 1024;
function readBoundedManifest(repoDir: string, file: string): { text: string; value: Record<string, unknown>; id: string; size: number } {
  assertSafePath(repoDir, file);
  let fd: number | undefined;
  try {
    const before = lstatSync(file);
    if (!before.isFile() || before.nlink !== 1 || before.size > MANIFEST_LIMIT) throw new Error("mode or size");
    fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW);
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.dev !== before.dev || stat.ino !== before.ino || stat.size > MANIFEST_LIMIT) throw new Error("mode or size");
    const bytes = Buffer.allocUnsafe(MANIFEST_LIMIT + 1);
    let length = 0;
    while (length < bytes.length) {
      const count = readSync(fd, bytes, length, bytes.length - length, null);
      if (!count) break;
      length += count;
    }
    if (length > MANIFEST_LIMIT || bytes.subarray(0, length).includes(0)) throw new Error("size or binary");
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, length));
    const document = parseDocument(text);
    if (document.errors.length || document.warnings.length) throw new Error("invalid YAML");
    const parsed: unknown = document.toJS({ maxAliasCount: 100 });
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
    const value = parsed as Record<string, unknown>;
    if ((typeof value.id !== "string" && typeof value.id !== "number") || !String(value.id).trim()) throw new Error("identity");
    if (!Array.isArray(value.workflows) || !value.workflows.length) throw new Error("workflows");
    const hasLegacy = value.workflows.some(item => typeof item === "string");
    if (!(value.version === undefined && hasLegacy)
      && !((typeof value.version === "string" || typeof value.version === "number") && String(value.version).trim())) throw new Error("version");
    const workflowIds = new Set<string>();
    for (const item of value.workflows) {
      if (typeof item === "string") {
        if (!validId(item)) throw new Error("legacy workflow");
        workflowIds.add(item);
      } else {
        if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("workflow");
        const workflow = item as Record<string, unknown>;
        if ((typeof workflow.id !== "string" && typeof workflow.id !== "number") || !String(workflow.id).trim()) throw new Error("workflow id");
        const workflowId = String(workflow.id).trim();
        const workflowPath = actionResourcePath(workflow.file);
        if (!workflowId.startsWith("._") && !workflowPath.split("/").at(-1)?.startsWith("._")) workflowIds.add(workflowId);
      }
    }
    validateCandidateMetadata(value, workflowIds);
    return { text, value, id: String(value.id).trim(), size: length };
  } catch {
    throw new Error("Invalid pack manifest candidate");
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

/** TaskGuard action roots are pack-relative; command paths are relative to their action root. */
function actionResourcePath(value: unknown): string {
  if (typeof value !== "string") throw new Error("Invalid action resource path");
  const file = value.trim();
  if (!file || file.includes("\\") || file.includes("\0") || path.posix.isAbsolute(file)
    || path.win32.isAbsolute(file) || /^[A-Za-z]:/.test(file)
    || file.split("/").some(part => !part || part === "." || part === "..")) {
    throw new Error("Invalid action resource path");
  }
  return file;
}

function assertNotResource(manifest: Record<string, unknown>, relativeFile: string, repoDir: string, packDir: string, links: string[]): void {
  if (relativeFile.split("/").some(part => ["scripts", ".git"].includes(part.toLowerCase()))) {
    throw new Error("Workflow path conflicts with reserved resource");
  }
  if (manifest.actions === undefined) return;
  if (!Array.isArray(manifest.actions)) throw new Error("Invalid manifest actions");
  const rejectOverlap = (resource: string) => {
    if (relativeFile === resource || relativeFile.startsWith(`${resource}/`) || resource.startsWith(`${relativeFile}/`)) {
      throw new Error("Workflow path conflicts with action resource");
    }
    // A symlink or gitlink can make an apparently unrelated action path alias
    // the workflow (or escape the pack), so refuse such uncertainty before writing.
    const absolute = path.join(packDir, resource);
    assertNoGitlink(repoDir, [absolute], links);
    assertSafePath(repoDir, absolute);
  };
  for (const item of manifest.actions) {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("Invalid manifest action");
    const action = item as Record<string, unknown>;
    const root = actionResourcePath(action.root);
    rejectOverlap(root);
    if (action.commands === undefined) continue;
    if (!action.commands || typeof action.commands !== "object" || Array.isArray(action.commands)) {
      throw new Error("Invalid manifest action commands");
    }
    for (const command of Object.values(action.commands)) {
      rejectOverlap(`${root}/${actionResourcePath(command)}`);
    }
  }
}

/** Refuse manifest edits involving YAML anchors or aliases; mutation could change another field. */
function assertUnaliasedManifest(text: string): void {
  const document = parseDocument(text);
  let unsafe = document.errors.length > 0;
  visit(document, (_key, node) => { if (isAlias(node) || (node && typeof node === "object" && "anchor" in node && node.anchor)) unsafe = true; });
  if (unsafe) throw new Error("Cannot safely edit aliased or anchored pack manifest");
}

function assertWorkflowFile(repoDir: string, file: string, id: string, required: boolean): void {
  assertSafePath(repoDir, file);
  try {
    const stat = lstatSync(file);
    if (!stat.isFile() || stat.nlink !== 1) throw new Error("Invalid workflow file type");
    const raw = readObject(file, "workflow file");
    if (raw.id !== id || normalizeWorkflowSpec(raw).id !== id) throw new Error("Workflow identity mismatch");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      if (!required) return;
      throw new Error("Pack manifest workflow file missing");
    }
    throw error;
  }
}

/** Resolve the existing pack and its declared workflow path before any writes. */
export function resolvePackPaths(
  repoDir: string,
  packId: string,
  workflowId: string,
  links: string[] = [],
  discovered: string[] = [],
): { specPath: string; manifestPath: string } {
  if (!validId(packId) || !validId(workflowId)) throw new Error("Invalid pack or workflow id");
  const root = path.join(repoDir, "workflow.pack.yaml");
  const directDir = path.join(repoDir, packId);
  const nestedDir = path.join(repoDir, "packs", packId);
  const candidates: string[] = [];
  // A root pack can have ordinary files named after candidate directories.
  let rootExists = false;
  try { lstatSync(root); rootExists = true; }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  const candidate = (dir: string) => {
    const relative = path.relative(repoDir, dir);
    let current = repoDir;
    for (const part of relative.split(path.sep)) {
      current = path.join(current, part);
      try {
        const stat = lstatSync(current);
        if (stat.isSymbolicLink()) throw new Error("Symlink in pack path");
        if (!stat.isDirectory()) {
          if (rootExists && stat.isFile()) return;
          throw new Error("Invalid pack directory");
        }
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
        throw error;
      }
    }
    const file = path.join(dir, "workflow.pack.yaml");
    try { lstatSync(file); candidates.push(file); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  };
  if (rootExists) candidates.push(root);
  candidate(directDir);
  candidate(nestedDir);
  for (const relative of discovered) {
    const file = path.join(repoDir, relative);
    assertNoGitlink(repoDir, [file], links);
    if (!candidates.includes(file)) candidates.push(file);
  }
  // A repository-root manifest is authoritative when it identifies this Pack and
  // declares the workflow. In that case, everything below packs/ is an ordinary
  // resource and its nested manifests are not parsed or treated as candidates.
  // If the root does not match, retain the old fail-closed discovery behavior.
  let manifestBytes = 0;
  const inspectedByFile = new Map<string, { file: string; text: string; value: Record<string, unknown>; id: string; size: number }>();
  const inspect = (file: string) => {
    const existing = inspectedByFile.get(file);
    if (existing) return existing;
    const found = readBoundedManifest(repoDir, file);
    manifestBytes += found.size;
    if (manifestBytes > 8 * 1024 * 1024) throw new Error("Pack manifest discovery limit exceeded");
    const candidate = { file, ...found };
    inspectedByFile.set(file, candidate);
    return candidate;
  };
  const rootPackCandidate = rootExists ? inspect(root) : undefined;
  const rootDeclaresWorkflow = Array.isArray(rootPackCandidate?.value.workflows)
    ? (rootPackCandidate!.value.workflows as unknown[]).some(item =>
      typeof item === "string" ? item === workflowId
        : !!item && typeof item === "object" && !Array.isArray(item) && (item as Record<string, unknown>).id === workflowId)
    : false;
  const selectedFiles = rootPackCandidate?.id === packId && rootDeclaresWorkflow
    ? candidates.filter(file => file === root || !path.relative(repoDir, file).startsWith("packs/"))
    : candidates;
  for (const file of selectedFiles) inspect(file);
  const selectedCandidates = selectedFiles.map(file => inspectedByFile.get(file)!);
  const matchingCandidates = selectedCandidates.filter(candidate => candidate.id === packId);
  if (matchingCandidates.length > 1) throw new Error("Pack manifest conflict");

  let filePath: string;
  let workflows: unknown[] = [];
  let manifest: Record<string, unknown> = {};
  let legacy = false;
  if (matchingCandidates.length) {
    const selected = matchingCandidates[0];
    filePath = selected.file;
    if (filePath !== root && path.basename(path.dirname(filePath)) !== packId) throw new Error("Pack manifest location conflict");
    manifest = selected.value;
    if (manifest.id !== packId || !Array.isArray(manifest.workflows) || !manifest.workflows.length) {
      throw new Error("Invalid pack manifest identity or workflows");
    }
    workflows = manifest.workflows;
    legacy = workflows.some(item => typeof item === "string");
    // The old writer omitted version entirely for its string-list manifests.
    // Only this verifiable legacy form gets the compatibility default 1.0.0;
    // existing explicit versions (including numeric TaskGuard versions) are retained.
    if (!(manifest.version === undefined && legacy)
      && !((typeof manifest.version === "string" || typeof manifest.version === "number") && String(manifest.version).trim())) {
      throw new Error("Invalid pack manifest version");
    }
    const ids = new Set<string>();
    const files = new Set<string>();
    for (const item of workflows) {
      const id = typeof item === "string" ? item : item && typeof item === "object" && !Array.isArray(item)
        ? (item as Record<string, unknown>).id : undefined;
      if (typeof id !== "string" || !validId(id)) throw new Error("Invalid pack manifest workflow");
      const relative = workflowFile(item);
      if (ids.has(id) || files.has(relative)) throw new Error("Pack manifest workflow conflict");
      ids.add(id); files.add(relative);
      if (legacy) {
        assertNotResource(manifest, relative, repoDir, path.dirname(filePath), links);
        const member = path.join(path.dirname(filePath), relative);
        assertNoGitlink(repoDir, [member], links);
        assertWorkflowFile(repoDir, member, id, true);
      }
    }
    const includesWorkflow = workflows.some(item => typeof item === "string" ? item === workflowId
      : !!item && typeof item === "object" && !Array.isArray(item) && (item as Record<string, unknown>).id === workflowId);
    if (legacy || !includesWorkflow) assertUnaliasedManifest(selected.text);
  } else {
    const directSpec = path.join(directDir, "workflows", `${workflowId}.yaml`);
    filePath = existsSync(directSpec) ? path.join(directDir, "workflow.pack.yaml") : path.join(nestedDir, "workflow.pack.yaml");
    if (candidates.includes(filePath)) throw new Error("Pack manifest identity conflict");
    assertSafePath(repoDir, filePath);
  }
  const matches = workflows.filter(item => typeof item === "string" ? item === workflowId
    : !!item && typeof item === "object" && !Array.isArray(item) && (item as Record<string, unknown>).id === workflowId);
  const relativeFile = matches.length ? workflowFile(matches[0]) : `workflows/${workflowId}.yaml`;
  if (workflows.some(item => item !== matches[0] && workflowFile(item) === relativeFile)) {
    throw new Error("Shared workflow path");
  }
  assertNotResource(manifest, relativeFile, repoDir, path.dirname(filePath), links);
  const target = path.join(path.dirname(filePath), relativeFile);
  if (target === filePath) throw new Error("Workflow path conflicts with manifest");
  assertNoGitlink(repoDir, [target], links);
  // Custom paths are only writable when the existing file proves it is the
  // declared workflow. Never turn a missing or unrelated resource into a spec.
  assertWorkflowFile(repoDir, target, workflowId, matches.length > 0 && (legacy || relativeFile !== `workflows/${workflowId}.yaml`));
  let targetStat: ReturnType<typeof lstatSync> | undefined;
  try { targetStat = lstatSync(target); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  for (const item of workflows) {
    if (item === matches[0]) continue;
    const other = path.join(path.dirname(filePath), workflowFile(item));
    assertNoGitlink(repoDir, [other], links);
    assertSafePath(repoDir, other);
    try {
      const stat = lstatSync(other);
      if (targetStat && stat.dev === targetStat.dev && stat.ino === targetStat.ino) {
        throw new Error("Shared workflow file");
      }
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  }
  // Different identities are independent only when their declared resources
  // do not own the files this operation will change.
  for (const otherPack of selectedCandidates) {
    if (otherPack.file === filePath) continue;
    const collision = (otherPack.value.workflows as unknown[]).some(item => {
      const id = typeof item === "string" ? item : (item as Record<string, unknown>).id;
      return (typeof id === "string" || typeof id === "number") && String(id).trim() === workflowId;
    });
    if (collision) throw new Error("Workflow ID conflict across Packs");
    const otherDir = path.dirname(otherPack.file);
    const destinations = [target, filePath];
    const checkResource = (relative: string, directory: boolean) => {
      const resource = path.join(otherDir, relative);
      assertNoGitlink(repoDir, [resource], links);
      assertSafePath(repoDir, resource);
      let resourceStat: ReturnType<typeof lstatSync> | undefined;
      try { resourceStat = lstatSync(resource); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
      for (const destination of destinations) {
        const within = path.relative(resource, destination);
        if (destination === resource || (directory && within !== ".." && !within.startsWith(`..${path.sep}`) && !path.isAbsolute(within))) {
          throw new Error("Pack resource conflict");
        }
        // On case-insensitive filesystems different spellings can name the
        // same inode. For action roots also inspect existing ancestors, even
        // when the workflow file itself has not yet been created.
        let current = destination;
        while (resourceStat) {
          try {
            const stat = lstatSync(current);
            if (stat.dev === resourceStat.dev && stat.ino === resourceStat.ino) throw new Error("Pack resource conflict");
          } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
          if (!directory || current === repoDir || path.dirname(current) === current) break;
          current = path.dirname(current);
        }
      }
    };
    // The manifest itself is a Pack resource, even if its YAML also looks
    // like a valid workflow. Reuse inode checks for filesystem aliases.
    checkResource(path.basename(otherPack.file), false);
    for (const member of otherPack.value.workflows as unknown[]) {
      const relative = typeof member === "string" ? `workflows/${member}.yaml`
        : actionResourcePath((member as Record<string, unknown>).file);
      checkResource(relative, false);
    }
    if (Array.isArray(otherPack.value.actions)) {
      for (const item of otherPack.value.actions) {
        const action = item as Record<string, unknown>;
        const root = actionResourcePath(action.root);
        checkResource(root, true);
        if (action.commands && typeof action.commands === "object" && !Array.isArray(action.commands)) {
          for (const command of Object.values(action.commands)) checkResource(`${root}/${actionResourcePath(command)}`, false);
        }
      }
    }
  }
  return { specPath: target, manifestPath: filePath };
}

/** Inspect both index and HEAD: an uninitialized submodule looks like a directory. */
async function gitlinks(repoDir: string): Promise<string[]> {
  const top = await git(repoDir, ["rev-parse", "--show-toplevel"]);
  if (realpathSync(top) !== realpathSync(repoDir)) throw new Error("Expected Git repository root");
  const index = await git(repoDir, ["ls-files", "--stage", "-z"]);
  let tree = "";
  let symbolic: string | undefined;
  try { symbolic = await git(repoDir, ["symbolic-ref", "-q", "HEAD"]); }
  catch (error) { if ((error as { status?: number }).status !== 1) throw error; }
  let unborn = false;
  if (symbolic) {
    if (!symbolic.startsWith("refs/heads/")) throw new Error("Invalid symbolic HEAD");
    try { await git(repoDir, ["show-ref", "--verify", "--quiet", symbolic]); }
    catch (error) {
      if ((error as { status?: number }).status !== 1) throw error;
      unborn = true;
    }
  }
  if (!unborn) tree = await git(repoDir, ["ls-tree", "-r", "-z", "HEAD"]);
  return [...index.split("\0"), ...tree.split("\0")].flatMap(record => {
    const tab = record.indexOf("\t");
    return tab >= 0 && record.startsWith("160000 ") ? [record.slice(tab + 1)] : [];
  });
}

function assertNoGitlink(repoDir: string, paths: string[], links: string[]): void {
  for (const target of paths) {
    const relative = path.relative(repoDir, target).split(path.sep).join("/");
    for (const link of links) {
      if (relative === link || relative.startsWith(`${link}/`)) throw new Error("Pack path enters a Git submodule (gitlink)");
      let linkStat: ReturnType<typeof lstatSync> | undefined;
      try { linkStat = lstatSync(path.join(repoDir, link)); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
      if (!linkStat && (relative.toLowerCase() === link.toLowerCase() || relative.toLowerCase().startsWith(`${link.toLowerCase()}/`))) {
        throw new Error("Pack path may enter a missing Git submodule (gitlink)");
      }
      let current = target;
      while (linkStat) {
        try {
          const stat = lstatSync(current);
          if (stat.dev === linkStat.dev && stat.ino === linkStat.ino) throw new Error("Pack path enters a Git submodule (gitlink)");
        } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
        if (current === repoDir || path.dirname(current) === current) break;
        current = path.dirname(current);
      }
    }
  }
}

/** Discover every manifest a versioned snapshot can see, plus ignored local
 * manifests whose data must not be overwritten. Index entries catch sparse or
 * deleted checkouts; untracked entries catch files a later `git add .` will add. */
async function discoverPackManifests(repoDir: string): Promise<string[]> {
  const options = { cwd: repoDir, env: isolatedGitEnvironment(process.env), timeout: 60_000, maxBuffer: 16 * 1024 * 1024 };
  const staged = await executeGit(["ls-files", "--stage", "-z"], options);
  const untracked = await executeGit(["ls-files", "--others", "--exclude-standard", "-z"], options);
  const ignored = await executeGit(["ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--",
    ":(top,literal)workflow.pack.yaml", ":(top,glob)*/workflow.pack.yaml", ":(top,glob)packs/*/workflow.pack.yaml"], options);
  const decode = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  const records = (output: Buffer): Buffer[] => {
    if (!output.length) return [];
    if (output[output.length - 1] !== 0) throw new Error("Invalid Git manifest discovery");
    const result: Buffer[] = [];
    let start = 0;
    for (let i = 0; i < output.length; i++) {
      if (output[i] === 0) { result.push(output.subarray(start, i)); start = i + 1; }
    }
    return result;
  };
  const manifestPaths = new Set<string>();
  const directories = new Set<string>();
  const add = (bytes: Buffer, countDirectory: boolean) => {
    let file: string;
    try { file = decode.decode(bytes); } catch { throw new Error("Invalid UTF-8 Git manifest discovery path"); }
    // Untracked embedded repositories are added by Git as gitlinks, not trees.
    if (file.endsWith("/")) return;
    const parts = file.split("/");
    if (parts.some(part => !part || part === "." || part === "..")) throw new Error("Invalid Git manifest discovery path");
    if (countDirectory && parts.length > 1) {
      if (parts[0] !== "packs") directories.add(parts[0]);
      else if (parts.length > 2) directories.add(`packs/${parts[1]}`);
    }
    if ((parts.length === 1 && parts[0] === "workflow.pack.yaml")
      || (parts.length === 2 && parts[1] === "workflow.pack.yaml")
      || (parts.length === 3 && parts[0] === "packs" && parts[2] === "workflow.pack.yaml")) {
      manifestPaths.add(file);
    }
    if (directories.size > 4096 || manifestPaths.size > 4097 || Buffer.byteLength(file, "utf8") > 8192) {
      throw new Error("Git manifest discovery limit exceeded");
    }
  };
  for (const record of records(staged)) {
    const tab = record.indexOf(9);
    if (tab < 0 || !/^(?:100644|100755|120000|160000) [0-9a-f]{40}(?:[0-9a-f]{24})? 0$/.test(record.subarray(0, tab).toString("ascii"))) {
      throw new Error("Invalid or unmerged Git index during manifest discovery");
    }
    add(record.subarray(tab + 1), true);
  }
  for (const record of records(untracked)) add(record, true);
  for (const record of records(ignored)) add(record, false);
  return [...manifestPaths];
}

async function safePackPaths(repoDir: string, packId: string, workflowId: string) {
  if (!validId(packId) || !validId(workflowId)) throw new Error("Invalid pack or workflow id");
  const links = await gitlinks(repoDir);
  // Check candidates before reading their manifests, even in an initialized submodule.
  assertNoGitlink(repoDir, [path.join(repoDir, "workflow.pack.yaml"),
    path.join(repoDir, packId, "workflow.pack.yaml"), path.join(repoDir, "packs", packId, "workflow.pack.yaml")], links);
  const discovered = await discoverPackManifests(repoDir);
  const paths = resolvePackPaths(repoDir, packId, workflowId, links, discovered);
  assertNoGitlink(repoDir, [paths.specPath, paths.manifestPath], links);
  return paths;
}

export async function writeWorkflowSpecToRepo(
  repoDir: string,
  packId: string,
  workflowId: string,
  spec: WorkflowSpec,
): Promise<void> {
  const normalized = normalizeWorkflowSpec(spec);
  if (normalized.id !== workflowId) throw new Error("Workflow identity mismatch");
  const { specPath: filePath } = await safePackPaths(repoDir, packId, workflowId);
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, stringifyYaml(normalized, { lineWidth: 0 }));
}

export async function ensurePackManifest(repoDir: string, packId: string, workflowId: string): Promise<void> {
  const { manifestPath: filePath } = await safePackPaths(repoDir, packId, workflowId);
  await mkdir(path.dirname(filePath), { recursive: true });
  if (!existsSync(filePath)) {
    await writeFile(filePath, stringifyYaml({ id: packId, version: "1.0.0", workflows: [{ id: workflowId, file: `workflows/${workflowId}.yaml` }] }, { lineWidth: 0 }), { flag: "wx" });
    return;
  }
  const { value: manifest, text } = readBoundedManifest(repoDir, filePath);
  const workflows = manifest.workflows as unknown[];
  const hasWorkflow = workflows.some(item => typeof item === "string" ? item === workflowId
    : !!item && typeof item === "object" && !Array.isArray(item) && (item as Record<string, unknown>).id === workflowId);
  const legacy = workflows.some(item => typeof item === "string");
  if (hasWorkflow && !legacy) return;
  // Update only the legacy representation; keep YAML comments and all metadata.
  const document = parseDocument(text);
  const sequence = document.get("workflows", true);
  if (document.errors.length || !isSeq(sequence)) throw new Error("Invalid pack manifest");
  if (legacy) {
    if (manifest.version === undefined) document.set("version", "1.0.0");
    workflows.forEach((item, index) => {
      if (typeof item !== "string") return;
      const old = sequence.items[index];
      const replacement = document.createNode({ id: item, file: `workflows/${item}.yaml` });
      if (isNode(old)) {
        replacement.comment = old.comment;
        replacement.commentBefore = old.commentBefore;
        replacement.spaceBefore = old.spaceBefore;
      }
      sequence.items[index] = replacement;
    });
  }
  if (!hasWorkflow) sequence.add({ id: workflowId, file: `workflows/${workflowId}.yaml` });
  await writeFile(filePath, document.toString({ lineWidth: 0 }));
}

/** Legacy duplicates are never removed automatically: either copy may contain unique data. */
export async function pruneLegacyPackCopy(_repoDir: string, _packId: string): Promise<boolean> {
  return false;
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

/** @deprecated No DB-first callback or immutable lease. Use withPreparedWorkflowSpec. */
export async function commitAndPushSaved(
  _repoDir: string, _workflowId: string, _note: string, _author: GitAuthor,
): Promise<SaveGitResult> {
  throw new Error('Legacy saved writer retired; use the prepared DB-first save core');
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
