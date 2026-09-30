import {
  buildGroupOpeningMessage,
  getGroupOpeningMessageError,
} from './groupOpeningMessage';

describe('groupOpeningMessage', () => {
  it('omits blank input and preserves nonblank input byte-for-byte', () => {
    expect(buildGroupOpeningMessage(' \n ')).toBeUndefined();
    expect(buildGroupOpeningMessage('  Run {{bcs.run_id}}\n')).toBe(
      '  Run {{bcs.run_id}}\n',
    );
  });

  it.each([
    '{{bcs.group_id}}',
    '{{bcs.session_id}}',
    '{{bcs.group_name}}',
    '{{bcs.session_name}}',
  ])('accepts supported token %s', (token) => {
    expect(
      getGroupOpeningMessageError(`开始 ${token}`, 'chat'),
    ).toBeUndefined();
  });

  it('accepts run_id only for StateMachine', () => {
    expect(
      getGroupOpeningMessageError('{{bcs.run_id}}', 'state_machine'),
    ).toBeUndefined();
    expect(getGroupOpeningMessageError('{{bcs.run_id}}', 'chat')).toContain(
      '不支持',
    );
    expect(
      getGroupOpeningMessageError('{{bcs.run_id}}', 'manager_worker'),
    ).toContain('不支持');
  });

  it('rejects unknown and unterminated template variables', () => {
    expect(getGroupOpeningMessageError('{{group_id}}', 'chat')).toContain(
      '不支持',
    );
    expect(getGroupOpeningMessageError('{{bcs.run_id}', 'chat')).toContain(
      '未闭合',
    );
  });

  it('enforces the UTF-8 byte limit', () => {
    expect(getGroupOpeningMessageError('中'.repeat(22_000), 'chat')).toBe(
      '自定义开场白不能超过 64 KiB',
    );
  });
});
