import {
  constants,
  lstat,
  readdir,
  realpath,
  stat,
} from "node:fs/promises";
import { homedir } from "node:os";
import { basename, join } from "node:path";

export type RequiredSkillSource =
  | "workspace-symlink"
  | "workspace-direct"
  | "home-symlink"
  | "home-direct"
  | "openclaw-ext";

export interface SkillResolutionDirs {
  workspaceDir?: string;
  homeDir?: string;
  workspaceSkillsDir?: string;
  homeSkillsDir?: string;
  openclawExtDir?: string;
}

export type ResolveRequiredSkillOptions = SkillResolutionDirs;

export interface ResolvedRequiredSkill {
  name: string;
  source: RequiredSkillSource;
  entryPath: string;
  skillDir: string;
  skillFile: string;
}

export class RequiredSkillNotFoundError extends Error {
  constructor(
    public readonly skillName: string,
    public readonly workspaceDir: string,
    public readonly homeDir: string,
    public readonly openclawExtDir?: string,
  ) {
    const checkedDirs = [workspaceDir, homeDir];
    if (openclawExtDir) checkedDirs.push(openclawExtDir);
    super(`未找到 skill：${skillName}。已检查 ${checkedDirs.join("、")}。请确认已安装该 skill。`);
    this.name = "RequiredSkillNotFoundError";
  }
}

// ── Process-level cache for skill resolution ──────────────────────────
//
// Concurrent embedded-agent nodes that reference the same skillName
// resolve to the same result.  Caching avoids redundant async fs scans.

const SKILL_CACHE_TTL_MS = 60_000;

type SkillCacheEntry = {
  result: ResolvedRequiredSkill;
  timestamp: number;
};

const skillResolveCache = new Map<string, SkillCacheEntry>();

/**
 * Clear the skill resolution cache.  Intended for test isolation.
 */
export function clearSkillResolveCache(): void {
  skillResolveCache.clear();
}

interface SourceConfig {
  dir: string;
  source: RequiredSkillSource;
  symlink: boolean;
}

type SkillCandidate = ResolvedRequiredSkill & {
  entryName: string;
  matchRank: number;
};

function defaultWorkspaceSkillsDir(): string {
  return process.env.WORKFLOW_ENGINE_SKILLS_DIR
    ?? join(homedir(), ".openclaw", "workspace", "skills");
}

function defaultHomeSkillsDir(): string {
  return process.env.WORKFLOW_ENGINE_HOME_SKILLS_DIR
    ?? join(homedir(), ".openclaw", "skills");
}

function defaultOpenclawExtSkillsDir(): string {
  return process.env.WORKFLOW_ENGINE_OPENCLAWEXT_SKILLS_DIR
    ?? join(homedir(), "openclawExt", "clawmind", "skills");
}

export async function resolveRequiredSkill(
  skillName: string,
  options: ResolveRequiredSkillOptions = {},
): Promise<ResolvedRequiredSkill> {
  const workspaceDir = options.workspaceDir ?? options.workspaceSkillsDir ?? defaultWorkspaceSkillsDir();
  const homeDir = options.homeDir ?? options.homeSkillsDir ?? defaultHomeSkillsDir();
  const openclawExtDir = options.openclawExtDir ?? defaultOpenclawExtSkillsDir();

  // Check cache first — key uses JSON.stringify to prevent delimiter collision
  // (a `::` in a skillName or path could produce the same key as a different split)
  const cacheKey = JSON.stringify([skillName, workspaceDir, homeDir, openclawExtDir]);
  const now = Date.now();
  const cached = skillResolveCache.get(cacheKey);
  if (cached && (now - cached.timestamp) < SKILL_CACHE_TTL_MS) {
    return cached.result;
  }
  const sources: SourceConfig[] = [
    { dir: workspaceDir, source: "workspace-symlink", symlink: true },
    { dir: workspaceDir, source: "workspace-direct", symlink: false },
    { dir: openclawExtDir, source: "openclaw-ext", symlink: false },
    { dir: homeDir, source: "home-symlink", symlink: true },
    { dir: homeDir, source: "home-direct", symlink: false },
  ];

  for (const source of sources) {
    const resolved = await findSkillInSource(skillName, source);
    if (resolved) {
      skillResolveCache.set(cacheKey, { result: resolved, timestamp: now });
      return resolved;
    }
  }

  throw new RequiredSkillNotFoundError(skillName, workspaceDir, homeDir, openclawExtDir);
}

async function findSkillInSource(
  skillName: string,
  source: SourceConfig,
): Promise<ResolvedRequiredSkill | undefined> {
  const entries = await readEntries(source.dir);
  const candidates: SkillCandidate[] = [];
  for (const entry of entries) {
    const entryPath = join(source.dir, entry);
    const entryKind = await statEntry(entryPath);
    if (!entryKind || entryKind.isSymbolicLink !== source.symlink) {
      continue;
    }

    const resolved = await resolveSkillEntry(skillName, source.source, entryPath);
    if (resolved) {
      candidates.push(resolved);
    }
  }

  candidates.sort((a, b) => a.matchRank - b.matchRank || a.entryName.localeCompare(b.entryName));
  const selected = candidates[0];
  if (!selected) return undefined;
  const { entryName: _entryName, matchRank: _matchRank, ...skill } = selected;
  return skill;
}

async function readEntries(dir: string): Promise<string[]> {
  try {
    const entries = await readdir(dir);
    return entries.sort((a, b) => a.localeCompare(b));
  } catch (error) {
    if (isMissingPathError(error)) {
      return [];
    }
    throw error;
  }
}

async function statEntry(entryPath: string): Promise<{ isSymbolicLink: boolean } | undefined> {
  try {
    const statResult = await lstat(entryPath);
    if (statResult.isSymbolicLink()) {
      return { isSymbolicLink: true };
    }
    if (statResult.isDirectory()) {
      return { isSymbolicLink: false };
    }
    return undefined;
  } catch (error) {
    if (isMissingPathError(error)) {
      return undefined;
    }
    throw error;
  }
}

async function resolveSkillEntry(
  skillName: string,
  source: RequiredSkillSource,
  entryPath: string,
): Promise<SkillCandidate | undefined> {
  const skillDir = await safeRealpath(entryPath);
  if (!skillDir || !(await isDirectory(skillDir))) {
    return undefined;
  }

  const entryName = basename(entryPath);
  const matchRank = getMatchRank(skillName, entryName, skillDir);
  if (matchRank === undefined) {
    return undefined;
  }

  const skillFile = join(skillDir, "SKILL.md");
  if (!(await isFile(skillFile))) {
    return undefined;
  }

  return {
    name: skillName,
    source,
    entryPath,
    skillDir,
    skillFile,
    entryName,
    matchRank,
  };
}

function getMatchRank(skillName: string, entryName: string, skillDir: string): number | undefined {
  if (entryName === skillName) return 0;
  if (entryName.endsWith(`_${skillName}`)) return 1;
  if (basename(skillDir) === skillName) return 2;
  return undefined;
}

async function safeRealpath(entryPath: string): Promise<string | undefined> {
  try {
    return await realpath(entryPath);
  } catch (error) {
    if (isMissingPathError(error)) {
      return undefined;
    }
    throw error;
  }
}

async function isDirectory(dir: string): Promise<boolean> {
  try {
    return (await stat(dir)).isDirectory();
  } catch (error) {
    if (isMissingPathError(error)) {
      return false;
    }
    throw error;
  }
}

async function isFile(file: string): Promise<boolean> {
  try {
    return (await stat(file)).isFile();
  } catch (error) {
    if (isMissingPathError(error)) {
      return false;
    }
    throw error;
  }
}

function isMissingPathError(error: unknown): boolean {
  return (
    error instanceof Error &&
    "code" in error &&
    (error.code === "ENOENT" || error.code === "ENOTDIR")
  );
}