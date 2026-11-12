import { load as parseYaml } from 'js-yaml';
import { MANIFEST_DOCS_URL, MANIFEST_TEMPLATE } from '@/domain/agentswarm/manifest';
import { validateManifest } from '@/domain/agentswarm/manifest';

test('the shipped template parses and passes the form validator', () => {
  expect(validateManifest(MANIFEST_TEMPLATE).issues).toEqual([]);
  const parsed = parseYaml(MANIFEST_TEMPLATE) as Record<string, any>;
  expect(parsed.schema_version).toBe(1);
  expect(parsed.manifest.identity).toHaveLength(1);
  expect(parsed.manifest.identity[0].type).toBe('SOUL.md');
});

test('the template declares only identity, so subscribing to it removes nothing else', () => {
  // An empty list is a declaration that the set is empty and clears that area,
  // so the template must not hand a user `mcp: []` they would submit verbatim.
  const parsed = parseYaml(MANIFEST_TEMPLATE) as Record<string, any>;
  expect(Object.keys(parsed.manifest)).toEqual(['identity']);
});

test('the two most-used categories are shown as commented examples', () => {
  for (const category of ['mcp', 'skills']) {
    expect(MANIFEST_TEMPLATE).toMatch(new RegExp(`#\\s*${category}:`));
  }
});

test('the template interpolates its values rather than shipping placeholders', () => {
  expect(MANIFEST_TEMPLATE).not.toContain('${');
  expect(MANIFEST_TEMPLATE.split('\n')[0]).toContain('https://github.com');
});

test('uncommenting the two examples yields valid YAML', () => {
  // Uncommenting is the one edit a user is meant to make, so the commented
  // shape must be valid the moment it loses its `#`. Prose comments stay
  // commented, which is why this drops only lines that look like configuration.
  const uncommented = MANIFEST_TEMPLATE.split('\n')
    .map((line) => (/^(\s*)# (.+:|- .+)/.test(line) ? line.replace(/^(\s*)# /, '$1') : line))
    .join('\n');
  const parsed = parseYaml(uncommented) as Record<string, any>;
  expect(parsed.manifest.mcp[0].server_code).toBe('github');
  expect(parsed.manifest.skills[0].source.protocol).toBe('oss');
});

test('the template points at the full grammar', () => {
  expect(MANIFEST_DOCS_URL).toMatch(/^https:\/\/github\.com\/inclusionAI\/Avernet\//);
  expect(MANIFEST_TEMPLATE).toContain(MANIFEST_DOCS_URL);
});

test('the template stays short enough to read in a 440px panel', () => {
  // A template carrying every shape buries the fields the user came to edit.
  expect(MANIFEST_TEMPLATE.split('\n').length).toBeLessThan(40);
});
