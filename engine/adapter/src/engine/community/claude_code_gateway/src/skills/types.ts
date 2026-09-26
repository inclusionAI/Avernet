// Wire types for skills.* RPC methods.
//
// Shape matches AiCodingSkillsPlugin in OCB
// (src/engine/src/engine/engines/aicoding/skills.py):
//   - camelCase `skillId`, `skillType` on the wire
//   - flat (not nested under `config`): `source`, `target`, `enabled`
//   - `path` is accepted as an alias for `source` on the way out
//     (plugin's _skill_from_payload falls back to `path` if `source` missing)

export type SkillType = 'symlink' | 'package' | 'builtin' | 'custom';

export type SkillStatus =
  | 'installed'
  | 'available'
  | 'disabled'
  | 'error'
  | 'installing';

/** Canonical in-memory representation of a skill. */
export type Skill = {
  skillId: string;
  name: string;
  description: string;
  skillType: SkillType;
  /** For SYMLINK: absolute path the entry points at. For PACKAGE: pkg name/url. */
  source?: string;
  /** Unused today; kept for parity with OCB model. */
  target?: string;
  enabled: boolean;
  status: SkillStatus;
  version?: string;
  dependencies: string[];
  capabilities: string[];
  /** Free-form metadata (future: per-skill tuning knobs). */
  parameters: Record<string, unknown>;
};

export type SkillError = { code: string; message: string };

export type SkillResult<T = unknown> =
  | { ok: true; payload: T }
  | { ok: false; error: SkillError };
