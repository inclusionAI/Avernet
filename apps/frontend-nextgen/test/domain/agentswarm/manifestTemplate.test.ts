import { load as parseYaml } from 'js-yaml';
import { MANIFEST_DOCS_URL, MANIFEST_LIMITS, MANIFEST_TEMPLATE } from '@/domain/agentswarm/manifest';
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

test('the template points at the full grammar', () => {
  expect(MANIFEST_DOCS_URL).toMatch(/^https:\/\/github\.com\/inclusionAI\/Avernet\//);
  expect(MANIFEST_TEMPLATE).toContain(MANIFEST_DOCS_URL);
});

test('the template stays short enough to read in a 440px panel', () => {
  // A template carrying every shape buries the fields the user came to edit.
  expect(MANIFEST_TEMPLATE.split('\n').length).toBeLessThan(40);
});

test('the caveats live in the limits list rather than bloating the template', () => {
  const joined = MANIFEST_LIMITS.join('\n');
  expect(joined).toContain('engine_config');
  expect(joined).toContain('script');
  expect(joined).toMatch(/MEMORY\.md/);
  // The clearing semantics are the one thing a reader must not guess.
  expect(joined).toContain('An empty list');
});

test('the limits list covers the refusals the gateway returns', () => {
  const joined = MANIFEST_LIMITS.join('\n');
  expect(joined).toContain('engine_config');
  expect(joined).toContain('MEMORY.md');
  expect(joined).toContain('server_code');
  expect(joined).toContain('digest');
});