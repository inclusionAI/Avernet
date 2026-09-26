import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

/**
 * Resolve the sessionId for a given sessionKey.
 *
 * Session keys follow various formats:
 *   agent:{agent}:dashboard:{uuid}                  — 4 parts, UUID is last
 *   agent:{agent}:session:{uuid}:user:{surface}     — 7 parts, UUID is part[3]
 *
 * Instead of assuming the UUID position, this function scans all segments
 * for a UUID pattern, falling back to sessions.json if none is found.
 *
 * @param sessionKey - The business-dimension session key (e.g. "agent:main:dashboard:xxx-yyy")
 * @param overridePath - Override store file path (for testing)
 */
export function resolveSessionId(sessionKey: string, overridePath?: string): string | null {
  // Scan all colon-separated segments for a UUID — robust across key formats.
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  for (const part of sessionKey.split(":")) {
    if (UUID_RE.test(part)) return part;
  }

  // Fallback: read from sessions.json for legacy session key formats
  const home = process.env.HOME || os.homedir();
  const storePath = overridePath ?? path.join(home, ".openclaw", "agents", "main", "sessions", "sessions.json");
  try {
    if (!fs.existsSync(storePath)) return null;
    const raw = fs.readFileSync(storePath, "utf-8");
    const store = JSON.parse(raw) as Record<string, { sessionId?: string }>;
    const normalizedKey = sessionKey.trim().toLowerCase();
    // Direct match first
    if (store[normalizedKey]?.sessionId) return store[normalizedKey].sessionId;
    // Case-insensitive fallback
    for (const [key, entry] of Object.entries(store)) {
      if (key.trim().toLowerCase() === normalizedKey && entry?.sessionId) {
        return entry.sessionId;
      }
    }
    return null;
  } catch {
    return null;
  }
}