import type { CreateGroupParams } from '../types';

const GROUP_WEBHOOK_EVENT_FILTERS = [
  'group.*',
  'session.*',
  'task.*',
  'state_machine.*',
  'message.created',
];

export function getGroupWebhookUrlError(
  rawWebhookUrl: string,
): string | undefined {
  const webhookUrl = rawWebhookUrl.trim();
  if (!webhookUrl) return undefined;

  let parsedUrl: URL;
  try {
    parsedUrl = new URL(webhookUrl);
  } catch {
    return 'Webhook URL 格式不正确';
  }

  if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
    return 'Webhook URL 仅支持 HTTP 或 HTTPS';
  }
  if (parsedUrl.username || parsedUrl.password) {
    return 'Webhook URL 不能包含用户名或密码';
  }
  if (parsedUrl.search || parsedUrl.hash) {
    return 'Webhook URL 不能包含查询参数或片段';
  }
  return undefined;
}

export function buildGroupWebhookSubscriptions(
  rawWebhookUrl: string,
): CreateGroupParams['event_subscriptions'] {
  const webhookUrl = rawWebhookUrl.trim();
  if (!webhookUrl) return undefined;

  return [
    {
      name: 'group-webhook',
      event_filters: [...GROUP_WEBHOOK_EVENT_FILTERS],
      payload: { mode: 'metadata_only' },
      sink: {
        type: 'webhook',
        url: webhookUrl,
        request_timeout_ms: 2000,
      },
    },
  ];
}
