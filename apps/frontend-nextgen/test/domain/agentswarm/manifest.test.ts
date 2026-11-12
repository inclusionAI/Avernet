import {
  isBlankManifest,
  MANIFEST_TEMPLATE,
  normalizeManifest,
  summarizeIssues,
  validateManifest,
} from '@/domain/agentswarm/manifest';

test('a blank manifest is valid — it means "let the platform synthesize"', () => {
  for (const blank of ['', '   ', undefined, null]) {
    expect(validateManifest(blank ?? '').ok).toBe(true);
    expect(isBlankManifest(blank as string)).toBe(true);
  }
});

test('the provided template is itself valid', () => {
  const result = validateManifest(MANIFEST_TEMPLATE);
  expect(result.issues).toEqual([]);
  expect(result.ok).toBe(true);
});

test('a malformed YAML document is refused with the parser message', () => {
  const result = validateManifest('manifest: [unclosed');
  expect(result.ok).toBe(false);
  expect(result.issues[0].message.length).toBeGreaterThan(0);
});

test('a non-mapping top level is refused', () => {
  const result = validateManifest('- just\n- a list');
  expect(result.ok).toBe(false);
  expect(result.issues[0].message).toContain('键值映射');
});

test('absent categories are allowed — the platform only requires schema_version', () => {
  // The document the platform synthesizes itself carries nothing but `identity`,
  // so demanding all five here would reject a manifest the platform accepts.
  const result = validateManifest('schema_version: 1\nmanifest:\n  identity:\n    - type: SOUL.md\n      content: x\n');
  expect(result.ok).toBe(true);
  expect(result.issues).toEqual([]);
});

test('a category present but not a list is refused', () => {
  const doc = MANIFEST_TEMPLATE.replace('mcp: []', 'mcp: none');
  const result = validateManifest(doc);
  expect(result.issues).toContainEqual({ path: 'manifest.mcp', message: 'mcp 必须是列表' });
});

test('an empty identity list is refused, because a persona needs a soul', () => {
  const doc = MANIFEST_TEMPLATE.replace(
    /identity:\n(?:.*\n)*?\s+mcp:/,
    'identity: []\n  mcp:',
  );
  const result = validateManifest(doc);
  expect(result.issues.some((issue) => issue.path === 'manifest.identity')).toBe(true);
});

test('identity entries must carry type and content', () => {
  const doc = MANIFEST_TEMPLATE.replace("  - type: SOUL.md\n      content: |", '  - content: |').replace(
    /content: \|[\s\S]*?cli_tools/,
    'cli_tools',
  );
  const result = validateManifest(doc);
  expect(result.issues.some((issue) => issue.path.endsWith('.type'))).toBe(true);
});

test('a missing or unsupported schema_version is named', () => {
  expect(validateManifest(MANIFEST_TEMPLATE.replace('schema_version: 1\n', '')).issues[0].path).toBe('schema_version');
  expect(
    validateManifest(MANIFEST_TEMPLATE.replace('schema_version: 1', 'schema_version: 2')).issues[0].message,
  ).toContain('2');
});

test('normalizeManifest omits a blank document and preserves a real one', () => {
  expect(normalizeManifest('')).toBeUndefined();
  expect(normalizeManifest('   ')).toBeUndefined();
  expect(normalizeManifest(undefined)).toBeUndefined();
  expect(normalizeManifest(' schema_version: 1 ')).toBe('schema_version: 1');
});

test('summarizing reports one issue verbatim and counts the rest', () => {
  expect(summarizeIssues([])).toBe('');
  expect(summarizeIssues([{ path: 'a', message: 'bad' }])).toBe('a: bad');
  expect(summarizeIssues([{ path: '', message: 'bad' }])).toBe('bad');
  expect(
    summarizeIssues([
      { path: 'a', message: 'bad' },
      { path: 'b', message: 'worse' },
    ]),
  ).toContain('另有 1 项');
});