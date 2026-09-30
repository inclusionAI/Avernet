/** @jest-environment jsdom */

import { BotChatRelatedTraceList } from '@/components/BotWorkshop/BotChatLogs/BotChatRelatedTraceList';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

describe('BotChatRelatedTraceList', () => {
  it('群关联 Trace 缺少 Bot 名称时展示 Trace 自身的 Bot ID', () => {
    render(
      <BotChatRelatedTraceList
        page={{
          items: [
            {
              id: 'trace-other-bot',
              botId: 'other-bot',
              timestamp: '2026-08-24T00:00:00Z',
              name: 'Other bot trace',
              status: 'SUCCESS',
              latencyMs: 0,
              totalTokens: 0,
              totalCost: 0,
            },
          ],
          total: 1,
          page: 1,
          limit: 100,
          hasMore: false,
        }}
        scope="group"
        currentTraceId="trace-current"
        botName="Viewer Bot"
        botId="viewer-bot"
        loading={false}
        onOpenTrace={jest.fn()}
        onLoadMore={jest.fn()}
      />,
    );

    expect(screen.getByText('other-bot')).toBeInTheDocument();
    expect(screen.queryByText('Viewer Bot')).not.toBeInTheDocument();
  });
});
