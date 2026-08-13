import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { resolveConfiguredSystemPrompt, resolveSystemPromptFile } from '../src/system-prompt.js';

describe('profile system prompt expansion', () => {
  let root: string;
  let profile: string;

  beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-profile-prompt-'));
    profile = path.join(root, 'platform-data');
    fs.mkdirSync(profile);
  });

  afterEach(() => {
    fs.rmSync(root, { recursive: true, force: true });
  });

  function write(relativePath: string, contents: string) {
    fs.writeFileSync(path.join(profile, relativePath), contents, 'utf8');
  }

  it('expands only relative Markdown imports and reports metadata without the prompt text', () => {
    write('CLAUDE.md', '# Identity\n@WORKFLOW.md\n@KNOWLEDGE.md\n@RULES.md\n@OUTPUT.md\n@MEMORY.md\n');
    for (const file of [ 'WORKFLOW.md', 'KNOWLEDGE.md', 'RULES.md', 'OUTPUT.md', 'MEMORY.md' ]) {
      write(file, `${file}-MARKER`);
    }

    const resolved = resolveSystemPromptFile(path.join(profile, 'CLAUDE.md'), profile);
    assert.equal(resolved.fileCount, 6);
    for (const file of [ 'WORKFLOW.md', 'KNOWLEDGE.md', 'RULES.md', 'OUTPUT.md', 'MEMORY.md' ]) {
      assert.match(resolved.prompt, new RegExp(`${file}-MARKER`));
    }
    assert.match(resolved.sha256, /^[a-f0-9]{64}$/);
  });

  it('rejects imports that leave the platform-data profile root', () => {
    fs.writeFileSync(path.join(root, 'outside.md'), 'outside', 'utf8');
    write('CLAUDE.md', '@../outside.md');

    assert.throws(
      () => resolveSystemPromptFile(path.join(profile, 'CLAUDE.md'), profile),
      /relative path inside the configured profile root/,
    );
  });

  it('rejects import cycles and nesting deeper than five levels', () => {
    write('CLAUDE.md', '@WORKFLOW.md');
    write('WORKFLOW.md', '@CLAUDE.md');
    assert.throws(
      () => resolveSystemPromptFile(path.join(profile, 'CLAUDE.md'), profile),
      /cycle/,
    );

    write('CLAUDE.md', '@A.md');
    write('A.md', '@B.md');
    write('B.md', '@C.md');
    write('C.md', '@D.md');
    write('D.md', '@E.md');
    write('E.md', '@F.md');
    write('F.md', 'too deep');
    assert.throws(
      () => resolveSystemPromptFile(path.join(profile, 'CLAUDE.md'), profile),
      /depth exceeds 5/,
    );
  });

  it('combines an optional inline prefix with the expanded profile prompt', () => {
    write('CLAUDE.md', 'PROFILE-MARKER');
    const prompt = resolveConfiguredSystemPrompt({
      RELAY_SYSTEM_PROMPT_PREFIX: 'INLINE-MARKER',
      RELAY_SYSTEM_PROMPT_FILE: path.join(profile, 'CLAUDE.md'),
      RELAY_SYSTEM_PROMPT_ROOT: profile,
    });
    assert.equal(prompt, 'INLINE-MARKER\n\nPROFILE-MARKER');
  });
});
