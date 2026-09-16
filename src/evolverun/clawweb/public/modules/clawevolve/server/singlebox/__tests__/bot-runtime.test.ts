import { afterEach, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { createLocalBotResolver } from "../bot-runtime.js";
import { loadSingleboxConfig } from "../config.js";
let root = "";
let db: SqliteDatabase | undefined;
const requiredSkillEntries = [
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
function createSkillEntries(base: string): void {
  for (const entry of requiredSkillEntries) {
    const file = join(base, entry); mkdirSync(dirname(file), { recursive: true }); writeFileSync(file, "");
  }
}
const originalSkillsRoot = process.env.CLAWEVOLVE_SKILLS_ROOT;
afterEach(async () => {
  await db?.close(); db = undefined;
  if (root) rmSync(root, { recursive: true, force: true });
  if (originalSkillsRoot === undefined) delete process.env.CLAWEVOLVE_SKILLS_ROOT;
  else process.env.CLAWEVOLVE_SKILLS_ROOT = originalSkillsRoot;
});

it("uses the in-tree skills root exported by the launcher when YAML omits skillsRoot", () => {
  root = realpathSync(mkdtempSync(join(tmpdir(), "ce-config-")));
  const data = join(root, "scripts/.dependencies/data"); mkdirSync(data, { recursive: true });
  writeFileSync(join(data, "backend.db"), "");
  const skillsRoot = join(root, "public-skills"); mkdirSync(skillsRoot); createSkillEntries(skillsRoot);
  process.env.CLAWEVOLVE_SKILLS_ROOT = skillsRoot;
  const configFile = join(root, "config.yaml");
  writeFileSync(configFile, JSON.stringify({
    userId: "owner", model: "local/model",
    backendDb: "scripts/.dependencies/data/backend.db", dataDirectory: "data",
  }));
  const config = loadSingleboxConfig(configFile);
  expect(config.skillsRoot).toBe(realpathSync(skillsRoot));
  expect(config.models).toEqual(["local/model"]);
});
it("uses an existing OpenClaw home without an Avernet Backend database", async () => {
  root = realpathSync(mkdtempSync(join(tmpdir(), "ce-openclaw-home-")));
  const home = join(root, "openclaw"); const workspace = join(home, "workspace");
  mkdirSync(workspace, { recursive: true }); createSkillEntries(root);
  writeFileSync(join(home, "openclaw.json"), JSON.stringify({ agents: { defaults: { workspace } } }));
  const configFile = join(root, "config.json");
  writeFileSync(configFile, JSON.stringify({ botSource: "openclaw", userId: "owner", model: "local/model",
    openclawHome: home, localBotId: "local-openclaw", skillsRoot: root, dataDirectory: join(root, "data") }));
  const config = loadSingleboxConfig(configFile);
  expect(config.backendDb).toBe(join(root, "data", "local-bot-catalog.db"));
  const resolve = createLocalBotResolver(config, { query: async () => [] } as unknown as SqliteDatabase);
  expect((await resolve("owner", "local-openclaw", "dev")).workspace).toBe(workspace);
  await expect(resolve("owner", "other", "dev")).rejects.toThrow("not found");
});
it("loads service-only config and resolves owned Bots without a fixed binding, rejecting symlink escapes", async () => {
  root = realpathSync(mkdtempSync(join(tmpdir(), "ce-resolver-")));
  const data = join(root, "scripts/.dependencies/data"); mkdirSync(data, { recursive: true });
  const raw = new Database(join(data, "backend.db")); db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE ac_bots(id INTEGER, bot_id TEXT, env TEXT, owner_id TEXT, entity_id TEXT, entity_type TEXT,
    active_engine TEXT, bot_type TEXT, is_delete INTEGER);
    INSERT INTO ac_bots VALUES(1,'bot','dev','owner','owner','staff','openclaw','personal',0)`);
  const configFile = join(root, "config.yaml");
  createSkillEntries(root);
  writeFileSync(configFile, JSON.stringify({ userId: "owner", model: "local/model", backendDb: "scripts/.dependencies/data/backend.db", skillsRoot: ".", dataDirectory: "data" }));
  const config = loadSingleboxConfig(configFile);
  expect(config).not.toHaveProperty("botId");
  const home = join(config.botsRoot, "staff_owner/bot/openclaw"); const workspace = join(home, "workspace");
  mkdirSync(workspace, { recursive: true });
  writeFileSync(join(home, "openclaw.json"), JSON.stringify({ agents: { defaults: { workspace } } }));
  const resolve = createLocalBotResolver(config, db);
  expect((await resolve("owner", "bot", "dev")).workspace).toBe(workspace);
  await expect(resolve("other", "bot", "dev")).rejects.toThrow();
  await expect(resolve("owner", "bot", "prod")).rejects.toThrow();
  raw.exec("UPDATE ac_bots SET is_delete=1");
  await expect(resolve("owner", "bot", "dev")).rejects.toThrow();
  raw.exec("UPDATE ac_bots SET is_delete=0");
  rmSync(workspace, { recursive: true }); symlinkSync(root, workspace);
  await expect(resolve("owner", "bot", "dev")).rejects.toThrow("escapes");
});
