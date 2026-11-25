/** @jest-environment jsdom */
import BotInfoCell from '@/components/BotWorkshop/BotCard/BotInfoCell';
import { listBotInventory } from '@/services/backendApi/bots/botController';
import { useIdentityStore } from '@/stores/identityStore';
import { mapBotList, mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

test.each([
  { expert_name: '产品经理' },
  {
    ext: {
      expert_origin: { expert_name: '产品经理', source_commit: 'private-commit', content_digest: 'private-digest' },
    },
  },
])('公开及企业Bot来源仅显示专家名', (origin) => {
  const bot = mapBotDto({
    bot_id: 'my-bot',
    bot_name: '我的助手',
    engine: 'openclaw',
    status: 'ACTIVE',
    ...origin,
  }).item;
  render(<BotInfoCell bot={bot} />);
  expect(screen.getByText('来自专家市场 · 产品经理')).toBeVisible();
  expect(screen.queryByText(/private-commit|private-digest/)).not.toBeInTheDocument();
});


test('真实Inventory HTTP响应经列表mapper在管理表格展示来源，普通Bot不显示来源', async () => {
  const originalFetch = globalThis.fetch;
  useIdentityStore.setState({ currentIdentityId: '001' });
  const item = {
    bot_id: 'expert-bot', card_id: 'expert-bot', bot_name: '我的产品经理', bot_desc: '',
    engine: 'openclaw', bot_type: 'personal', kind: 'personal_cloud', deploy_mode: 'cloud',
    display_state: 'running', status: 'ACTIVE', owner_entity_id: '001',
    space: { space_id: '262', name: '个人空间', kind: 'personal' },
    template_config: null, actions: ['view', 'edit'], expert_name: '产品经理',
  };
  const fetchMock = jest.fn().mockResolvedValue({
    ok: true, headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 200000, data: { total: 2, items: [item, { ...item, bot_id: 'ordinary', card_id: 'ordinary', expert_name: null }] } }),
  });
  globalThis.fetch = fetchMock;
  try {
    const response = await listBotInventory({ space_id: 262 });
    const list = mapBotList(response.data, '001');
    render(<>{list.items.map((bot) => <BotInfoCell key={bot.id} bot={bot} />)}</>);
    expect(fetchMock).toHaveBeenCalledWith('/openapi/v1/bots/all?space_id=262&user_id=001', expect.objectContaining({ method: 'GET' }));
    expect(screen.getAllByText('来自专家市场 · 产品经理')).toHaveLength(1);
    expect(list.items[1].expertOriginName).toBeUndefined();
  } finally {
    globalThis.fetch = originalFetch;
    useIdentityStore.getState().reset();
  }
});
