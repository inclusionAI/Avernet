import { readFileSync } from "node:fs";
import { isAbsolute, join, posix, win32 } from "node:path";
import { parse as parseYaml } from "yaml";
import type {
  WorkflowPackFacade,
  WorkflowPackFacadeHelp,
  WorkflowPackManifest,
  WorkflowPackManifestAction,
  WorkflowPackManifestWorkflow,
} from "./types.js";

export const PACK_MANIFEST_FILENAME = "workflow.pack.yaml";
const FACADE_COMMAND_RE = /^[a-z0-9][a-z0-9_-]{1,63}$/u;
const RESERVED_FACADE_COMMANDS = new Set(["workflow"]);

export class WorkflowPackManifestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowPackManifestError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function requiredString(raw: Record<string, unknown>, key: string, path: string): string {
  const value = raw[key];
  if (typeof value !== "string" && typeof value !== "number") {
    throw new WorkflowPackManifestError(`${path}: must be a non-empty string`);
  }

  const normalized = String(value).trim();
  if (!normalized) {
    throw new WorkflowPackManifestError(`${path}: must be a non-empty string`);
  }
  return normalized;
}

function optionalString(raw: Record<string, unknown>, key: string, path: string): string | undefined {
  if (raw[key] === undefined) return undefined;
  return requiredString(raw, key, path);
}

export function normalizeSafeRelativePath(value: unknown, path: string): string {
  if (typeof value !== "string") {
    throw new WorkflowPackManifestError(`${path}: must be a safe relative path`);
  }

  const raw = value.trim();
  if (
    !raw ||
    raw.includes("\\") ||
    raw.includes("\0") ||
    isAbsolute(raw) ||
    win32.isAbsolute(raw) ||
    /^[a-zA-Z]:/u.test(raw)
  ) {
    throw new WorkflowPackManifestError(`${path}: must be a safe relative path`);
  }

  const segments = raw.split("/");
  if (segments.some((segment) => !segment || segment === "." || segment === "..")) {
    throw new WorkflowPackManifestError(`${path}: must be a safe relative path`);
  }

  const normalized = posix.normalize(raw);
  if (normalized === "." || normalized.startsWith("../") || normalized.includes("/../")) {
    throw new WorkflowPackManifestError(`${path}: must be a safe relative path`);
  }

  return normalized;
}

function normalizeWorkflows(value: unknown): WorkflowPackManifestWorkflow[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new WorkflowPackManifestError("workflows: must contain at least one workflow");
  }

  return value
    .map((entry, index) => {
      const path = `workflows[${index}]`;
      if (!isRecord(entry)) {
        throw new WorkflowPackManifestError(`${path}: must be an object`);
      }
      return {
        id: requiredString(entry, "id", `${path}.id`),
        file: normalizeSafeRelativePath(entry.file, `${path}.file`),
      };
    })
    .filter((wf) => {
      // Skip macOS AppleDouble resource fork ghost entries (._-prefixed id or file).
      // These are injected when ensurePackManifest scans a workflows/ directory
      // that contains ._xxx.yaml files from macOS Finder. They are not real workflows
      // and should be silently dropped rather than causing load_error.
      const basename = wf.file.split("/").pop() ?? wf.file;
      if (wf.id.startsWith("._") || basename.startsWith("._")) {
        console.warn(`[packs] Skipping macOS resource fork entry in manifest: id=${wf.id}, file=${wf.file}`);
        return false;
      }
      return true;
    });
}

function normalizeActionCommands(value: unknown, path: string): Record<string, string> | undefined {
  if (value === undefined) return undefined;
  if (!isRecord(value)) {
    throw new WorkflowPackManifestError(`${path}: must be an object`);
  }

  const commands: Record<string, string> = {};
  for (const [actionName, scriptPath] of Object.entries(value)) {
    const normalizedActionName = actionName.trim();
    if (!normalizedActionName) {
      throw new WorkflowPackManifestError(`${path}: action name must be a non-empty string`);
    }
    commands[normalizedActionName] = normalizeSafeRelativePath(scriptPath, `${path}.${normalizedActionName}`);
  }
  return commands;
}

function normalizeActions(value: unknown): WorkflowPackManifestAction[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) {
    throw new WorkflowPackManifestError("actions: must be an array");
  }

  return value.map((entry, index) => {
    const path = `actions[${index}]`;
    if (!isRecord(entry)) {
      throw new WorkflowPackManifestError(`${path}: must be an object`);
    }
    const action: WorkflowPackManifestAction = {
      id: requiredString(entry, "id", `${path}.id`),
      type: requiredString(entry, "type", `${path}.type`),
      root: normalizeSafeRelativePath(entry.root, `${path}.root`),
    };
    const commands = normalizeActionCommands(entry.commands, `${path}.commands`);
    if (commands !== undefined) {
      action.commands = commands;
    }
    return action;
  });
}

function normalizeFacadeName(value: unknown, path: string): string {
  const name = requiredString({ value }, "value", path);
  if (!FACADE_COMMAND_RE.test(name)) {
    throw new WorkflowPackManifestError(`${path}: must match ${FACADE_COMMAND_RE}`);
  }
  if (RESERVED_FACADE_COMMANDS.has(name)) {
    throw new WorkflowPackManifestError(`${path}: reserved facade command "${name}"`);
  }
  return name;
}

function normalizeFacadeHelp(value: unknown, path: string): WorkflowPackFacadeHelp | undefined {
  if (value === undefined) return undefined;
  if (!isRecord(value)) {
    throw new WorkflowPackManifestError(`${path}: must be an object`);
  }

  const help: WorkflowPackFacadeHelp = {};
  const title = optionalString(value, "title", `${path}.title`);
  const summary = optionalString(value, "summary", `${path}.summary`);
  if (title !== undefined) help.title = title;
  if (summary !== undefined) help.summary = summary;
  if (value.examples !== undefined) {
    if (!Array.isArray(value.examples)) {
      throw new WorkflowPackManifestError(`${path}.examples: must be an array`);
    }
    help.examples = value.examples.map((entry, index) => {
      if (typeof entry !== "string" && typeof entry !== "number") {
        throw new WorkflowPackManifestError(`${path}.examples[${index}]: must be a non-empty string`);
      }
      const normalized = String(entry).trim();
      if (!normalized) {
        throw new WorkflowPackManifestError(`${path}.examples[${index}]: must be a non-empty string`);
      }
      return normalized;
    });
  }
  return help;
}

function normalizeFacades(value: unknown, workflows: WorkflowPackManifestWorkflow[]): WorkflowPackFacade[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) {
    throw new WorkflowPackManifestError("facades: must be an array");
  }

  const workflowIds = new Set(workflows.map((workflow) => workflow.id));
  return value.map((entry, index) => {
    const path = `facades[${index}]`;
    if (!isRecord(entry)) {
      throw new WorkflowPackManifestError(`${path}: must be an object`);
    }
    const command = normalizeFacadeName(entry.command, `${path}.command`);
    const defaultWorkflow = requiredString(entry, "defaultWorkflow", `${path}.defaultWorkflow`);
    if (!workflowIds.has(defaultWorkflow)) {
      throw new WorkflowPackManifestError(`${path}.defaultWorkflow: must reference a workflow in this pack`);
    }

    const facade: WorkflowPackFacade = {
      command,
      defaultWorkflow,
    };
    if (entry.aliases !== undefined) {
      if (!Array.isArray(entry.aliases)) {
        throw new WorkflowPackManifestError(`${path}.aliases: must be an array`);
      }
      facade.aliases = entry.aliases.map((alias, aliasIndex) => normalizeFacadeName(alias, `${path}.aliases[${aliasIndex}]`));
    }
    const help = normalizeFacadeHelp(entry.help, `${path}.help`);
    if (help !== undefined) facade.help = help;
    return facade;
  });
}

function normalizeSkills(value: unknown): WorkflowPackManifest["skills"] {
  if (value === undefined) return undefined;
  if (!isRecord(value)) {
    throw new WorkflowPackManifestError("skills: must be an object");
  }

  const required = value.required;
  if (required === undefined) return {};
  if (!Array.isArray(required)) {
    throw new WorkflowPackManifestError("skills.required: must be an array");
  }

  return {
    required: required.map((entry, index) => {
      if (typeof entry !== "string" || !entry.trim()) {
        throw new WorkflowPackManifestError(`skills.required[${index}]: must be a non-empty string`);
      }
      return entry.trim();
    }),
  };
}

function normalizeCompat(value: unknown): WorkflowPackManifest["compat"] {
  if (value === undefined) return undefined;
  if (!isRecord(value)) {
    throw new WorkflowPackManifestError("compat: must be an object");
  }

  const compat: WorkflowPackManifest["compat"] = {};
  if (value.minRuntimeVersion !== undefined) {
    compat.minRuntimeVersion = requiredString(value, "minRuntimeVersion", "compat.minRuntimeVersion");
  }
  if (value.schemaVersion !== undefined) {
    if (typeof value.schemaVersion !== "number" || !Number.isInteger(value.schemaVersion) || value.schemaVersion < 1) {
      throw new WorkflowPackManifestError("compat.schemaVersion: must be a positive integer");
    }
    compat.schemaVersion = value.schemaVersion;
  }
  return compat;
}

export function normalizePackManifest(raw: unknown): WorkflowPackManifest {
  if (!isRecord(raw)) {
    throw new WorkflowPackManifestError("manifest: must be an object");
  }
  const workflows = normalizeWorkflows(raw.workflows);

  return {
    id: requiredString(raw, "id", "id"),
    version: requiredString(raw, "version", "version"),
    title: optionalString(raw, "title", "title"),
    description: optionalString(raw, "description", "description"),
    workflows,
    actions: normalizeActions(raw.actions),
    facades: normalizeFacades(raw.facades, workflows),
    skills: normalizeSkills(raw.skills),
    compat: normalizeCompat(raw.compat),
  };
}

export function readPackManifest(packRoot: string): WorkflowPackManifest {
  const content = readFileSync(join(packRoot, PACK_MANIFEST_FILENAME), "utf-8");
  return normalizePackManifest(parseYaml(content) as unknown);
}
