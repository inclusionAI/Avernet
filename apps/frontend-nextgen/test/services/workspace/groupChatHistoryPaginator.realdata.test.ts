/**
 * @jest-environment node
 *
 * 线上真实响应回放（2026-10-08 预发抓取）：messages 为信封、deliveries 查询为裸数组。
 * 回归「已终止」标注在真实数据形态下生效（含同 run 聚合、tool_result、空内容消息）。
 */
import type { DeliveryStatusView } from '@/domain/collaboration/types';
import { querySessionDeliveries } from '@/services/backendApi/collaboration/deliveryController';
import { listSessionMessages } from '@/services/backendApi/collaboration/sessionController';
import { GroupChatHistoryPaginator } from '@/services/workspace/groupChatHistoryPaginator';
import { describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/sessionController');
jest.mock('@/services/backendApi/collaboration/deliveryController');

const mockedList = listSessionMessages as jest.MockedFunction<typeof listSessionMessages>;
const mockedQuery = querySessionDeliveries as jest.MockedFunction<typeof querySessionDeliveries>;

// 线上真实响应（顺序：新→旧；长文本已截断，不影响映射逻辑）
const REAL_MESSAGES = [
  {
    id: '27505754-259d-42ed-b881-c98a68f9bc41',
    timestamp: 1791452595238,
    sender: 'default:410025',
    content: '',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: '4111936f-a97b-411b-9786-66e9586a9731',
  },
  {
    id: 'f0676d1a-b06e-4fc3-819e-ae9ec5546483',
    timestamp: 1791452585197,
    sender: 'human_410025',
    content: '写一个1000字文章，内容随意',
    message_type: 'bot',
    bot_name: '卓人',
    role: 'user',
  },
  {
    id: '807ae72e-0116-4266-b4ea-3343de56391e',
    timestamp: 1791448552325,
    sender: 'default:410025',
    content: 'NO_REPLY',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: '4a0db236-7e4c-44ff-b8a0-f17be82dba94',
  },
  {
    id: 'd50ea578-d30d-416a-9d31-ad874699c1f1',
    timestamp: 1791448542028,
    sender: 'default:410025',
    content: 'NO_REPLY',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: '78de59df-dd26-42c1-b8dc-c0de413fa4bc',
  },
  {
    id: '586b4865-039b-4364-9bd2-d200c7d9511a',
    timestamp: 1791448527642,
    sender: 'human_410025',
    content: '滴滴',
    message_type: 'bot',
    bot_name: '卓人',
    role: 'user',
  },
  {
    id: 'abbac56c-7b64-4f6c-acf7-982f8f25ec88',
    timestamp: 1791448526452,
    sender: 'human_410025',
    content: '滴滴',
    message_type: 'bot',
    bot_name: '卓人',
    role: 'user',
  },
  {
    id: 'fd9d970b-1c25-49a1-9197-b1ed0f78b669',
    timestamp: 1791447997974,
    sender: 'default:410025',
    content: '',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: '38c22113-9cc1-41c5-adb4-a3aeaee3119a',
  },
  {
    id: '9c77a8ec-714f-4dad-bac6-57e83d0e8c29',
    timestamp: 1791447990853,
    sender: 'human_410025',
    content: '@ALL-Bots 大家好',
    message_type: 'bot',
    bot_name: '卓人',
    role: 'user',
  },
  {
    id: 'c80b952c-bd46-4f7e-b57b-4b4ae98e3649',
    timestamp: 1791441104593,
    sender: 'default:410025',
    content: '',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: '57d2db48-f94f-4fa5-868c-63635f849c89',
  },
  {
    id: '3687116c-1266-436c-a7f1-556fc0b68396',
    timestamp: 1791441096249,
    sender: 'human_410025',
    content: '@ALL-Bots 大家好',
    message_type: 'bot',
    bot_name: '卓人',
    role: 'user',
  },
  {
    id: 'e5419be9-e009-4bff-8630-ea12ce18e05e',
    timestamp: 1791441070845,
    sender: 'default:410025',
    content: '刚上线，正在熟悉这个工作空间呢。你呢，在忙什么？',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: 'ebaec86b-d641-4695-a4a0-6f4b2260f75b',
  },
  {
    id: 'acfe5737-6e54-43d3-8a5b-b178bcf93fd6',
    timestamp: 1791441060100,
    sender: 'human_410025',
    content: '你在干嘛',
    message_type: 'bot',
    bot_name: '卓人',
    role: 'user',
  },
  {
    id: '94381cf5-d538-45ef-aa0b-4fbe39679392',
    timestamp: 1790236355519,
    sender: 'default:410025',
    content: '',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: '8e42c546-48b9-4739-b657-1c6a61acd5d5',
  },
  {
    id: 'c994e80e-af49-4496-850c-03ab7f0a5676',
    timestamp: 1790236354688,
    sender: 'default:410025',
    content: '...(tool)...',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'tool_result',
    run_id: '8e42c546-48b9-4739-b657-1c6a61acd5d5',
    metadata: {
      arguments: { path: '/usr/lib/node_modules/openclaw/skills/weather/SKILL.md' },
      is_error: false,
      result: 'x',
      tool_call_id: 'fc-0e525d7c',
      tool_name: 'read',
    },
  },
  {
    id: 'bfd0330e-ca34-42c1-914b-f91645fceb5c',
    timestamp: 1790236343706,
    sender: 'human_410025',
    content: '今天成都天气如何？',
    message_type: 'bot',
    bot_name: '卓人',
    role: 'user',
  },
  {
    id: '34519827-31ed-4177-8eda-fb70e862600f',
    timestamp: 1790235932175,
    sender: 'default:410025',
    content: '今天是 **9月24日，周四**。',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: '1fa08caf-fc36-4636-8b2a-b7ee609d8dc2',
  },
  {
    id: 'cff91fd0-3c59-4eb8-8a94-9635aa7f6aca',
    timestamp: 1790235915652,
    sender: 'human_410025',
    content: '今天星期几？',
    message_type: 'bot',
    bot_name: '卓人',
    role: 'user',
  },
  {
    id: 'a2294a49-86a9-47a1-9a03-52bae77ae4a2',
    timestamp: 1790235284790,
    sender: 'default:410025',
    content: '嘿，我是 Ray 👋',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: '0fa005bb-9b15-4544-a949-808e7beb42dd',
  },
  {
    id: 'f426c744-afbd-4af9-a440-9cc531b02782',
    timestamp: 1790235272278,
    sender: 'human_410025',
    content: '你可以做什么？',
    message_type: 'bot',
    bot_name: '卓人',
    role: 'user',
  },
  {
    id: 'b51210b6-93fd-49ea-9f4f-b10b66d26545',
    timestamp: 1790220695263,
    sender: 'default:410025',
    content: '嘿，我是 **ray**，刚上线。',
    message_type: 'bot',
    bot_name: 'ray',
    role: 'assistant',
    run_id: '86021ca0-1ecb-42a7-9dde-0e1343df3115',
  },
] as never[];

const delivery = (messageId: string, runId: string, status: string): DeliveryStatusView => ({
  delivery_id: `d-${runId.slice(0, 8)}`,
  message_id: messageId,
  target_bot_id: 'default:410025',
  flow_kind: 'group',
  kind: 'send',
  status: status as DeliveryStatusView['status'],
  state_version: 5,
  run_id: runId,
  wait_reason: null,
  admission_error: null,
});

const REAL_DELIVERIES: DeliveryStatusView[] = [
  delivery('f426c744-afbd-4af9-a440-9cc531b02782', '0fa005bb-9b15-4544-a949-808e7beb42dd', 'completed'),
  delivery('cff91fd0-3c59-4eb8-8a94-9635aa7f6aca', '1fa08caf-fc36-4636-8b2a-b7ee609d8dc2', 'completed'),
  delivery('bfd0330e-ca34-42c1-914b-f91645fceb5c', '8e42c546-48b9-4739-b657-1c6a61acd5d5', 'cancelled'),
  delivery('acfe5737-6e54-43d3-8a5b-b178bcf93fd6', 'ebaec86b-d641-4695-a4a0-6f4b2260f75b', 'completed'),
  delivery('3687116c-1266-436c-a7f1-556fc0b68396', '57d2db48-f94f-4fa5-868c-63635f849c89', 'cancelled'),
  delivery('9c77a8ec-714f-4dad-bac6-57e83d0e8c29', '38c22113-9cc1-41c5-adb4-a3aeaee3119a', 'cancelled'),
  delivery('abbac56c-7b64-4f6c-acf7-982f8f25ec88', '78de59df-dd26-42c1-b8dc-c0de413fa4bc', 'completed'),
  delivery('586b4865-039b-4364-9bd2-d200c7d9511a', '4a0db236-7e4c-44ff-b8a0-f17be82dba94', 'completed'),
  delivery('f0676d1a-b06e-4fc3-819e-ae9ec5546483', '4111936f-a97b-411b-9786-66e9586a9731', 'cancelled'),
];

describe('真实数据回放：历史消息 aborted 标注', () => {
  it('cancelled 投递命中的 run 全部标注为 aborted', async () => {
    mockedList.mockResolvedValue({ code: 20000, message: 'OK', data: REAL_MESSAGES } as never);
    // 线上真实响应：裸数组（无信封）。
    mockedQuery.mockResolvedValue(REAL_DELIVERIES);

    const paginator = new GroupChatHistoryPaginator('s1', 'me');
    const messages = await paginator.loadLatest();

    const byRun = messages.map((m) => ({
      id: m.id.slice(0, 8),
      role: m.role,
      status: m.status,
      runId: m.extra?.runId,
      botUuid: m.extra?.botUuid,
      content: (m.content ?? '').slice(0, 12),
    }));
    // eslint-disable-next-line no-console
    console.log(JSON.stringify(byRun, null, 1));

    const abortedRuns = messages.filter((m) => m.status === 'aborted').map((m) => m.extra?.runId);
    expect(new Set(abortedRuns)).toEqual(
      new Set([
        '57d2db48-f94f-4fa5-868c-63635f849c89',
        '38c22113-9cc1-41c5-adb4-a3aeaee3119a',
        '8e42c546-48b9-4739-b657-1c6a61acd5d5',
        '4111936f-a97b-411b-9786-66e9586a9731',
      ]),
    );
  });
});
