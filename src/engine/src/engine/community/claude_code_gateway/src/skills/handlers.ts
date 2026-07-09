// RPC handlers for skills.* methods.
//
// Same shape as `src/mcp/handlers.ts` and `src/cron/handlers.ts`:
// each handler is a pure async function of (store, params) → SkillResult<T>.
// `server.ts#handleFrame` flattens the result into the WS response frame.
//
// Methods implemented in this phase:
//   - skills.list      — snapshot of ~/.claude/skills/
//   - skills.get       — { skill } or NOT_FOUND
//   - skills.install   — only `skillType: symlink` supported in phase-1;
//                        others return NOT_IMPLEMENTED. ALREADY_EXISTS on
//                        duplicate skillId, INVALID_SOURCE for bad paths.
//   - skills.uninstall — removes symlinks; real dirs return NOT_ALLOWED so we
//                        never clobber user-authored content accidentally.
//   - skills.update    — handles the three shapes AiCodingSkillsPlugin sends:
//                          1) `{skillId, enabled: true|false}`  — enable/disable
//                          2) `{skillId, source: "..."}`         — rebind symlink
//                          3) both — apply sequentially
//                        NOT_FOUND when the skill doesn't exist.
//
// Non-SYMLINK skill types (PACKAGE, BUILTIN, CUSTOM) deliberately return
// NOT_IMPLEMENTED on the write path. The plugin already copes with failures
// from install/update/uninstall (raises RuntimeError / returns False), and
// the read path still surfaces pre-existing skills of any type so operators
// can inspect them.

import { createLogger } from '../debug.js';
import type { SkillsStore } from './store.js';
import type { Skill, SkillResult, SkillType } from './types.js';

const log = createLogger('skills');

// ---- small validators ---------------------------------------------------

function asObject(value: unknown): Record<string, unknown> {
  return (value && typeof value === 'object' && !Array.isArray(value))
    ? value as Record<string, unknown>
    : {};
}

function asString(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined;
}

function asSkillType(v: unknown): SkillType | undefined {
  const s = typeof v === 'string' ? v.trim().toLowerCase() : '';
  if (s === 'symlink' || s === 'package' || s === 'builtin' || s === 'custom') return s;
  return undefined;
}

function err(code: string, message: string): SkillResult<never> {
  return { ok: false, error: { code, message } };
}
function ok<T>(payload: T): SkillResult<T> { return { ok: true, payload }; }

// ---- handlers -----------------------------------------------------------

export async function handleList(
  store: SkillsStore,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _params: unknown,
): Promise<SkillResult<{ skills: Skill[] }>> {
  return ok({ skills: store.list() });
}

export async function handleGet(
  store: SkillsStore,
  params: unknown,
): Promise<SkillResult<{ skill: Skill }>> {
  const skillId = asString(asObject(params).skillId);
  if (!skillId) return err('INVALID_PARAMS', 'skillId is required');
  const skill = store.get(skillId);
  if (skill === null) return err('NOT_FOUND', `skill not found: ${skillId}`);
  return ok({ skill });
}

export async function handleInstall(
  store: SkillsStore,
  params: unknown,
): Promise<SkillResult<{ skill: Skill }>> {
  const p = asObject(params);
  const skillId = asString(p.skillId);
  if (!skillId) return err('INVALID_PARAMS', 'skillId is required');

  const skillType = asSkillType(p.skillType) ?? 'symlink';
  if (skillType !== 'symlink') {
    // Phase-1: the plugin's caller raises RuntimeError on non-ok result, so a
    // clear error message matters more than the code choice here.
    return err(
      'NOT_IMPLEMENTED',
      `skills.install for skillType=${skillType} is not yet supported by claude-code-gateway`,
    );
  }

  const source = asString(p.source);
  if (!source) return err('INVALID_PARAMS', 'source is required for skillType=symlink');
  const enabled = p.enabled === undefined ? true : Boolean(p.enabled);

  try {
    const skill = await store.installSymlink(skillId, source, enabled);
    log.debug('install', { skillId, skillType, source, enabled });
    return ok({ skill });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith('ALREADY_EXISTS:')) {
      return err('ALREADY_EXISTS', `skill already exists: ${skillId}`);
    }
    if (msg.startsWith('INVALID_SOURCE:')) {
      return err('INVALID_SOURCE', msg.slice('INVALID_SOURCE:'.length));
    }
    log.error('install:failed', { skillId, error: msg });
    return err('INTERNAL_ERROR', msg);
  }
}

export async function handleUninstall(
  store: SkillsStore,
  params: unknown,
): Promise<SkillResult<{ removed: boolean }>> {
  const skillId = asString(asObject(params).skillId);
  if (!skillId) return err('INVALID_PARAMS', 'skillId is required');
  try {
    const removed = await store.uninstall(skillId);
    log.debug('uninstall', { skillId, removed });
    return ok({ removed });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith('NOT_ALLOWED:')) {
      return err('NOT_ALLOWED', msg.slice('NOT_ALLOWED:'.length));
    }
    log.error('uninstall:failed', { skillId, error: msg });
    return err('INTERNAL_ERROR', msg);
  }
}

export async function handleUpdate(
  store: SkillsStore,
  params: unknown,
): Promise<SkillResult<{ skill: Skill }>> {
  const p = asObject(params);
  const skillId = asString(p.skillId);
  if (!skillId) return err('INVALID_PARAMS', 'skillId is required');

  // AiCodingSkillsPlugin uses this method for three different callers:
  //   - enable_skill   → { skillId, enabled: true }
  //   - disable_skill  → { skillId, enabled: false }
  //   - update_skill   → full SkillConfig, may include source / skillType
  const hasEnabled = p.enabled !== undefined;
  const newSource = asString(p.source);
  const newType = asSkillType(p.skillType);

  // Pre-flight: surface NOT_FOUND cleanly before we mutate.
  if (store.get(skillId) === null) {
    return err('NOT_FOUND', `skill not found: ${skillId}`);
  }

  try {
    // 1) source rebind — only meaningful for symlink skills
    if (newSource) {
      if (newType && newType !== 'symlink') {
        return err(
          'NOT_IMPLEMENTED',
          `skills.update for skillType=${newType} is not yet supported by claude-code-gateway`,
        );
      }
      await store.updateSymlinkSource(skillId, newSource);
    }
    // 2) enable/disable toggle
    if (hasEnabled) {
      await store.setEnabled(skillId, Boolean(p.enabled));
    }
    const skill = store.get(skillId);
    if (!skill) return err('INTERNAL_ERROR', `skill disappeared after update: ${skillId}`);
    log.debug('update', { skillId, newSource: newSource ?? null, enabled: hasEnabled ? Boolean(p.enabled) : null });
    return ok({ skill });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith('NOT_FOUND:')) {
      return err('NOT_FOUND', `skill not found: ${skillId}`);
    }
    if (msg.startsWith('NOT_ALLOWED:')) {
      return err('NOT_ALLOWED', msg.slice('NOT_ALLOWED:'.length));
    }
    if (msg.startsWith('INVALID_SOURCE:')) {
      return err('INVALID_SOURCE', msg.slice('INVALID_SOURCE:'.length));
    }
    log.error('update:failed', { skillId, error: msg });
    return err('INTERNAL_ERROR', msg);
  }
}

// ---- dispatch table -----------------------------------------------------

export type SkillsHandler = (store: SkillsStore, params: unknown) => Promise<SkillResult<unknown>>;

export const SKILLS_METHODS: Record<string, SkillsHandler> = {
  'skills.list': handleList,
  'skills.get': handleGet,
  'skills.install': handleInstall,
  'skills.uninstall': handleUninstall,
  'skills.update': handleUpdate,
};
