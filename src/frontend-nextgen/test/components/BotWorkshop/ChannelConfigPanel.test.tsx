/** @jest-environment jsdom */

import { ChannelConfigPanel } from '@/components/BotWorkshop/Editor/ChannelConfigPanel';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

beforeEach(() => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

test('已有渠道可进入编辑并回显后端配置', () => {
  render(
    <ChannelConfigPanel
      editable
      channels={[
        {
          id: 1,
          type: 'dingding',
          description: '研发群',
          status: 'active',
          clientId: 'ding-app-1',
          hasSecret: true,
          enableStreamingCards: true,
          cardTemplateId: 'tpl-1',
          dmPolicy: 'open',
          allowlist: ['1001'],
          replyToMessage: true,
          aixEnable: true,
          includeSenderName: true,
        },
      ]}
      onCreate={jest.fn()}
      onUpdate={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '编辑研发群' }));
  expect(screen.getByRole('dialog')).toHaveTextContent('编辑钉钉渠道');
  expect(screen.getByDisplayValue('ding-app-1')).toBeInTheDocument();
  expect(screen.getByDisplayValue('tpl-1')).toBeInTheDocument();
  expect(screen.getByPlaceholderText('留空则保持原 Secret')).toBeInTheDocument();
});
