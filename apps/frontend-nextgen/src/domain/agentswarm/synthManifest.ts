/**
 * The persona document for an agent that carries no `config_manifest`.
 *
 * Shown read-only so the user sees the real document instead of an empty box,
 * without that draft overwriting one they wrote themselves.
 *
 * The five content categories and the script block are always emitted, with
 * empty lists where the agent has nothing to declare. The platform's own
 * synthesis (`agentswarm/host.py`) writes only `identity`, which is equally
 * valid — an absent category and an empty one mean the same thing — but a
 * preview that omitted them read as an incomplete format rather than as a
 * complete one with nothing filled in.
 *
 * `name` and `role` are deliberately absent: they label the agent in a listing
 * and never enter the persona.
 */

/** The identity filename the platform uses when the spec names none. */
const DEFAULT_IDENTITY_FILE = 'SOUL.md';

/** Categories the manifest grammar defines, in the order it documents them. */
export const MANIFEST_CATEGORIES = ['identity', 'mcp', 'resources', 'skills', 'cli_tools'] as const;

export function synthesizeManifest(instructions: string): string {
  const content = instructions.trim();
  const lines = [
    'schema_version: 1',
    'manifest:',
    '  identity:',
    `    - type: ${DEFAULT_IDENTITY_FILE}`,
    content ? '      content: |-' : '      content: ""',
  ];
  if (content) {
    lines.push(...content.split('\n').map((line) => `        ${line}`.trimEnd()));
  }
  // Empty is the honest value: the agent declares no MCP server, resource,
  // skill or CLI tool, and the platform reads that the same as an absent key.
  lines.push('  mcp: []', '  resources: []', '  skills: []', '  cli_tools: []');
  return lines.join('\n');
}

/** Whether an agent's manifest is blank, i.e. the synthesized one applies. */
export function isSynthesized(manifest: string | null | undefined): boolean {
  return !(manifest || '').trim();
}