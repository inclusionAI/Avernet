import {
  readManifestInstructions,
  synthesizeManifest,
  syncManifestInstructions,
} from '@/domain/agentswarm/synthManifest';
import { validateManifest } from '@/domain/agentswarm/manifest';

// 指令 and the YAML's SOUL.md content are the same text, edited from either side.

test('a blank manifest is synthesized from 指令', () => {
  const manifest = syncManifestInstructions('', 'be brief');
  expect(manifest).toContain('SOUL.md');
  expect(manifest).toContain('be brief');
  expect(readManifestInstructions(manifest)).toBe('be brief');
});

test('editing 指令 rewrites only the SOUL.md content', () => {
  // mcp/skills and a second identity document must survive: this edits the
  // document rather than regenerating it from the instructions.
  const original = [
    'schema_version: 1',
    'manifest:',
    '  identity:',
    '    - type: SOUL.md',
    '      content: old text',
    '    - type: MEMORY.md',
    '      content: kept',
    '  mcp:',
    '    - server_code: github',
    '',
  ].join('\n');

  const updated = syncManifestInstructions(original, 'new text');

  expect(readManifestInstructions(updated)).toBe('new text');
  expect(updated).toContain('MEMORY.md');
  expect(updated).toContain('kept');
  expect(updated).toContain('server_code: github');
  expect(updated).not.toContain('old text');
  expect(validateManifest(updated).ok).toBe(true);
});

test('a manifest with no SOUL.md gains one instead of losing the instructions', () => {
  const withoutSoul = [
    'schema_version: 1',
    'manifest:',
    '  identity:',
    '    - type: MEMORY.md',
    '      content: kept',
    '',
  ].join('\n');

  const updated = syncManifestInstructions(withoutSoul, 'added');
  expect(readManifestInstructions(updated)).toBe('added');
  expect(updated).toContain('kept');
});

test('the reverse direction reads 指令 out of the SOUL.md content', () => {
  const fromYaml = syncManifestInstructions(synthesizeManifest('from yaml'), 'from yaml');
  expect(readManifestInstructions(fromYaml)).toBe('from yaml');
});

test('an unparseable manifest is left untouched rather than clobbered', () => {
  // The user is mid-edit in a document we cannot model; rewriting it would
  // destroy whatever they were typing.
  const broken = 'manifest: [';
  expect(syncManifestInstructions(broken, 'x')).toBe(broken);
  expect(readManifestInstructions(broken)).toBeNull();
});

test('a document without a SOUL.md yields no 指令 to mirror', () => {
  expect(readManifestInstructions('schema_version: 1\nmanifest:\n  mcp: []\n')).toBeNull();
});

test('an unchanged instruction set returns the same string, so no needless re-render', () => {
  const original = syncManifestInstructions('', 'same');
  expect(syncManifestInstructions(original, 'same')).toBe(original);
});