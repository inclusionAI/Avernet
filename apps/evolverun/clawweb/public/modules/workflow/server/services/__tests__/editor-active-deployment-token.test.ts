import { describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { editorActiveDeploymentToken, InvalidEditorActiveDeploymentTokenInputError } from '../editor-active-deployment-token.js';

const row = {
  pack_id: 'pack', deploy_number: 9, version: 7, tag_name: 'deploy/flow/#9',
  action: 'deploy', spec_json: '{"title":"原文"}',
};

describe('active workflow baseline token', () => {
  it('is independent of SQL column order, extra fields, timestamps and integer representation', () => {
    const differentlyProjected = {
      gmt_modified: 'different driver representation', spec_json: row.spec_json, action: row.action,
      version: '7', tag_name: row.tag_name, pack_id: row.pack_id, deploy_number: '9', extra: 'ignored',
    };
    expect(editorActiveDeploymentToken('flow', differentlyProjected)).toBe(editorActiveDeploymentToken('flow', row));
    expect(editorActiveDeploymentToken('flow', row)).toMatch(/^[a-f0-9]{64}$/);
  });

  it('binds the workflow and every declared business field, including exact spec text', () => {
    const activeHash = editorActiveDeploymentToken('flow', row);
    expect(editorActiveDeploymentToken('other', row)).not.toBe(activeHash);
    for (const changed of [
      { pack_id: 'other' }, { deploy_number: 10 }, { version: 8 },
      { tag_name: null }, { action: 'rollback' }, { spec_json: '{ "title":"原文"}' },
    ]) expect(editorActiveDeploymentToken('flow', { ...row, ...changed })).not.toBe(activeHash);
  });

  it('rejects indistinguishable SQLite text decoded from distinct invalid UTF-8 bytes', () => {
    const db = new Database(':memory:');
    try {
      const first = db.prepare("SELECT CAST(X'80' AS TEXT) AS value").get() as { value: string };
      const second = db.prepare("SELECT CAST(X'81' AS TEXT) AS value").get() as { value: string };
      expect(first.value).toBe('\uFFFD');
      expect(second.value).toBe(first.value);
      for (const value of [first.value, second.value]) {
        expect(() => editorActiveDeploymentToken('flow', { ...row, spec_json: value })).toThrow('Ambiguous');
      }
    } finally {
      db.close();
    }
  });

  it('conservatively rejects a legitimate U+FFFD in every string field', () => {
    expect(() => editorActiveDeploymentToken('flow\uFFFD', row)).toThrow('Ambiguous');
    for (const changes of [
      { pack_id: 'pack\uFFFD' }, { tag_name: 'tag\uFFFD' },
      { action: 'deploy\uFFFD' }, { spec_json: '{"title":"\uFFFD"}' },
    ]) expect(() => editorActiveDeploymentToken('flow', { ...row, ...changes })).toThrow('Ambiguous');
    // An ASCII JSON escape is not an actual replacement character in stored text.
    expect(editorActiveDeploymentToken('flow', { ...row, spec_json: '{"title":"\\ufffd"}' })).toMatch(/^[a-f0-9]{64}$/);
  });

  it('uses the dedicated input error for malformed rows, integers and ambiguous text', () => {
    const inputs = [
      () => editorActiveDeploymentToken('flow', null as never),
      () => editorActiveDeploymentToken('flow', { ...row, spec_json: undefined as never }),
      () => editorActiveDeploymentToken('flow', { ...row, deploy_number: '09' }),
      () => editorActiveDeploymentToken('flow', { ...row, version: -1 }),
      () => editorActiveDeploymentToken('flow', { ...row, spec_json: 'ambiguous \uFFFD' }),
    ];
    for (const input of inputs) expect(input).toThrow(InvalidEditorActiveDeploymentTokenInputError);
  });

  it('fails closed on invalid required fields and non-canonical numeric values', () => {
    for (const value of [NaN, Infinity, -1, Number.MAX_SAFE_INTEGER + 1, '09', '1.0', '-1', '']) {
      expect(() => editorActiveDeploymentToken('flow', { ...row, deploy_number: value })).toThrow();
    }
    expect(() => editorActiveDeploymentToken('flow', { ...row, spec_json: undefined as unknown as string })).toThrow();
  });
});
