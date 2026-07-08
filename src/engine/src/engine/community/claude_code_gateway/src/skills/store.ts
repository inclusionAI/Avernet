// Skills store — scans `~/.claude/skills/` (env `SKILLS_DIR` overrides) and
// treats each top-level entry as a skill.
//
// Layout:
//
//   ~/.claude/skills/
//     my-skill/                 <- directory or symlink
//       SKILL.md                <- YAML frontmatter first, then body
//     another-skill -> /abs/source/path  (symlink; target is `source`)
//     .state.json               <- { "disabled": ["my-skill", ...] }
//
// Read path (list/get) is pure filesystem + YAML frontmatter parsing. We
// deliberately do NOT run `yaml` through a dependency — the frontmatter we
// care about (name/description/version/capabilities/dependencies) is a flat
// key-value block and we can parse it with a tiny hand-rolled scanner that
// handles string scalars and simple `[a, b]` lists. Anything exotic in the
// frontmatter is ignored, which is the same contract SKILL.md authors
// already rely on.
//
// Write path (install/uninstall/update): in this phase we only handle the
// SYMLINK skill type. PACKAGE / BUILTIN / CUSTOM return NOT_IMPLEMENTED.

import fs from 'node:fs';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createLogger } from '../debug.js';
import type { Skill, SkillStatus, SkillType } from './types.js';

const log = createLogger('skills');

export function defaultSkillsDir(): string {
  if (process.env.SKILLS_DIR) return process.env.SKILLS_DIR;
  return path.join(os.homedir(), '.claude', 'skills');
}

export type SkillsStoreOptions = {
  /** For tests — bypass HOME and put SKILLS_DIR somewhere temp. */
  rootDir?: string;
};

const STATE_FILENAME = '.state.json';
const SKILL_MD_FILENAME = 'SKILL.md';

// ── Frontmatter parsing ────────────────────────────────────────────────────

type Frontmatter = Record<string, string | string[]>;

/**
 * Extract the first `---` ... `---` block and parse KEY: VALUE lines.
 * - String values may be quoted; quotes are stripped.
 * - Bracketed lists `[a, b, c]` are parsed as string[].
 * - Anything multiline / nested is silently ignored.
 */
export function parseFrontmatter(raw: string): Frontmatter {
  const lines = raw.split(/\r?\n/);
  if (lines[0]?.trim() !== '---') return {};
  const out: Frontmatter = {};
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === '---') break;
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$/);
    if (!m) continue;
    const key = m[1];
    const rawVal = m[2].trim();
    // Bracketed list
    const listMatch = rawVal.match(/^\[(.*)\]$/);
    if (listMatch) {
      const inner = listMatch[1];
      if (!inner.trim()) { out[key] = []; continue; }
      const parts = inner.split(',').map(p => p.trim().replace(/^["']|["']$/g, ''));
      out[key] = parts.filter(Boolean);
      continue;
    }
    // Quoted string
    const quoted = rawVal.match(/^["'](.*)["']$/);
    out[key] = quoted ? quoted[1] : rawVal;
  }
  return out;
}

// ── Store ─────────────────────────────────────────────────────────────────

export class SkillsStore {
  private readonly root: string;

  constructor(opts: SkillsStoreOptions = {}) {
    this.root = opts.rootDir ?? defaultSkillsDir();
  }

  private ensureRoot(): void {
    fs.mkdirSync(this.root, { recursive: true });
  }

  private statePath(): string {
    return path.join(this.root, STATE_FILENAME);
  }

  private loadState(): { disabled: Set<string> } {
    try {
      if (!fs.existsSync(this.statePath())) return { disabled: new Set() };
      const raw = fs.readFileSync(this.statePath(), 'utf8');
      const parsed = JSON.parse(raw);
      const disabled = Array.isArray(parsed?.disabled)
        ? new Set<string>((parsed.disabled as unknown[]).map(v => String(v)))
        : new Set<string>();
      return { disabled };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.warn('state.load:failed', { path: this.statePath(), error: msg });
      return { disabled: new Set() };
    }
  }

  private async saveState(state: { disabled: Set<string> }): Promise<void> {
    this.ensureRoot();
    const payload = { disabled: [ ...state.disabled ].sort() };
    const tmp = `${this.statePath()}.${process.pid}.${Date.now()}.tmp`;
    await fsp.writeFile(tmp, JSON.stringify(payload, null, 2) + '\n', 'utf8');
    await fsp.rename(tmp, this.statePath());
  }

  /** Entries in the skills root that look like a skill (dir or symlink). */
  private listEntries(): Array<{ name: string; entryPath: string; isSymlink: boolean; symlinkTarget?: string }> {
    if (!fs.existsSync(this.root)) return [];
    const entries: Array<{ name: string; entryPath: string; isSymlink: boolean; symlinkTarget?: string }> = [];
    for (const name of fs.readdirSync(this.root)) {
      if (name === STATE_FILENAME) continue;
      if (name.startsWith('.')) continue; // hide hidden files, but not hidden skills
      const entryPath = path.join(this.root, name);
      let stat: fs.Stats;
      try { stat = fs.lstatSync(entryPath); } catch { continue; }
      const isSymlink = stat.isSymbolicLink();
      let symlinkTarget: string | undefined;
      if (isSymlink) {
        try {
          const tgt = fs.readlinkSync(entryPath);
          symlinkTarget = path.isAbsolute(tgt) ? tgt : path.resolve(this.root, tgt);
        } catch { /* broken link is OK — we still list it with status=error */ }
      } else if (!stat.isDirectory()) {
        continue; // skip loose files
      }
      entries.push({ name, entryPath, isSymlink, symlinkTarget });
    }
    return entries;
  }

  /** Read a single entry into a Skill view. Returns null if the entry is unreadable. */
  private readSkill(
    entry: { name: string; entryPath: string; isSymlink: boolean; symlinkTarget?: string },
    disabled: Set<string>,
  ): Skill {
    const skillId = entry.name;
    const skillType: SkillType = entry.isSymlink ? 'symlink' : 'custom';
    const source = entry.isSymlink ? entry.symlinkTarget : entry.entryPath;

    // Try to read SKILL.md — follow symlinks so we read the source's SKILL.md.
    const skillMdPath = path.join(entry.entryPath, SKILL_MD_FILENAME);
    let fm: Frontmatter = {};
    let status: SkillStatus = 'installed';
    try {
      const s = fs.statSync(entry.entryPath);
      if (!s.isDirectory()) {
        // Broken symlink (readlinkSync worked but stat failed to follow)
        status = 'error';
      } else if (fs.existsSync(skillMdPath)) {
        fm = parseFrontmatter(fs.readFileSync(skillMdPath, 'utf8'));
      }
    } catch {
      status = 'error';
    }

    if (status !== 'error' && disabled.has(skillId)) status = 'disabled';

    const version = typeof fm.version === 'string' ? fm.version : undefined;
    const asList = (v: Frontmatter[string] | undefined): string[] => {
      if (Array.isArray(v)) return v;
      if (typeof v === 'string' && v.trim()) return [ v.trim() ];
      return [];
    };

    return {
      skillId,
      name: typeof fm.name === 'string' && fm.name ? fm.name : skillId,
      description: typeof fm.description === 'string' ? fm.description : '',
      skillType,
      source,
      enabled: status !== 'disabled',
      status,
      version,
      dependencies: asList(fm.dependencies),
      capabilities: asList(fm.capabilities),
      parameters: {},
    };
  }

  // ── CRUD ─────────────────────────────────────────────────────────────────

  list(): Skill[] {
    const { disabled } = this.loadState();
    const skills = this.listEntries().map(e => this.readSkill(e, disabled));
    return skills.sort((a, b) => a.skillId.localeCompare(b.skillId));
  }

  get(skillId: string): Skill | null {
    const entries = this.listEntries();
    const match = entries.find(e => e.name === skillId);
    if (!match) return null;
    const { disabled } = this.loadState();
    return this.readSkill(match, disabled);
  }

  /**
   * Install a SYMLINK skill: create `<root>/<skillId>` → `<source>`.
   * Validates:
   *   - source is an absolute, existing, readable directory
   *   - no existing entry at `<root>/<skillId>` (would be ALREADY_EXISTS)
   * Non-SYMLINK types throw NOT_IMPLEMENTED.
   */
  async installSymlink(skillId: string, source: string, enabled = true): Promise<Skill> {
    this.ensureRoot();
    if (!path.isAbsolute(source)) throw new Error(`INVALID_SOURCE:source must be absolute: ${source}`);
    let srcStat: fs.Stats;
    try { srcStat = fs.statSync(source); } catch { throw new Error(`INVALID_SOURCE:source not found: ${source}`); }
    if (!srcStat.isDirectory()) throw new Error(`INVALID_SOURCE:source must be a directory: ${source}`);

    const target = path.join(this.root, skillId);
    if (fs.existsSync(target) || isBrokenSymlink(target)) {
      throw new Error(`ALREADY_EXISTS:${skillId}`);
    }
    await fsp.symlink(source, target);

    if (!enabled) {
      const state = this.loadState();
      state.disabled.add(skillId);
      await this.saveState(state);
    }

    const skill = this.get(skillId);
    if (!skill) throw new Error(`INTERNAL_ERROR:symlink created but skill not readable: ${skillId}`);
    return skill;
  }

  /**
   * Remove a skill. Only symlinks are removed; real directories return
   * NOT_ALLOWED to avoid accidental deletion of user-authored content.
   * Also clears any disabled-state for the skill.
   */
  async uninstall(skillId: string): Promise<boolean> {
    const target = path.join(this.root, skillId);
    let stat: fs.Stats;
    try { stat = fs.lstatSync(target); } catch { return false; }
    if (!stat.isSymbolicLink()) {
      throw new Error(`NOT_ALLOWED:refusing to remove non-symlink skill: ${skillId}`);
    }
    await fsp.unlink(target);

    const state = this.loadState();
    if (state.disabled.has(skillId)) {
      state.disabled.delete(skillId);
      await this.saveState(state);
    }
    return true;
  }

  /** Toggle enable/disable via the state file. Returns whether the skill exists. */
  async setEnabled(skillId: string, enabled: boolean): Promise<Skill | null> {
    if (this.get(skillId) === null) return null;
    const state = this.loadState();
    const wasDisabled = state.disabled.has(skillId);
    if (enabled && wasDisabled) state.disabled.delete(skillId);
    else if (!enabled && !wasDisabled) state.disabled.add(skillId);
    else {
      return this.get(skillId); // no-op
    }
    await this.saveState(state);
    return this.get(skillId);
  }

  /** Update a SYMLINK skill's source path by rebuilding the link. */
  async updateSymlinkSource(skillId: string, newSource: string): Promise<Skill> {
    const entry = this.listEntries().find(e => e.name === skillId);
    if (!entry) throw new Error(`NOT_FOUND:${skillId}`);
    if (!entry.isSymlink) throw new Error(`NOT_ALLOWED:refusing to rebind non-symlink skill: ${skillId}`);
    if (!path.isAbsolute(newSource)) throw new Error(`INVALID_SOURCE:source must be absolute: ${newSource}`);

    const target = path.join(this.root, skillId);
    await fsp.unlink(target);
    await fsp.symlink(newSource, target);
    const skill = this.get(skillId);
    if (!skill) throw new Error(`INTERNAL_ERROR:${skillId}`);
    return skill;
  }
}

function isBrokenSymlink(p: string): boolean {
  try {
    const st = fs.lstatSync(p);
    if (!st.isSymbolicLink()) return false;
    try { fs.statSync(p); return false; } catch { return true; }
  } catch { return false; }
}
