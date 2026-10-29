/** @jest-environment jsdom */
// 协作群 SDK 副屏接线回归（最小聚焦）：只验证 GroupChatPane → ChatLayoutPanelSlot 的
// 传参契约（宽 480 / 点外不收起）与 ChatLayout 对单聊同款 1px 分隔线样式的挂载类，
// 不复制主测试文件的全量夹具；插槽以捕获桩替换，空会话路径不触发 Sender/消息列表等 SDK 渲染。
import { GroupChatPane } from '@/pages/Workspace/components/GroupChatPane';
import type { GroupView } from '@/domain/collaboration';
import type { UseChatResult } from '@tc-chat/adapters';
import { render } from '@testing-library/react';
import '@testing-library/jest-dom/jest-globals';
import type { PanelHandle } from '@tc-chat/core';
import { createRef } from 'react';

let mockSlotProps: Array<Record<string, unknown>> = [];
jest.mock('@/components/Workspace/ChatLayoutPanelSlot', () => {
  const { forwardRef } = jest.requireActual('react');
  return {
    // forwardRef 吸收 GroupChatPane 传入的 panelRef（原组件为 forwardRef，桩保持同构零告警）。
    ChatLayoutPanelSlot: forwardRef((props: Record<string, unknown>) => {
      mockSlotProps.push(props);
      return null;
    }),
  };
});

const noop = () => undefined;

const group: GroupView = {
  groupId: 'g1',
  name: '接线协作群',
  kind: 'free_chat',
  status: 'active',
  participants: [],
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  participantCount: 0,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

/** 仅覆盖组件读取的最小 chat 形状；空会话不渲染 Sender/消息列表，无需 SDK 渲染桩。 */
const chat = {
  messages: [],
  isRequesting: false,
  isDefaultMessagesRequesting: false,
  connectionStatus: 'connected',
  retryCount: 0,
  onRequest: noop,
  abort: noop,
  reconnect: noop,
} as never as UseChatResult<unknown>;

it('副屏插槽接线：宽 480、点外不收起，并复用单聊 1px 分隔线样式', () => {
  mockSlotProps.length = 0;
  const { container } = render(
    <GroupChatPane
      group={group}
      session={null}
      chat={chat}
      supportState={{ phase: 'ready', error: null }}
      connectionStatus="connected"
      send={noop}
      submitPanelMessage={noop}
      submitTaskExecutionMessage={noop}
      abortBot={() => Promise.resolve()}
      abortingBotIds={new Set()}
      reconnect={noop}
      reloadHistory={noop}
      canManageGroup={{ allowed: false }}
      activePanel="none"
      onTogglePanel={noop}
      favorites={{ favorite: false, disabled: false, onToggle: noop }}
      sidePaneOpen={false}
      onToggleSidePane={noop}
      onRequestDissolve={noop}
      onRequestShareGroup={noop as never}
      onRequestShareSession={noop as never}
      panelRef={createRef<PanelHandle>()}
    />,
  );

  expect(container.querySelector('.workspace-chat-panel-layout')).toBeInTheDocument();
  const slotProps = mockSlotProps.at(-1) as { panelWidth?: number; dismissOnOutside?: boolean };
  expect(slotProps?.panelWidth).toBe(480);
  expect(slotProps?.dismissOnOutside).toBe(false);
});