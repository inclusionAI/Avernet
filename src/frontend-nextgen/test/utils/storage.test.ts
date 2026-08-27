import { storageKey, teamclawStorageKey } from '../../src/utils/storage';

describe('storage', () => {
  test('统一使用 tc_ 前缀', () => {
    expect(storageKey('workspace', 'active')).toBe('tc_workspace_active');
    expect(teamclawStorageKey('WORKSPACE_ACTIVE_SESSION')).toBe('tc_workspace_active_session');
  });
});
