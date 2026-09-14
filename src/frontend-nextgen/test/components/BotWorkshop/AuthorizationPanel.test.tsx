/** @jest-environment jsdom */

import { AuthorizationPanel } from '@/components/BotWorkshop/CreateBotModal/AuthorizationPanel';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

test('AgentPass 授权按老版交互使用无外框的全屏 iframe', () => {
  render(
    <AuthorizationPanel
      authorization={{
        type: 'authorization_required',
        botId: 'bot-1',
        iframeUrl: 'https://agentpass.example/authorize',
        redirectUrl: '',
        request: {
          bot_name: '测试 Bot',
          bot_desc: '等待 AgentPass 授权',
          engine: 'openclaw',
          cluster_name: 'ACRA',
          bot_type: 'personal',
        },
      }}
    />,
  );

  const iframe = screen.getByTitle('Bot 授权');
  expect(iframe).toHaveAttribute('src', 'https://agentpass.example/authorize');
  expect(iframe).toHaveClass('h-full', 'w-full', 'border-none');
  expect(iframe.parentElement).toHaveClass('fixed', 'inset-0', 'z-[200]');
  expect(iframe.parentElement?.parentElement).toBe(document.body);
  expect(screen.queryByRole('button', { name: '打开授权页面' })).not.toBeInTheDocument();
});
