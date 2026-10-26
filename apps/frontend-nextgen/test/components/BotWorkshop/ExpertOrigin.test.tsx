/** @jest-environment jsdom */
import BotInfoCell from '@/components/BotWorkshop/BotCard/BotInfoCell';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
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
