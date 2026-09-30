import { readFileSync, realpathSync, statSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { parse } from "yaml";

export type SingleboxConfig = {
  userId: string; model: string; models: string[];
  backendDb: string; skillsRoot: string; botsRoot: string;
  dataDirectory: string; port: number; maxArtifactBytes?: number;
  botSource?: "singlebox" | "openclaw";
  openclawHome?: string;
  localBotId?: string;
  localBotName?: string;
};

const REQUIRED_SKILL_ENTRIES = [
  "clawevolve-diagnose/scripts/run.sh",
  "clawevolve-plan/scripts/run.sh",
  "clawevolve-pack/scripts/pack.sh",
  "clawevolve-deploy/scripts/deploy.sh",
  "scripts/clawevolve_runtime_cleanup.py",
  "clawevolve-workflow/scripts/handlers/clawevolve_bench_run.py",
  "clawevolve-workflow/scripts/handlers/clawevolve_bench_plan_run.py",
  "clawevolve-workflow/scripts/handlers/clawevolve_pack_run.py",
  "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py",
];

function validateSkillsRoot(root: string): string {
  for (const entry of REQUIRED_SKILL_ENTRIES) {
    const target = realpathSync(join(root, entry));
    const rel = relative(root, target);
    if (rel.startsWith("..") || isAbsolute(rel) || !statSync(target).isFile()) {
      throw new Error(`Invalid public Skill entry: ${entry}`);
    }
  }
  return root;
}

/** Local service configuration. Bot identity and paths are resolved from Backend per request. */
export function loadSingleboxConfig(file: string): SingleboxConfig {
  const base = dirname(resolve(file));
  const value: unknown = parse(readFileSync(file, "utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Singlebox config must be a mapping");
  const input = value as Record<string, unknown>;
  const required = (name: string): string => {
    const value = input[name];
    if (typeof value !== "string" || !value.trim()) throw new Error(`Singlebox config requires ${name}`);
    return value.trim();
  };
  const path = (name: string, directory: boolean, fallback?: string): string => {
    const configured = input[name];
    const raw = typeof configured === "string" && configured.trim()
      ? configured.trim()
      : fallback;
    if (!raw) throw new Error(`Singlebox config requires ${name}`);
    const result = realpathSync(resolve(base, raw));
    if (statSync(result).isDirectory() !== directory) throw new Error(`Invalid ${name}`);
    return result;
  };
  const port = Number(input.port ?? 5173);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid Singlebox port");
  const maxArtifactBytes = Number(input.maxArtifactBytes ?? 512 * 1024 * 1024);
  if (!Number.isSafeInteger(maxArtifactBytes) || maxArtifactBytes < 1) throw new Error("Invalid maxArtifactBytes");
  const rawBotSource = input.botSource === undefined ? "singlebox" : String(input.botSource).trim().toLowerCase();
  if (rawBotSource !== "singlebox" && rawBotSource !== "openclaw") throw new Error("botSource must be singlebox or openclaw");
  const botSource: "singlebox" | "openclaw" = rawBotSource;
  const dataDirectory = resolve(base, required("dataDirectory"));
  const openclawHome = botSource === "openclaw" ? path("openclawHome", true) : undefined;
  const backendDb = botSource === "singlebox"
    ? path("backendDb", false)
    : resolve(dataDirectory, "local-bot-catalog.db");
  const model = required("model");
  const rawModels = input.models === undefined ? [] : input.models;
  if (!Array.isArray(rawModels) || rawModels.some((value) => typeof value !== "string")) {
    throw new Error("Singlebox config models must be a string list");
  }
  const models = [...new Set([model, ...rawModels.map((value) => value.trim()).filter(Boolean)])];
  if (models.some((value) => value.length > 128 || /[\0\r\n\s]/.test(value))) {
    throw new Error("Invalid local model name");
  }
  const config = {
    maxArtifactBytes, userId: required("userId"), model, models,
    botSource,
    backendDb, skillsRoot: validateSkillsRoot(path("skillsRoot", true, process.env.CLAWEVOLVE_SKILLS_ROOT)),
    // Same default layout as scripts/singlebox.sh and BAAS local_proc.
    botsRoot: botSource === "openclaw" ? openclawHome! : input.botsRoot === undefined
      ? resolve(dirname(backendDb), "../../..", "test-bots/aidesktop/aidesktop_singlebox/bolt_data")
      : path("botsRoot", true),
    dataDirectory, port,
    ...(openclawHome ? { openclawHome } : {}),
    ...(botSource === "openclaw" ? {
      localBotId: typeof input.localBotId === "string" && input.localBotId.trim() ? input.localBotId.trim() : "local-openclaw",
      localBotName: typeof input.localBotName === "string" && input.localBotName.trim() ? input.localBotName.trim() : "Local OpenClaw",
    } : {}),
  };
  if (config.localBotId && !/^[A-Za-z0-9_.-]+$/.test(config.localBotId)) throw new Error("Invalid localBotId");
  return config;
}
