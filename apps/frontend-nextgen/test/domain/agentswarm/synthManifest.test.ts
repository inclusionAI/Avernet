import { isSynthesized, synthesizeManifest } from '@/domain/agentswarm/synthManifest';
import { validateManifest } from '@/domain/agentswarm/manifest';

test('the synthesized document mirrors what the platform builds from instructions', () => {
  const doc = synthesizeManifest('你是一个数据清洗 Agent。');
  expect(doc).toContain('schema_version: 1');
  expect(doc).toContain('identity:');
  expect(doc).toContain('- type: SOUL.md');
  expect(doc).toContain('你是一个数据清洗 Agent。');
});

test('the synthesized document is valid YAML the form accepts', () => {
  expect(validateManifest(synthesizeManifest('任意指令')).issues).toEqual([]);
});

test('name and role never enter the synthesized persona', () => {
  // The backend only reads `instructions`; name/role only label the agent.
  const doc = synthesizeManifest('只包含指令内容');
  expect(doc).not.toContain('worker');
  expect(doc).not.toContain('职责');
});

test('empty instructions yield an empty content string rather than invalid YAML', () => {
  const doc = synthesizeManifest('');
  expect(doc).toContain('content: ""');
  expect(validateManifest(doc).issues.some((issue) => issue.path.includes('identity'))).toBe(true);
});

test('multiline instructions keep their line structure', () => {
  const doc = synthesizeManifest('第一行\n第二行');
  expect(doc).toContain('第一行');
  expect(doc).toContain('第二行');
  expect(validateManifest(doc).issues).toEqual([]);
});

test('a blank manifest means synthesized; a written one means user-authored', () => {
  expect(isSynthesized('')).toBe(true);
  expect(isSynthesized('   ')).toBe(true);
  expect(isSynthesized(null)).toBe(true);
  expect(isSynthesized('schema_version: 1')).toBe(false);
});