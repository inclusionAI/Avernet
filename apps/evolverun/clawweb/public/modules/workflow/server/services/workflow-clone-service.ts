import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseDocument, parse as parseYaml, stringify as stringifyYaml } from "yaml";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { withGitOperation } from "@avernet/clawweb-shared/server/services/git-execution";
import { resolveAutoHealGitSettings } from "../config/auto-heal-git-config.js";
import { normalizeWorkflowSpec, validateWorkflowSemantics } from "../workflow.js";
import type { WorkflowSpecRepository, WorkflowSpecSummary } from "../repositories/workflow-spec-repository.js";
import { FacadeBindingRepository } from "../repositories/facade-binding-repository.js";
import {
  createTempRepo,
  removeTempRepo,
  type ClawFlowsGitConfig,
  type GitAuthor,
} from "./clawflows-git-client.js";
import { initSavedWorkflowRepo, workflowSavedCommitMessage, workflowSavedGitRunner } from "./workflow-saved-ref.js";
import {
  createPreparedGitBundle,
  gitOidPattern,
  readPreparedPackMetadata,
  validPackGitId,
} from "./pack-git-prepared.js";
import {
  PackCandidateOutcomeUnknownError,
  pushPreparedPackCandidate,
  type PreparedPackCandidate,
  type SavePackCandidateResult,
} from "./pack-candidate-save.js";
import { readPackScriptsFromRemote } from "./pack-script-read-service.js";
import type { PackScriptSnapshot, PackScriptVersion } from "./pack-script-snapshot.js";

export type WorkflowCloneCandidate = {
  workflowId: string;
  title: string;
  packId: string | null;
  updatedAt: number;
};

export type WorkflowClonePreview = {
  sourceWorkflowId: string;
  sourcePackId: string;
  sourceTitle: string;
  baseCommit: string;
  scriptCount: number;
};

export type WorkflowCloneFacade = { command?: string; remark?: string | null };

export type WorkflowCloneInput = {
  sourceWorkflowId: string;
  targetWorkflowId: string;
  targetTitle: string;
  facade: WorkflowCloneFacade;
  actorId: string;
};

export type WorkflowCloneResult = WorkflowClonePreview & {
  targetWorkflowId: string;
  targetPackId: string;
  branch: string;
  commit: string;
};

type PreparedClone = {
  targetWorkflowId: string;
  targetPackId: string;
  targetWorkflowYaml: string;
  config: ClawFlowsGitConfig;
  author: GitAuthor;
  scripts: PackScriptSnapshot["files"];
};

export type WithPreparedClonedPack = <T>(
  input: PreparedClone,
  callback: (prepared: PreparedPackCandidate, push: () => Promise<SavePackCandidateResult>) => Promise<T>,
) => Promise<T>;

export type WorkflowCloneServiceDeps = Readonly<{
  db: IDatabase;
  workflowSpecRepo: WorkflowSpecRepository;
  facadeRepo: FacadeBindingRepository;
  gitConfig: ClawFlowsGitConfig;
  gitAuthor: GitAuthor;
  readSnapshot?: (input: {
    db: IDatabase;
    gitConfig: ClawFlowsGitConfig;
    gitAuthor: GitAuthor;
    workflowId: string;
    packId: string;
    version: PackScriptVersion;
    includeVersionFiles: true;
  }) => Promise<PackScriptSnapshot>;
  prepareClonedPack?: WithPreparedClonedPack;
}>;

export class WorkflowCloneError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "WorkflowCloneError";
  }
}

const MAX_TITLE_BYTES = 256;
const MAX_FACADE_BYTES = 128;
const MAX_REMARK_BYTES = 256;
const MAX_SCRIPT_BYTES = 1024 * 1024;
const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/;
const RESERVED_WORKFLOW_ID_PATTERN = /^__.+__$/;
const MAX_TOTAL_SCRIPT_BYTES = 8 * 1024 * 1024;

function requiredText(value: unknown, field: string, maxBytes: number): string {
  if (typeof value !== "string") throw new WorkflowCloneError(400, "INVALID_REQUEST", `${field} is required`);
  const text = value.trim();
  if (!text || Buffer.byteLength(text) > maxBytes) throw new WorkflowCloneError(400, "INVALID_REQUEST", `${field} is invalid`);
  return text;
}

function toCandidate(row: WorkflowSpecSummary): WorkflowCloneCandidate {
  const updated = new Date(row.gmt_modified).getTime();
  return {
    workflowId: row.workflow_id,
    title: row.title?.trim() || row.workflow_id,
    packId: row.pack_id,
    updatedAt: Number.isFinite(updated) ? updated : 0,
  };
}

function snapshotScripts(snapshot: PackScriptSnapshot): PackScriptSnapshot["files"] {
  return (snapshot.versionFiles ?? []).flatMap((entry) => {
    if (!entry.path.startsWith("scripts/")) return [];
    const content = Buffer.from(entry.contentBase64, "base64").toString("utf8");
    return [{ path: entry.path, content, mode: entry.mode }];
  });
}

function decodeBase64File(snapshot: PackScriptSnapshot, file: string): string {
  const entry = snapshot.versionFiles?.find((item) => item.path === file);
  if (!entry) throw new WorkflowCloneError(404, "SOURCE_YAML_NOT_FOUND", "Source workflow YAML was not found");
  const bytes = Buffer.from(entry.contentBase64, "base64");
  return bytes.toString("utf8");
}

function sourceWorkflowFile(snapshot: PackScriptSnapshot): string {
  const manifest = decodeBase64File(snapshot, "workflow.pack.yaml");
  const parsed = parseYaml(manifest) as { workflows?: unknown } | null;
  const workflows = parsed?.workflows;
  if (!Array.isArray(workflows)) throw new WorkflowCloneError(409, "SOURCE_PACK_INVALID", "Source Pack manifest is invalid");
  for (const item of workflows) {
    if (typeof item === "string") {
      if (item === snapshot.workflowId) return `workflows/${item}.yaml`;
      continue;
    }
    if (item && typeof item === "object" && (item as Record<string, unknown>).id === snapshot.workflowId) {
      const file = (item as Record<string, unknown>).file;
      if (typeof file !== "string" || !file || file.startsWith("/")) continue;
      return file;
    }
  }
  throw new WorkflowCloneError(404, "SOURCE_YAML_NOT_FOUND", "Source workflow is not declared by its Pack");
}

function buildTargetYaml(sourceYaml: string, targetWorkflowId: string, targetTitle: string): string {
  const document = parseDocument(sourceYaml);
  if (document.errors.length || !document.contents) {
    throw new WorkflowCloneError(409, "SOURCE_YAML_INVALID", "Source workflow YAML is invalid");
  }
  document.set("id", targetWorkflowId);
  document.set("title", targetTitle);
  // The clone owns an independent Pack and a new optional command. Do not leak
  // those source identities into later saves of this workflow.
  document.delete("packId");
  document.delete("facade");
  return document.toString({ lineWidth: 0 });
}

function buildTargetManifest(targetPackId: string, targetWorkflowId: string): string {
  return stringifyYaml(
    {
      id: targetPackId,
      version: "1.0.0",
      workflows: [{ id: targetWorkflowId, file: `workflows/${targetWorkflowId}.yaml` }],
    },
    { lineWidth: 0 },
  );
}

function validateScripts(files: PackScriptSnapshot["files"]): PackScriptSnapshot["files"] {
  if (!Array.isArray(files)) throw new WorkflowCloneError(409, "SOURCE_SCRIPTS_INVALID", "Source scripts are invalid");
  const seen = new Set<string>();
  let total = 0;
  for (const file of files) {
    if (
      !file
      || typeof file.path !== "string"
      || !file.path.startsWith("scripts/")
      || file.path.includes("\\")
      || file.path.split("/").some((part) => !part || part === "." || part === ".." || part.toLowerCase() === ".git")
      || !/\.(?:py|js|ts|sh|bash)$/.test(file.path)
      || seen.has(file.path)
      || typeof file.content !== "string"
      || !["100644", "100755"].includes(file.mode)
    ) {
      throw new WorkflowCloneError(409, "SOURCE_SCRIPTS_INVALID", "Source scripts are invalid");
    }
    const size = Buffer.byteLength(file.content);
    if (size > MAX_SCRIPT_BYTES || (total += size) > MAX_TOTAL_SCRIPT_BYTES) {
      throw new WorkflowCloneError(409, "SOURCE_SCRIPTS_TOO_LARGE", "Source scripts exceed the copy limit");
    }
    seen.add(file.path);
  }
  return files;
}

async function safeWriteFile(root: string, relativePath: string, content: string): Promise<void> {
  const target = path.resolve(root, relativePath);
  if (!target.startsWith(`${root}${path.sep}`)) throw new Error("Invalid Pack file path");
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, content, "utf8");
}

/** Build one root Pack from YAML and the source Pack's scripts. The target wf ref
 * must not exist; this helper never reads or writes the source workflow ref. */
async function withPreparedClonedPackInternal<T>(
  input: PreparedClone,
  callback: (prepared: PreparedPackCandidate, push: () => Promise<SavePackCandidateResult>) => Promise<T>,
): Promise<T> {
  const targetWorkflowFile = `workflows/${input.targetWorkflowId}.yaml`;
  const targetManifest = buildTargetManifest(input.targetPackId, input.targetWorkflowId);
  return withGitOperation({}, async () => {
    const repoDir = await createTempRepo();
    let pushStarted = false;
    try {
      const savedState = await initSavedWorkflowRepo(repoDir, input.targetWorkflowId, input.config);
      if (savedState.wfHead !== null || savedState.commit !== null) {
        throw new WorkflowCloneError(409, "TARGET_WORKFLOW_ALREADY_EXISTS", "Target workflow Git branch already exists");
      }
      const { text } = workflowSavedGitRunner(repoDir);
      await text(["read-tree", "--empty"]);
      await safeWriteFile(repoDir, "workflow.pack.yaml", targetManifest);
      await safeWriteFile(repoDir, targetWorkflowFile, input.targetWorkflowYaml);
      const stage = async (relativePath: string, mode: "100644" | "100755") => {
        const object = await text(["hash-object", "-w", "--no-filters", "--", relativePath]);
        if (!gitOidPattern.test(object)) throw new Error("Invalid Git object");
        await text(["update-index", "--add", "--cacheinfo", mode, object, relativePath]);
      };
      await stage("workflow.pack.yaml", "100644");
      await stage(targetWorkflowFile, "100644");
      for (const script of input.scripts) {
        await safeWriteFile(repoDir, script.path, script.content);
        await stage(script.path, script.mode);
      }
      const tree = await text(["write-tree"]);
      const message = await workflowSavedCommitMessage(repoDir, input.targetWorkflowId, tree, {
        packId: input.targetPackId,
        packPath: "",
        workflowFile: targetWorkflowFile,
      });
      const commit = await text([
        "-c", `user.name=${input.author.name}`,
        "-c", `user.email=${input.author.email}`,
        "commit-tree", tree,
        "-m", message,
      ]);
      if (!gitOidPattern.test(commit)) throw new Error("Invalid Git commit");
      const metadata = await readPreparedPackMetadata(repoDir, input.targetWorkflowId, commit);
      const prepared: PreparedPackCandidate = Object.freeze({
        ...metadata,
        workflowId: input.targetWorkflowId,
        branch: `wf/${input.targetWorkflowId}`,
        commit,
        sourceVersion: "initial",
        sourceCommit: null,
        expectedSavedHead: null,
        bundleBase64: await createPreparedGitBundle(repoDir, [commit]),
      });
      const result = await callback(prepared, async () => {
        pushStarted = true;
        return pushPreparedPackCandidate(repoDir, prepared);
      });
      return result;
    } finally {
      try {
        await removeTempRepo(repoDir);
      } catch (error) {
        if (pushStarted) {
          if (error instanceof PackCandidateOutcomeUnknownError) throw error;
          throw new PackCandidateOutcomeUnknownError(input.targetWorkflowId, "");
        }
        throw error;
      }
    }
  });
}

export class WorkflowCloneService {
  private readonly prepare: WithPreparedClonedPack;

  constructor(private readonly deps: WorkflowCloneServiceDeps) {
    this.prepare = deps.prepareClonedPack ?? withPreparedClonedPackInternal;
  }

  async candidates(): Promise<WorkflowCloneCandidate[]> {
    return (await this.deps.workflowSpecRepo.listSummaries()).map(toCandidate);
  }

  async preview(sourceWorkflowId: string): Promise<WorkflowClonePreview> {
    const { row, snapshot, scripts } = await this.readSource(sourceWorkflowId);
    return {
      sourceWorkflowId: row.workflow_id,
      sourcePackId: snapshot.packId,
      sourceTitle: row.title?.trim() || row.workflow_id,
      baseCommit: snapshot.baseCommit,
      scriptCount: scripts.length,
    };
  }

  private async readSource(sourceWorkflowId: string): Promise<
    {
      row: NonNullable<Awaited<ReturnType<WorkflowSpecRepository["findByWorkflowId"]>>>;
      snapshot: PackScriptSnapshot;
      scripts: PackScriptSnapshot["files"];
    }
  > {
    const source = requiredText(sourceWorkflowId, "sourceWorkflowId", 128);
    const row = await this.deps.workflowSpecRepo.findByWorkflowId(source);
    if (!row) throw new WorkflowCloneError(404, "SOURCE_WORKFLOW_NOT_FOUND", `Workflow "${source}" not found`);
    const sourcePackId = row.pack_id?.trim() || source;
    if (!validPackGitId(sourcePackId)) throw new WorkflowCloneError(409, "SOURCE_PACK_INVALID", "Source Pack id is invalid");
    const snapshot = await this.readSnapshot(source, sourcePackId);
    return { row, snapshot, scripts: validateScripts(snapshotScripts(snapshot)) };
  }

  private async readSnapshot(workflowId: string, packId: string): Promise<PackScriptSnapshot> {
    try {
      return await (this.deps.readSnapshot ?? readPackScriptsFromRemote)({
        db: this.deps.db,
        gitConfig: this.deps.gitConfig,
        gitAuthor: this.deps.gitAuthor,
        workflowId,
        packId,
        version: "draft",
        includeVersionFiles: true,
      });
    } catch (error) {
      if (error instanceof WorkflowCloneError) throw error;
      throw new WorkflowCloneError(503, "SOURCE_SNAPSHOT_UNAVAILABLE", "Source workflow snapshot unavailable");
    }
  }

  async clone(input: Omit<WorkflowCloneInput, "actorId">, actorId: string): Promise<WorkflowCloneResult> {
    const sourceWorkflowId = requiredText(input.sourceWorkflowId, "sourceWorkflowId", 128);
    const targetWorkflowId = requiredText(input.targetWorkflowId, "targetWorkflowId", 128);
    const targetTitle = requiredText(input.targetTitle, "targetTitle", MAX_TITLE_BYTES);
    const actor = requiredText(actorId, "actorId", 128);
    const command = input.facade?.command?.trim() ?? "";
    if (command && (!COMMAND_PATTERN.test(command) || Buffer.byteLength(command) > MAX_FACADE_BYTES)) {
      throw new WorkflowCloneError(400, "INVALID_FACADE_COMMAND", "Facade command must be kebab-case or snake_case");
    }
    const remark = input.facade?.remark?.trim() ?? "";
    if (Buffer.byteLength(remark) > MAX_REMARK_BYTES) {
      throw new WorkflowCloneError(400, "INVALID_FACADE_REMARK", "Facade remark is too long");
    }
    if (!validPackGitId(targetWorkflowId) || RESERVED_WORKFLOW_ID_PATTERN.test(targetWorkflowId)) {
      throw new WorkflowCloneError(400, "INVALID_TARGET_WORKFLOW_ID", "Target workflow id is invalid");
    }

    const { row, snapshot, scripts } = await this.readSource(sourceWorkflowId);
    const preview: WorkflowClonePreview = {
      sourceWorkflowId: row.workflow_id,
      sourcePackId: snapshot.packId,
      sourceTitle: row.title?.trim() || row.workflow_id,
      baseCommit: snapshot.baseCommit,
      scriptCount: scripts.length,
    };
    const targetPackId = targetWorkflowId;
    if (await this.deps.workflowSpecRepo.existsByWorkflowId(targetWorkflowId)) {
      throw new WorkflowCloneError(409, "TARGET_WORKFLOW_ALREADY_EXISTS", `Workflow "${targetWorkflowId}" already exists`);
    }
    if (command && await this.deps.facadeRepo.findByCommand(command)) {
      throw new WorkflowCloneError(409, "FACADE_COMMAND_CONFLICT", `Facade command "/${command}" is already bound`);
    }

    const sourceYaml = decodeBase64File(snapshot, sourceWorkflowFile(snapshot));
    const targetYaml = buildTargetYaml(sourceYaml, targetWorkflowId, targetTitle);
    const targetSpec = normalizeWorkflowSpec(parseDocument(targetYaml).toJS({ maxAliasCount: 0 }));
    validateWorkflowSemantics(targetSpec);
    const specJson = JSON.stringify({ ...targetSpec, title: targetTitle });
    const { gitConfig: resolvedConfig } = await resolveAutoHealGitSettings(this.deps.db, this.deps.gitConfig, this.deps.gitAuthor);

    let result: WorkflowCloneResult;
    try {
      result = await this.prepare(
        {
          targetWorkflowId,
          targetPackId,
          targetWorkflowYaml: targetYaml,
          config: resolvedConfig,
          author: this.deps.gitAuthor,
          scripts,
        },
        async (prepared, push) => {
          // DB first reserves the target identity before the leased Git push.
          // A failed push is surfaced as an uncertain clone outcome.
          await this.persistTarget({
            db: this.deps.db,
            targetWorkflowId,
            targetPackId,
            targetTitle,
            actorId: actor,
            specJson,
            facade: command ? { command, remark: remark || null } : undefined,
          });
          const git = await push();
          return {
            ...preview,
            targetWorkflowId,
            targetPackId,
            branch: git.branch,
            commit: git.commit,
          };
        },
      );
    } catch (error) {
      if (error instanceof PackCandidateOutcomeUnknownError) {
        throw new WorkflowCloneError(503, "CLONE_OUTCOME_UNKNOWN", "Target workflow Git push outcome is unknown; verify before retrying");
      }
      throw error;
    }
    return result;
  }

  private async persistTarget(input: {
    db: IDatabase;
    targetWorkflowId: string;
    targetPackId: string;
    targetTitle: string;
    actorId: string;
    specJson: string;
    facade?: { command: string; remark: string | null };
  }): Promise<void> {
    await input.db.transaction(async tx => {
      const existing = await tx.query(
        "SELECT workflow_id FROM workflow_specs WHERE workflow_id = ? OR pack_id = ? LIMIT 1",
        [input.targetWorkflowId, input.targetPackId],
      );
      if (existing.length) throw new WorkflowCloneError(409, "TARGET_WORKFLOW_ALREADY_EXISTS", "Target workflow or Pack already exists");
      if (input.facade) {
        const existingFacade = await tx.query("SELECT command FROM facade_bindings WHERE command = ? LIMIT 1", [input.facade.command]);
        if (existingFacade.length) throw new WorkflowCloneError(409, "FACADE_COMMAND_CONFLICT", "Facade command is already bound");
      }
      const now = tx.dialect.now();
      const inserted = await tx.exec(
        "INSERT INTO workflow_specs (workflow_id, pack_id, spec_json, title, owner_id, gmt_create, gmt_modified) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [input.targetWorkflowId, input.targetPackId, input.specJson, input.targetTitle, input.actorId, now, now],
      );
      if (inserted.affectedRows !== 1) throw new Error("Workflow clone database write failed");
      const permissions = new BotWorkflowPermissionRepository(tx);
      await permissions.upsert({
        bot_id: null,
        bot_owner_id: input.actorId,
        workflow_id: input.targetWorkflowId,
        can_view: 1,
        can_execute: 1,
        can_edit: 1,
      });
      if (input.facade) {
        const facades = new FacadeBindingRepository(tx);
        const facade = await facades.upsert({
          command: input.facade.command,
          workflowId: input.targetWorkflowId,
          packId: input.targetPackId,
          remark: input.facade.remark,
        });
        if (!facade) throw new Error("Workflow clone facade write failed");
      }
    });
  }
}
