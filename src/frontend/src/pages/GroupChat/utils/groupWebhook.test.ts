import {
  buildGroupWebhookSubscriptions,
  getGroupWebhookUrlError,
} from './groupWebhook';

describe('groupWebhook', () => {
  it('does not create a subscription for a blank URL', () => {
    expect(buildGroupWebhookSubscriptions('   ')).toBeUndefined();
    expect(getGroupWebhookUrlError('   ')).toBeUndefined();
  });

  it('builds the current group event subscription without auth', () => {
    expect(
      buildGroupWebhookSubscriptions('  http://127.0.0.1:28082/events  '),
    ).toEqual([
      {
        name: 'group-webhook',
        event_filters: [
          'group.*',
          'session.*',
          'task.*',
          'state_machine.*',
          'message.created',
        ],
        payload: { mode: 'metadata_only' },
        sink: {
          type: 'webhook',
          url: 'http://127.0.0.1:28082/events',
          request_timeout_ms: 2000,
        },
      },
    ]);
  });

  it.each([
    ['not-a-url', 'Webhook URL 格式不正确'],
    ['ftp://example.com/events', 'Webhook URL 仅支持 HTTP 或 HTTPS'],
    ['https://user@example.com/events', 'Webhook URL 不能包含用户名或密码'],
    [
      'https://example.com/events?token=secret',
      'Webhook URL 不能包含查询参数或片段',
    ],
    [
      'https://example.com/events#fragment',
      'Webhook URL 不能包含查询参数或片段',
    ],
  ])('rejects unsupported URL %s', (url, message) => {
    expect(getGroupWebhookUrlError(url)).toBe(message);
  });
});
