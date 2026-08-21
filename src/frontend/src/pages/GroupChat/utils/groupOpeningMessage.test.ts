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
    '{{bcs.run_id}}',
    '{{bcs.group_name}}',
    '{{bcs.session_name}}',
  ])('accepts supported token %s', (token) => {
    expect(getGroupOpeningMessageError(`开始 ${token}`)).toBeUndefined();
  });

  it('rejects unknown and unterminated template variables', () => {
    expect(getGroupOpeningMessageError('{{group_id}}')).toContain('不支持');
    expect(getGroupOpeningMessageError('{{bcs.run_id}')).toContain('未闭合');
  });

  it('enforces the UTF-8 byte limit', () => {
    expect(getGroupOpeningMessageError('中'.repeat(22_000))).toBe(
      '自定义开场白不能超过 64 KiB',
    );
  });
});
