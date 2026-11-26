/**
 * A structured view of a bot-config manifest, for editing it as a form.
 *
 * The document on the wire is YAML and stays authoritative: this module only
 * maps the parts a form can edit onto that text and back. Anything the form does
 * not model (unknown keys, comments) is preserved from the source text on a
 * field edit, so switching to the form does not silently strip a hand-written
 * document.
 */
import { dump, load } from 'js-yaml';

export interface IdentityEntry {
  type: string;
  content: string;
}

export interface ManifestForm {
  /** Raw-ish list of identity documents; the only category the form edits. */
  identity: IdentityEntry[];
  /** Kept verbatim so a category the form does not edit survives a round trip. */
  rest: Record<string, unknown>;
  /** Old `script` block, also not modelled by the form. */
  script?: unknown;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** Categories the structured form edits. Everything else is carried through. */
const EDITED_CATEGORY = 'identity';

export const EMPTY_MANIFEST_FORM: ManifestForm = { identity: [], rest: {} };

/**
 * Parses a manifest into the form model.
 *
 * Returns null when the text does not parse: the caller then keeps the user in
 * raw mode rather than showing an empty form that would overwrite their work.
 */
export function readManifestForm(source: string | null | undefined): ManifestForm | null {
  const text = (source || '').trim();
  if (!text) return { ...EMPTY_MANIFEST_FORM };
  let parsed: unknown;
  try {
    parsed = load(text);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  const manifest = isRecord(parsed.manifest) ? { ...parsed.manifest } : {};
  const identityRaw = Array.isArray(manifest[EDITED_CATEGORY]) ? manifest[EDITED_CATEGORY] : [];
  delete manifest[EDITED_CATEGORY];
  return {
    identity: identityRaw.filter(isRecord).map((entry) => ({
      type: typeof entry.type === 'string' ? entry.type : 'SOUL.md',
      content: typeof entry.content === 'string' ? entry.content : '',
    })),
    rest: manifest,
    script: parsed.script,
  };
}

/**
 * Writes the form model back to YAML.
 *
 * `schema_version` is always 1: the validator rejects anything else, and letting
 * the form emit a different one would produce a document the platform refuses.
 */
export function writeManifestForm(form: ManifestForm): string {
  const document: Record<string, unknown> = { schema_version: 1 };
  const manifest: Record<string, unknown> = { ...form.rest };
  manifest[EDITED_CATEGORY] = form.identity.map((entry) => ({
    type: entry.type,
    content: entry.content,
  }));
  document.manifest = manifest;
  if (form.script !== undefined) document.script = form.script;
  return dump(document, { lineWidth: 100, noRefs: true });
}

/** A blank form seeded with the one identity every agent needs. */
export function seededManifestForm(): ManifestForm {
  return {
    // Content is not blank: the validator refuses an identity with empty content,
    // and a seed the user has to repair before saving is not a useful default.
    identity: [
      {
        type: 'SOUL.md',
        content:
          'You are a general-purpose assistant agent.\n' +
          'Do what the task asks, report results honestly, and do not invent\n' +
          'information you were not given.\n',
      },
    ],
    rest: {},
  };
}