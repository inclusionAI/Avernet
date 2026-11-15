/**
 * The persona document the platform synthesizes when an agent carries no
 * `config_manifest`.
 *
 * Mirrors `agentswarm/host.py`: a blank manifest becomes a schema_version-1
 * document whose only content is an identity entry, and whose `content` is the
 * agent's `instructions`. `name` and `role` are NOT part of it — they name the
 * agent and describe it, but never enter the persona.
 *
 * Shown read-only so the user can see what will actually be created instead of
 * guessing, without that draft overwriting a document they wrote themselves.
 */
/** The identity filename the platform uses when the spec names none. */
const DEFAULT_IDENTITY_FILE = 'SOUL.md';

export function synthesizeManifest(instructions: string): string {
  const content = instructions.trim();
  return [
    'schema_version: 1',
    'manifest:',
    '  identity:',
    `    - type: ${DEFAULT_IDENTITY_FILE}`,
    content ? '      content: |-' : '      content: ""',
    ...(content ? content.split('\n').map((line) => `        ${line}`.trimEnd()) : []),
  ].join('\n');
}

/** Whether an agent's manifest is blank, i.e. the platform will synthesize one. */
export function isSynthesized(manifest: string | null | undefined): boolean {
  return !(manifest || '').trim();
}