import { dump as dumpYaml, load as parseYaml } from 'js-yaml';

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

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

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
/**
 * Writes new 指令 into the manifest's SOUL.md content, keeping everything else.
 *
 * 指令 and the SOUL.md content are the same text — the synthesizer copies one
 * into the other — so editing either must update the other. Everything else in
 * the document (mcp, skills, other identity files) is carried through untouched,
 * which is why this edits rather than regenerates.
 *
 * A blank manifest becomes the synthesized document: there is nothing to keep.
 */
export function syncManifestInstructions(manifest: string | null | undefined, instructions: string): string {
  const text = (manifest || '').trim();
  if (!text) return synthesizeManifest(instructions);

  let document: unknown;
  try {
    document = parseYaml(text);
  } catch {
    // Unparseable: the user is mid-edit in a document we cannot model. Leave it.
    return manifest as string;
  }
  if (!isRecord(document) || !isRecord(document.manifest)) return manifest as string;

  const identity = Array.isArray(document.manifest.identity) ? document.manifest.identity : [];
  const soulIndex = identity.findIndex(
    (entry) => isRecord(entry) && entry.type === DEFAULT_IDENTITY_FILE,
  );
  const content = instructions.replace(/\s+$/, '');

  if (soulIndex === -1) {
    // No SOUL.md yet: add one rather than dropping the instructions on the floor.
    identity.push({ type: DEFAULT_IDENTITY_FILE, content });
    document.manifest.identity = identity;
  } else {
    const entry = identity[soulIndex] as Record<string, unknown>;
    if (entry.content === content) return manifest as string;
    entry.content = content;
  }

  return dumpYaml(document, { lineWidth: 100, noRefs: true });
}

/**
 * Reads 指令 back out of a manifest's SOUL.md content.
 *
 * The reverse direction of `syncManifestInstructions`: when the user edits the
 * YAML's SOUL.md, the 指令 field should show the same text. Returns null when
 * there is no SOUL.md entry to read.
 */
export function readManifestInstructions(manifest: string | null | undefined): string | null {
  const text = (manifest || '').trim();
  if (!text) return null;
  let document: unknown;
  try {
    document = parseYaml(text);
  } catch {
    return null;
  }
  if (!isRecord(document) || !isRecord(document.manifest)) return null;
  const identity = Array.isArray(document.manifest.identity) ? document.manifest.identity : [];
  const soul = identity.find((entry) => isRecord(entry) && entry.type === DEFAULT_IDENTITY_FILE);
  if (!isRecord(soul) || typeof soul.content !== 'string') return null;
  return soul.content;
}

