import type { WorkflowPackFacade, WorkflowPackFacadeHelp, ResolvedWorkflowPack } from "../packs/types.js";
import type { WorkflowCommandSurface } from "../types.js";
import type { IDatabase } from "../db/types.js";
import type { ApiClient } from "../db/api-client.js";
import { FacadeBindingRepository } from "../db/repositories/facade-binding-repository.js";
import { FacadeBindingApiRepository } from "../db/api-repositories/facade-binding-api-repository.js";

export type ResolvedWorkflowFacade = {
  command: string;
  aliases: string[];
  defaultWorkflow: string;
  packId: string;
  packVersion: string;
  help?: WorkflowPackFacadeHelp;
  source: "pack" | "db";
  remark?: string;
};

export type DbFacadeBinding = {
  command: string;
  workflowId: string;
  packId?: string;
  remark?: string;
};

export type FacadeRegistry = {
  commands(): string[];
  resolve(command: string | undefined): ResolvedWorkflowFacade | undefined;
  facadeForWorkflow(workflowId: string | undefined): ResolvedWorkflowFacade | undefined;
};

const WORKFLOW_ID_COMMANDS = new Set(["run", "reopen", "detail", "validate", "cutover-check", "help"]);

function normalizeCommand(value: string | undefined): string {
  return value?.trim().replace(/^\/+/, "").toLowerCase() ?? "";
}

function resolveFacade(params: {
  facade: WorkflowPackFacade;
  pack: ResolvedWorkflowPack;
}): ResolvedWorkflowFacade {
  return {
    command: params.facade.command,
    aliases: params.facade.aliases ?? [],
    defaultWorkflow: params.facade.defaultWorkflow,
    packId: params.pack.manifest.id,
    packVersion: params.pack.manifest.version,
    ...(params.facade.help ? { help: params.facade.help } : {}),
    source: "pack",
  };
}

export function buildFacadeRegistry(
  packs: ResolvedWorkflowPack[] = [],
  dbBindings: DbFacadeBinding[] = [],
): FacadeRegistry {
  const lookup = new Map<string, ResolvedWorkflowFacade>();
  const workflowLookup = new Map<string, ResolvedWorkflowFacade>();

  for (const pack of packs) {
    for (const facadeSpec of pack.manifest.facades ?? []) {
      const facade = resolveFacade({ facade: facadeSpec, pack });
      for (const name of [facade.command, ...facade.aliases]) {
        const normalized = normalizeCommand(name);
        const existing = lookup.get(normalized);
        if (existing) {
          throw new Error(
            `facade command conflict "${normalized}": ${existing.packId} (${existing.source}) and ${facade.packId} (pack)`,
          );
        }
        lookup.set(normalized, facade);
      }
      workflowLookup.set(facade.defaultWorkflow, facade);
    }
  }

  for (const binding of dbBindings) {
    const facade: ResolvedWorkflowFacade = {
      command: binding.command,
      aliases: [],
      defaultWorkflow: binding.workflowId,
      packId: binding.packId ?? "__db__",
      packVersion: "0",
      source: "db",
      ...(binding.remark ? { remark: binding.remark } : {}),
    };
    const normalized = normalizeCommand(binding.command);
    const existing = lookup.get(normalized);
    if (existing && existing.source === "db") {
      throw new Error(
        `facade command conflict "${normalized}": duplicate DB bindings`,
      );
    }
    // DB binding overrides Pack facade — DB is the source of truth at runtime
    lookup.set(normalized, facade);
    // Also remove the old pack facade's workflow lookup if it pointed to a different workflow
    if (existing && existing.defaultWorkflow !== facade.defaultWorkflow) {
      workflowLookup.delete(existing.defaultWorkflow);
    }
    workflowLookup.set(facade.defaultWorkflow, facade);
  }

  return {
    commands: () => Array.from(lookup.keys()),
    resolve: (command) => lookup.get(normalizeCommand(command)),
    facadeForWorkflow: (workflowId) => workflowId ? workflowLookup.get(workflowId) : undefined,
  };
}

export async function loadDbFacadeBindings(db: IDatabase): Promise<DbFacadeBinding[]> {
  const repo = new FacadeBindingRepository(db);
  const rows = await repo.listAll();
  return rows.map((row) => ({
    command: row.command,
    workflowId: row.workflow_id,
    ...(row.pack_id ? { packId: row.pack_id } : {}),
    ...(row.remark ? { remark: row.remark } : {}),
  }));
}

/**
 * Load facade bindings from clawweb's internal API (API mode).
 * When botId and/or botOwnerId are provided, the server filters by bot permissions
 * (only returns facade bindings for workflows the bot has view access to).
 * Falls back gracefully: returns empty array on any error.
 */
export async function loadApiFacadeBindings(
  apiClient: ApiClient,
  botId?: string,
  botOwnerId?: string,
): Promise<DbFacadeBinding[]> {
  const repo = new FacadeBindingApiRepository(apiClient);
  const rows = await repo.listAll(botId, botOwnerId);
  return rows.map((row) => ({
    command: row.command,
    workflowId: row.workflow_id,
    ...(row.pack_id ? { packId: row.pack_id } : {}),
    ...(row.remark ? { remark: row.remark } : {}),
  }));
}

export function formatWorkflowCommand(
  registry: FacadeRegistry,
  workflowId: string,
  command: string,
  args: string[] = [],
  options: { surface?: WorkflowCommandSurface } = {},
): string {
  void registry;
  const surface = options.surface ?? { type: "workflow" };
  if (surface.type === "facade") {
    return [`/${surface.command}`, command, ...args].filter(Boolean).join(" ");
  }

  const workflowArgs = WORKFLOW_ID_COMMANDS.has(command)
    ? [workflowId, ...args]
    : args;
  return ["/workflow", command, ...workflowArgs].filter(Boolean).join(" ");
}