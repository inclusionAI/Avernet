import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { resolveDefaultSessionModel } from '../src/gateway/handlers/sessions.js';

// resolveDefaultSessionModel priority chain:
//   RELAY_DEFAULT_MODEL (env) > role settings.json env.ANTHROPIC_MODEL
//   > RELAY_MODEL_SETTINGS_SOURCE env.ANTHROPIC_MODEL > 'claude-sonnet-4-5'
// settings.json directory mirrors the SDK / CLI subprocess:
//   RELAY_CLAUDE_CONFIG_DIR / CLAUDE_CONFIG_DIR, else <RELAY_CLAUDE_HOME|HOME>/.claude.
describe('resolveDefaultSessionModel', () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-default-model-'));
    // Ensure a clean env baseline so each case controls its own inputs.
    delete process.env.RELAY_DEFAULT_MODEL;
    delete process.env.RELAY_CLAUDE_CONFIG_DIR;
    delete process.env.RELAY_CLAUDE_HOME;
    delete process.env.RELAY_MODEL_SETTINGS_SOURCE;
    process.env.CLAUDE_CONFIG_DIR = tmpDir;
  });

  afterEach(() => {
    delete process.env.RELAY_DEFAULT_MODEL;
    delete process.env.CLAUDE_CONFIG_DIR;
    delete process.env.RELAY_CLAUDE_CONFIG_DIR;
    delete process.env.RELAY_CLAUDE_HOME;
    delete process.env.RELAY_MODEL_SETTINGS_SOURCE;
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  function writeSettings(content: string) {
    fs.writeFileSync(path.join(tmpDir, 'settings.json'), content);
  }

  // UT-a: settings.json has env.ANTHROPIC_MODEL → returns that value.
  it('returns settings.json env.ANTHROPIC_MODEL when set and no env override', () => {
    writeSettings(JSON.stringify({ env: { ANTHROPIC_MODEL: 'GLM-X' } }));
    assert.equal(resolveDefaultSessionModel(), 'GLM-X');
  });

  // UT-b: RELAY_DEFAULT_MODEL takes priority over settings.json.
  it('prefers RELAY_DEFAULT_MODEL over settings.json', () => {
    writeSettings(JSON.stringify({ env: { ANTHROPIC_MODEL: 'GLM-X' } }));
    process.env.RELAY_DEFAULT_MODEL = 'GLM-ENV';
    assert.equal(resolveDefaultSessionModel(), 'GLM-ENV');
  });

  // UT-c1: settings.json does not exist → fallback, no throw.
  it('falls back to claude-sonnet-4-5 when settings.json is missing', () => {
    assert.equal(resolveDefaultSessionModel(), 'claude-sonnet-4-5');
  });

  // UT-c2: settings.json is corrupt JSON → fallback, no throw.
  it('falls back to claude-sonnet-4-5 when settings.json is corrupt', () => {
    writeSettings('{bad');
    assert.equal(resolveDefaultSessionModel(), 'claude-sonnet-4-5');
  });

  // UT-c3: settings.json lacks env.ANTHROPIC_MODEL → fallback, no throw.
  it('falls back to claude-sonnet-4-5 when env.ANTHROPIC_MODEL is absent', () => {
    writeSettings(JSON.stringify({ env: {} }));
    assert.equal(resolveDefaultSessionModel(), 'claude-sonnet-4-5');
  });

  // env.ANTHROPIC_MODEL present but non-string → fallback, no throw.
  it('falls back to claude-sonnet-4-5 when env.ANTHROPIC_MODEL is not a string', () => {
    writeSettings(JSON.stringify({ env: { ANTHROPIC_MODEL: 123 } }));
    assert.equal(resolveDefaultSessionModel(), 'claude-sonnet-4-5');
  });

  // RELAY_DEFAULT_MODEL whitespace-only is treated as unset → falls through.
  it('treats whitespace-only RELAY_DEFAULT_MODEL as unset', () => {
    writeSettings(JSON.stringify({ env: { ANTHROPIC_MODEL: 'GLM-X' } }));
    process.env.RELAY_DEFAULT_MODEL = '   ';
    assert.equal(resolveDefaultSessionModel(), 'GLM-X');
  });

  it('uses the singlebox model-provider source when the role config has no settings file', () => {
    const sourcePath = path.join(tmpDir, 'model-provider-settings.json');
    fs.writeFileSync(sourcePath, JSON.stringify({ env: { ANTHROPIC_MODEL: 'local-compatible-model' } }));
    process.env.RELAY_MODEL_SETTINGS_SOURCE = sourcePath;

    assert.equal(resolveDefaultSessionModel(), 'local-compatible-model');
  });

  it('prefers a role-specific settings file over the singlebox model-provider source', () => {
    const sourcePath = path.join(tmpDir, 'model-provider-settings.json');
    fs.writeFileSync(sourcePath, JSON.stringify({ env: { ANTHROPIC_MODEL: 'source-model' } }));
    writeSettings(JSON.stringify({ env: { ANTHROPIC_MODEL: 'role-model' } }));
    process.env.RELAY_MODEL_SETTINGS_SOURCE = sourcePath;

    assert.equal(resolveDefaultSessionModel(), 'role-model');
  });
});
