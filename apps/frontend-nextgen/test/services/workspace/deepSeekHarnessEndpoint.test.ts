import { buildDeepSeekHarnessRegistrationCommand } from '@/services/workspace/deepSeekHarnessRegistrationCommand';

const input = { token: 'test-token', botName: 'Test Bot', profile: 'web' };

it.each([
  'https://bcs.example.com/bcn?tenant=a',
  'https://bcs.example.com/bcn#bot',
  'https://user:password@bcs.example.com/bcn',
  'wss://bcs.example.com/bcn',
  'invalid',
])('rejects endpoints that the plugin configure command cannot accept: %s', (bcsEndpoint) => {
  expect(buildDeepSeekHarnessRegistrationCommand({ ...input, bcsEndpoint })).toBe('');
});

it.each(['https://bcs.example.com/bcn/', 'http://127.0.0.1:21000', 'https://bcs.example.com/bcn%3Ftenant%23bot'])(
  'preserves supported endpoint paths and ports: %s',
  (bcsEndpoint) => {
    expect(buildDeepSeekHarnessRegistrationCommand({ ...input, bcsEndpoint })).toContain(bcsEndpoint);
  },
);
