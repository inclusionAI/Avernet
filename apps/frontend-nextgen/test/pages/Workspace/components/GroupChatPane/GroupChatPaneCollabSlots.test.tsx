/** @jest-environment jsdom */
// collab 形态任务栈回归（独立文件 = 独立模块注册表）：先注册全 null 槽位再静态 import
// GroupChatPane（paneTaskSlots 模块作用域常量按求值一次性绑定），验证任务下钻分支在
// linkedSession 命中时整体跳出（GroupTaskLinkedSession=null），输入区照常渲染
// （useResolvedGroupTaskExecution 绑定 null 兜底 hook，无任务装配、无占位实现抛错）。
// 与 GroupChatPane.test.tsx（registerOpenGroupTaskSlots 真实装配）互为形态对照组。
// SDK UI 桩复刻该套件写法：聚焦 header/副屏/Sender 存在性，不渲染真实 SDK 气泡/Markdown。
import '../../../../helpers/registerCollabNullSlots';
import type { GroupView, SessionView } from '@/domain/collaboration';
import { GroupChatPane } from '@/pages/Workspace/components/GroupChatPane';
import type { GroupChatState } from '@/services/workspace/groupChatProvider';
import { getExtensionSlots } from '@/extensions/slots';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { PanelHandle } from '@tc-chat/core';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';

jest.mock('@tc-chat/ui/es/Bubble', () => ({
  Bubble: (props: Record<string, unknown>) => {
    const blocks = props.blocks as Array<{ content?: string }> | undefined;
    return <div>{blocks?.map((block) => block.content).filter(Boolean).join('')}</div>;
  },
}));
jest.mock('@tc-chat/ui/es/BubbleList', () => ({
  BubbleList: ({ emptyPlaceholder, footer }: { emptyPlaceholder?: React.ReactNode; footer?: React.ReactNode }) => (
    <div data-testid="bubble-list">
      {emptyPlaceholder}
      {footer}
    </div>
  ),
}));
jest.mock('@tc-chat/ui/es/Sender', () => ({
  Sender: (props: { placeholder?: string; loading?: boolean }) => (
    <div>
      <input data-testid="sender" placeholder={props.placeholder} data-loading={props.loading ? 'true' : 'false'} readOnly />
    </div>
  ),
  ToolbarButton: () => null,
}));
jest.mock('@tc-chat/ui/es/SystemNotice', () => ({
  SystemNotice: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
jest.mock('@tc-chat/adapters', () => ({}));
jest.mock('@tc-chat/ui/es/ChatLayout', () => {
  const { forwardRef } = require('react');
  const ChatLayout = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  ChatLayout.Panel = forwardRef((_props: Record<string, unknown>, ref: unknown) => {
    void ref;
    return <div data-testid="panel-slot" />;
  });
  return { ChatLayout };
});
jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({ aixUiPlugin: {}, fileRefPlugin: {} }));
jest.mock('@/components/ui/Tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// 注意：不 mock '@/components/Workspace/TaskLinkedSession'——真实实现本身不带 testid（Open 套件的
// task-linked-session testid 只是 GroupChatPane.test.tsx 中 jest 桩件的产物）。不 mock 才成立如下证据链：
// 真实实现若挂载必然渲染 <section aria-label="任务联动会话">（隐式 role=region landmark，
// TaskLinkedSession.tsx），该 landmark 缺席 = 实现未进入渲染树；配合槽位注册表全 null，
// 下钻分支在 `linkedSession && GroupTaskLinkedSession` 求值处短路（GroupTaskLinkedSession undefined）。

const supportState: GroupChatState = { phase: 'ready', error: null };

const group: GroupView = {
  groupId: 'g1',
  name: '主站协作群',
  kind: 'free_chat',
  status: 'active',
  participants: [],
  participantCount: 0,
  sessions: [],
  lastMessageAt: 1,
  createdAt: 1,
  isPublic: false,
  deliveryPolicy: 'send_to_driver',
};

const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: '主会话',
  kind: 'chat',
  status: 'running',
  participants: [],
  lastMessageAt: 1,
  createdAt: 1,
  favorite: false,
};

function makeChat(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    messages: [],
    isRequesting: false,
    isDefaultMessagesRequesting: false,
    connectionStatus: 'connected',
    retryCount: 0,
    onRequest: () => {},
    abort: () => {},
    reconnect: () => {},
    ...overrides,
  } as never;
}

function renderPane(overrides: Partial<React.ComponentProps<typeof GroupChatPane>> = {}) {
  const { panelRef, ...rest } = overrides;
  return render(
    <MemoryRouter>
      <GroupChatPane
        group={group}
        session={session}
        chat={makeChat()}
        supportState={supportState}
        connectionStatus="connected"
        send={() => {}}
        submitPanelMessage={() => {}}
        submitTaskExecutionMessage={() => {}}
        abortBot={() => Promise.resolve()}
        abortingBotIds={new Set()}
        reconnect={() => {}}
        reloadHistory={() => {}}
        canManageGroup={{ allowed: false }}
        activePanel="none"
        onTogglePanel={() => {}}
        favorites={{ favorite: false, disabled: false, onToggle: () => {} }}
        sidePaneOpen={false}
        onToggleSidePane={() => {}}
        onRequestDissolve={() => {}}
        onRequestShareGroup={() => Promise.resolve({ ok: true, data: { invitationUrl: 'https://example.test/g' } })}
        onRequestShareSession={() => Promise.resolve({ ok: true, data: { invitationUrl: 'https://example.test/s' } })}
        panelRef={panelRef ?? React.createRef<PanelHandle>()}
        {...rest}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useTaskSidePanelStore.getState().resetAll();
});

describe('GroupChatPane — collab 空槽位（任务栈整体缺席，基础会话照常）', () => {
  it('文件环境：槽位注册表为全 null（对齐 collab 生产装配）', () => {
    expect(getExtensionSlots()).toEqual({
      groupTasks: null,
      groupServiceBotRouting: null,
      taskSessionNavigationObserver: null,
      taskPanelBootstrap: null,
      sessionTaskPanelTab: null,
    });
  });

  it('linkedSession 命中时不渲染任务下钻，群会话主体照常渲染', () => {
    useTaskSidePanelStore.getState().openLinkedSession({
      sessionId: 'bcs_grp_902:round:2',
      kind: 'group',
      groupId: 'g-902',
      botId: null,
    });

    renderPane();

    // 任务节点下钻分支整体短路：真实 TaskLinkedSession（未 mock）若挂载必渲染
    // <section aria-label="任务联动会话">（隐式 region landmark）——landmark 缺席即
    // 「实现未进入渲染树」的直接证据；Open 形态对照组见 GroupChatPane.test.tsx（桩件渲染）。
    expect(screen.queryByRole('region', { name: '任务联动会话' })).not.toBeInTheDocument();
    // 群会话主体（header 身份卡 + 副屏插槽）照常渲染（空消息走骨架屏占位，不经 BubbleList）。
    expect(screen.getByRole('group', { name: /主站协作群/ })).toBeInTheDocument();
    expect(screen.getByTestId('panel-slot')).toBeInTheDocument();
  });

  it('human 视角已加入会话时输入区照常渲染（null 兜底 task hook 无占位实现抛错）', () => {
    const humanIdentity = { id: 'human_1', kind: 'user' as const, displayName: '章梧', online: true };
    const humanPresentSession: SessionView = {
      ...session,
      participants: [{ actorId: 'human_1', kind: 'human', name: '章梧', role: 'member', mode: 'present' }],
    };
    // useResolvedGroupTaskExecution 绑定 null 兜底 hook：渲染不抛错、composer 正常挂载即证明
    // （jest 全局 setup 的抛错占位若被绑定，本用例会在渲染期显式失败）。
    renderPane({ session: humanPresentSession, activeIdentity: humanIdentity });
    expect(screen.getByTestId('sender')).toBeInTheDocument();
  });
});
