import { expect, it } from 'vitest';
import {
  callbackCompensationErrorMessage,
  callbackCompensationReason,
} from '../callbackCompensationErrors';

it('maps only allowlisted service codes to safe Chinese, never exception messages or response bodies', () => {
  expect(
    callbackCompensationErrorMessage({ data: { code: 'EXECUTION_DISABLED', message: 'secret=never' } }, 'fallback'),
  ).toBe('补偿执行尚未启用，当前只能预览。');
  for (const error of [
    new Error('secret=never'),
    { code: 'unknown', message: 'secret=never' },
    { data: { code: 'not-safe-secret', message: 'secret=never' } },
  ])
    expect(callbackCompensationErrorMessage(error, '安全提示')).toBe('安全提示');
  expect(callbackCompensationReason('TRUSTED_EVIDENCE_PROVIDER_MISSING')).toBe('未配置可信幂等证据提供方');
  expect(callbackCompensationReason('UNKNOWN_SERVER_CODE')).toBe('服务端尚未确认安全投递条件');
});

it('never displays an unrecognized raw reason string from preview/batch metadata', () => {
  expect(callbackCompensationReason('HTTP error: token=never')).toBe('服务端尚未确认安全投递条件');
});
