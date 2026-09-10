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
          bindingMode: 'plugin',
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

test('按绑定方式分栏展示渠道，并在 BCN 表单回显对应配置', () => {
  render(
    <ChannelConfigPanel
      editable
      channels={[
        {
          id: 2,
          type: 'dingding',
          bindingMode: 'bcn_gateway',
          description: '协作群',
          status: 'inactive',
          clientId: 'ding-app-2',
          hasSecret: true,
          robotCode: 'robot-2',
          enableStreamingCards: false,
          dmPolicy: 'open',
          allowlist: ['*'],
          replyToMessage: true,
          aixEnable: true,
          includeSenderName: true,
          groupChatScope: 'conversation_shared',
          outboundVisibility: 'lead_only',
        },
      ]}
      onCreate={jest.fn()}
      onUpdate={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  expect(screen.getByRole('tab', { name: '基于开源插件' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '基于BCN' }));
  fireEvent.click(screen.getByRole('button', { name: '编辑协作群' }));
  expect(screen.getByRole('dialog')).toHaveTextContent('基于 BCN');
  expect(screen.getByDisplayValue('robot-2')).toBeInTheDocument();
  expect(screen.getByText('群内共享会话')).toBeInTheDocument();
});

test('按老版渠道表格展示草稿环境、机器人标识、状态和操作', () => {
  render(
    <ChannelConfigPanel
      editable
      channels={[
        {
          id: 3,
          type: 'dingding',
          bindingMode: 'plugin',
          description: '发布通知群',
          status: 'active',
          clientId: 'ding-app-3',
          hasSecret: true,
          enableStreamingCards: false,
          dmPolicy: 'open',
          allowlist: ['*'],
          replyToMessage: true,
          aixEnable: true,
          includeSenderName: true,
          createdAt: '2026-09-10T08:00:00Z',
        },
      ]}
      onCreate={jest.fn()}
      onUpdate={jest.fn()}
      onToggle={jest.fn()}
      onDelete={jest.fn()}
    />,
  );

  expect(screen.getByText('场景描述')).toBeInTheDocument();
  expect(screen.getByText('绑定环境')).toBeInTheDocument();
  expect(screen.getByText('机器人 ID')).toBeInTheDocument();
  expect(screen.getByText('草稿态')).toBeInTheDocument();
  expect(screen.getByText('ding-app-3')).toBeInTheDocument();
});
