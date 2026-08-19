import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

/**
 * Returns the platform-aware path to the `.credentials` file.
 * - macOS (darwin): $HOME/.credentials
 * - Linux: /home/admin/.credentials
 */
export function getCredentialsPath(): string {
  if (process.platform === "darwin") {
    const home = process.env.HOME || os.homedir();
    return home ? path.join(home, ".credentials") : "/home/admin/.credentials";
  }
  return "/home/admin/.credentials";
}

/**
 * Parses a KEY=VALUE file, skipping blank lines and # comments.
 * First `=` in each line separates key from value; both sides trimmed.
 */
export function parseKeyValueFile(content: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eqIdx = trimmed.indexOf("=");
    if (eqIdx <= 0) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    const value = trimmed.slice(eqIdx + 1).trim();
    if (key) result[key] = value;
  }
  return result;
}

let cachedBotId: string | null | undefined = undefined;
let cachedOwnerId: string | null | undefined = undefined;
let cachedInstanceId: string | null | undefined = undefined;

/**
 * Reads BOT_ID (real_bot_id) from the local .credentials file.
 * Returns the raw BOT_ID string as-is (e.g. "default"), or null if not found / file missing.
 * NOTE: For instance-level isolation ID (ownerId_botId), use loadInstanceId() instead.
 * Result is cached after first read.
 * Pass `forceReload: true` to re-read the file.
 */
export function loadBotId(forceReload = false): string | null {
  if (!forceReload && cachedBotId !== undefined) return cachedBotId;
  const credPath = getCredentialsPath();
  try {
    if (!fs.existsSync(credPath)) {
      cachedBotId = null;
      return null;
    }
    const content = fs.readFileSync(credPath, "utf-8");
    const parsed = parseKeyValueFile(content);
    cachedBotId = parsed.BOT_ID || null;
    return cachedBotId;
  } catch {
    cachedBotId = null;
    return null;
  }
}

/**
 * Reads OWNER_ID from the local .credentials file.
 * Returns the OWNER_ID string, or null if not found / file missing.
 * Result is cached after first read.
 * Pass `forceReload: true` to re-read the file.
 */
export function loadOwnerId(forceReload = false): string | null {
  if (!forceReload && cachedOwnerId !== undefined) return cachedOwnerId;
  const credPath = getCredentialsPath();
  try {
    if (!fs.existsSync(credPath)) {
      cachedOwnerId = null;
      return null;
    }
    const content = fs.readFileSync(credPath, "utf-8");
    const parsed = parseKeyValueFile(content);
cachedOwnerId = parsed.OWNER_ID || null;
    return cachedOwnerId;
  } catch {
    cachedOwnerId = null;
    return null;
  }
}

/**
 * 加载当前 OpenClaw 实例标识。
 * 规则：staff_no + "_" + real_bot_id（如 "103892_20260402_mnpvqm6v"）。
 * 用于蓄流系统中隔离不同 OpenClaw 实例的蓄流池。
 * 如果 credentials 文件不存在或缺少必要字段，返回 null。
 */
export function loadInstanceId(forceReload = false): string | null {
  if (!forceReload && cachedInstanceId !== undefined) return cachedInstanceId;
  const credPath = getCredentialsPath();
  try {
    if (!fs.existsSync(credPath)) {
      cachedInstanceId = null;
      return null;
    }
    const content = fs.readFileSync(credPath, "utf-8");
    const parsed = parseKeyValueFile(content);
    const ownerId = parsed.OWNER_ID;
    const botId = parsed.BOT_ID;
    if (!ownerId || !botId) {
      cachedInstanceId = null;
      return null;
    }
    cachedInstanceId = `${ownerId}_${botId}`;
    return cachedInstanceId;
  } catch {
    cachedInstanceId = null;
    return null;
  }
}