import { readFileSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, join, relative } from "node:path";
import type { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import type { SingleboxConfig } from "./config.js";

export type LocalBotPaths = { botId: string; botEnv: string; openclawHome: string; workspace: string };
export type ResolveLocalBot = (userId: string, botId: string, env: string) => Promise<LocalBotPaths>;

/** Local-only adapter for the existing BAAS local_proc workspace layout; no Bot registry or writes. */
export function createLocalBotResolver(config: SingleboxConfig, db: Pick<SqliteDatabase, "query">): ResolveLocalBot {
  return async (userId, botId, env) => {
    if (userId !== config.userId || !botId || !env) throw new Error("Invalid local Bot owner/environment");
    if (config.botSource === "openclaw") {
      if (botId !== config.localBotId || env !== "dev" || !config.openclawHome) {
        throw new Error("Local OpenClaw Bot not found for the current user/environment");
      }
      const openclawHome = realpathSync(config.openclawHome);
      const workspace = realpathSync(join(openclawHome, "workspace"));
      if (!statSync(workspace).isDirectory()) throw new Error("Local OpenClaw workspace is missing");
      const profile = JSON.parse(readFileSync(join(openclawHome, "openclaw.json"), "utf8"));
      const declaredWorkspace = profile.agents?.defaults?.workspace;
      if (typeof declaredWorkspace === "string" && realpathSync(declaredWorkspace) !== workspace) {
        throw new Error("OpenClaw profile workspace does not match its local home");
      }
      return { botId, botEnv: env, openclawHome, workspace };
    }
    // COSEC: bind all selection values and recheck ownership at creation AND dispatch.
    const rows = await db.query<{ entity_id: string; entity_type: string; active_engine: string; bot_type: string }>(
      `SELECT entity_id, entity_type, active_engine, bot_type FROM ac_bots
       WHERE bot_id = ? AND env = ? AND (owner_id = ? OR entity_id = ?) AND is_delete = 0
       ORDER BY id DESC LIMIT 1`, [botId, env, userId, userId]);
    const row = rows[0];
    if (!row || row.active_engine?.toLowerCase() !== "openclaw" || row.bot_type?.toLowerCase() !== "personal") {
      throw new Error("Personal OpenClaw Bot not found for the current user/environment");
    }
    // COSEC: database metadata is not permission to traverse outside the configured local Bot root.
    for (const segment of [row.entity_type, row.entity_id, botId]) {
      if (!segment || !/^[A-Za-z0-9_.-]+$/.test(segment) || segment.includes("..")) throw new Error("Invalid local Bot path metadata");
    }
    const root = realpathSync(config.botsRoot);
    const inside = (path: string) => {
      const actual = realpathSync(path);
      const rel = relative(root, actual);
      if (actual !== path || !rel || rel.startsWith("..") || isAbsolute(rel)) throw new Error("Bot path escapes botsRoot");
      return actual;
    };
    // Mirrors BAAS local_proc/_workspace.py: entity_type_entity_id / bot_id / engine / workspace.
    const openclawHome = inside(join(root, `${row.entity_type}_${row.entity_id}`, botId, "openclaw"));
    const workspace = inside(join(openclawHome, "workspace"));
    if (!statSync(workspace).isDirectory()) throw new Error("Bot workspace is missing");
    const profilePath = inside(join(openclawHome, "openclaw.json"));
    const profile = JSON.parse(readFileSync(profilePath, "utf8"));
    const declaredWorkspace = profile.agents?.defaults?.workspace;
    if (typeof declaredWorkspace !== "string" || realpathSync(declaredWorkspace) !== workspace) {
      throw new Error("Bot profile workspace does not match Backend local runtime layout");
    }
    return { botId, botEnv: env, openclawHome, workspace };
  };
}
