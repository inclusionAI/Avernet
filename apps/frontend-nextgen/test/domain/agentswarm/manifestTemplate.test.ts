import { load as parseYaml } from 'js-yaml';
import { MANIFEST_LIMITS, MANIFEST_TEMPLATE } from '@/domain/agentswarm/manifest';
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

test('every other category is offered as a commented example, not an empty list', () => {
  for (const category of ['mcp', 'resources', 'skills', 'cli_tools']) {
    // The shape is present as guidance...
    expect(MANIFEST_TEMPLATE).toContain(`${category}:`);
    // ...but behind a comment, so the user opts in deliberately.
    expect(MANIFEST_TEMPLATE).toMatch(new RegExp(`#\\s*${category}:`));
  }
});

test('the template names the caveats a user would otherwise hit at submit', () => {
  // engine_config is refused outright; script is engine-gated.
  expect(MANIFEST_TEMPLATE).toContain('engine_config');
  expect(MANIFEST_TEMPLATE).toContain('script');
  expect(MANIFEST_TEMPLATE).toMatch(/MEMORY\.md/);
});

test('the limits list covers the refusals the gateway returns', () => {
  const joined = MANIFEST_LIMITS.join('\n');
  expect(joined).toContain('engine_config');
  expect(joined).toContain('MEMORY.md');
  expect(joined).toContain('server_code');
  expect(joined).toContain('digest');
});