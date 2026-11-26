import {
  EMPTY_MANIFEST_FORM,
  readManifestForm,
  seededManifestForm,
  writeManifestForm,
} from '@/domain/agentswarm/manifestForm';
import { validateManifest } from '@/domain/agentswarm/manifest';

const DOC = `schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      content: |
        You are a greeter.
  mcp:
    - name: search
`;

test('a manifest round-trips through the form model', () => {
  const form = readManifestForm(DOC);
  expect(form?.identity).toEqual([{ type: 'SOUL.md', content: 'You are a greeter.\n' }]);
  const written = writeManifestForm(form!);
  expect(validateManifest(written).ok).toBe(true);
  // The category the form does not edit survives.
  expect(written).toContain('search');
});

test('editing identity keeps the categories the form does not model', () => {
  const form = readManifestForm(DOC)!;
  const written = writeManifestForm({
    ...form,
    identity: [{ type: 'SOUL.md', content: 'Changed.' }],
  });
  expect(written).toContain('Changed.');
  expect(written).toContain('mcp:');
  expect(written).toContain('search');
});

test('an unparseable document yields no form so the caller stays in YAML', () => {
  expect(readManifestForm('manifest: [')).toBeNull();
});

test('a blank document is an empty form, not an error', () => {
  expect(readManifestForm('')).toEqual(EMPTY_MANIFEST_FORM);
  expect(readManifestForm(null)).toEqual(EMPTY_MANIFEST_FORM);
});

test('writing always emits schema_version 1', () => {
  // Anything else is refused by the platform, so the form must not invent one.
  expect(writeManifestForm(seededManifestForm())).toContain('schema_version: 1');
});

test('the seeded form carries the one identity an agent needs', () => {
  const form = seededManifestForm();
  expect(form.identity).toHaveLength(1);
  expect(validateManifest(writeManifestForm(form)).ok).toBe(true);
});