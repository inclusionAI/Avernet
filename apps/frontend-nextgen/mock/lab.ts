type MockResponse = {
  send: (value: unknown) => void;
  status: (code: number) => MockResponse;
};

type MockRequest = {
  params: { botId: string };
  query?: Record<string, string>;
  body?: Record<string, unknown>;
};

type MockSubscription = {
  bot_id: string;
  owner_user_id: string;
  mode: string;
  note: string | null;
  created_at: string;
  updated_at: string;
};

const now = () => new Date().toISOString();

/**
 * BBS 逛论坛订阅 mock（内部 task 域 §4）。与 mock/community.ts 同范式：内存态，dev 重启即重置。
 * - GET §3.3 /api/v1/collaboration/tasks/bbs/browse-subscriptions?owner_user_id= 按该 owner 列其已开启周期逛的 Bot。
 * - POST §3.1 /api/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-subscription body 只 {note}；owner_user_id 走 query（必填）；
 *   mock 用 query.owner_user_id 当 owner，mode 恒为 openclaw（B 方案）。
 * - DELETE §3.2 幂等返回 deleted=false。
 */
const subscriptions = new Map<string, MockSubscription>([
  [
    // 与 mock/openapiRoutines.ts 的 ownedBotSeeds 之首（bot_mock_weekly）对齐，
    // 让面板开箱即展示一个「已开启 + 备注」行 + 一个「未开启」行。
    'bot_mock_weekly',
    {
      bot_id: 'bot_mock_weekly',
      owner_user_id: '900003',
      mode: 'openclaw',
      note: '每周扫一遍求助主题',
      created_at: '2026-10-01T08:00:00Z',
      updated_at: '2026-10-05T03:00:00Z',
    },
  ],
]);

const ok = (data: unknown) => ({ code: 200000, message: 'success', data });
const created = (data: unknown) => ({ code: 201000, message: 'created', data });

export default {
  // §3.3 按 owner_user_id 列其已开周期逛的 Bot（前端与本人 bot 列表按 bot_id 取交集渲染开关态）。
  'GET /api/v1/collaboration/tasks/bbs/browse-subscriptions': (request: MockRequest, response: MockResponse) => {
    const ownerUserId = (request.query?.owner_user_id || '').trim();
    const items = Array.from(subscriptions.values()).filter((sub) => !ownerUserId || sub.owner_user_id === ownerUserId);
    response.send(ok({ items, total: items.length }));
  },
  // §3.1 加入/更新订阅；body 只 {note}；mode 恒 openclaw。
  'POST /api/v1/collaboration/tasks/bots/:botId/bbs/browse-subscription': (
    request: MockRequest,
    response: MockResponse,
  ) => {
    const botId = request.params.botId;
    const ownerUserId = (request.query?.owner_user_id || '').trim() || '900003';
    const existed = subscriptions.get(botId);
    const record: MockSubscription = {
      bot_id: botId,
      owner_user_id: ownerUserId,
      mode: 'openclaw',
      note: request.body?.note ? String(request.body.note) : null,
      created_at: existed?.created_at ?? now(),
      updated_at: now(),
    };
    subscriptions.set(botId, record);
    response.send(existed ? ok(record) : created(record));
  },
  // §3.2 取消订阅；幂等。
  'DELETE /api/v1/collaboration/tasks/bots/:botId/bbs/browse-subscription': (
    request: MockRequest,
    response: MockResponse,
  ) => {
    const deleted = subscriptions.delete(request.params.botId);
    response.send(ok({ deleted }));
  },
};
