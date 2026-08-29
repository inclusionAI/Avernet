/** @jest-environment jsdom */
import {
  GroupDrillDownPanel,
  filterGroupMessages,
  isNonMessageGroupContent,
  resolveMasterBot,
  type GroupMessage,
} from '@/assets/TaskPanel/GroupDrillDown';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

function makeMessage(overrides: Partial<GroupMessage> = {}): GroupMessage {
  return {
    id: 'message-1',
    sender: 'Bot A',
    bot_name: 'Bot A',
    content: '正常消息',
    message_type: 'bot',
    timestamp: '2026-08-22T10:00:00+08:00',
    ...overrides,
  };
}

describe('GroupDrillDown message filtering', () => {
  it('过滤 system 消息和 GroupContext 注入内容', () => {
    const systemMessage = makeMessage({ id: 'system', message_type: 'system', content: '节点开始执行' });
    const systemBotMessage = makeMessage({
      id: 'system-bot',
      message_type: 'bot',
      bot_name: 'system Bot',
      content: '系统执行回执',
    });
    const contextMessage = makeMessage({
      id: 'context',
      content: '<GroupContext>\n当前你在 bcn 群聊中，群聊相关信息如下\n## 群聊信息',
    });
    const normalMessage = makeMessage({ id: 'normal', content: '请分析当前数据架构。' });

    expect(isNonMessageGroupContent(systemMessage)).toBe(true);
    expect(isNonMessageGroupContent(systemBotMessage)).toBe(true);
    expect(isNonMessageGroupContent(contextMessage)).toBe(true);
    expect(isNonMessageGroupContent(normalMessage)).toBe(false);
    expect(filterGroupMessages([systemMessage, systemBotMessage, contextMessage, normalMessage])).toEqual([
      normalMessage,
    ]);
  });
});

describe('GroupDrillDown owner display', () => {
  it('使用 manager Bot 作为群 Owner，不回退到 group_id', () => {
    const owner = resolveMasterBot([
      { actor_id: 'group-id', actor_kind: 'bot', name: 'bcs_grp_xxx', role: 'worker', mode: 'auto' },
      { actor_id: 'master-id', actor_kind: 'bot', name: '业务架构视角Bot', role: 'manager', mode: 'auto' },
    ]);

    expect(owner).toMatchObject({ name: '业务架构视角Bot', role: 'manager', actor_kind: 'bot' });
  });
});

describe('GroupDrillDownPanel tabs', () => {
  it('点击标题前的关闭图标关闭对应群 Tab', async () => {
    const node = {
      id: 'node-group-1',
      name: '风险模型协同验证',
      sequence: 1,
      status: 'done' as const,
      executor: '群主 Bot',
      executorColor: '#165DFF',
      runMode: 'coop_group',
      startedAt: null,
      endAt: null,
      timeConsuming: null,
      output: null,
      outputSummary: null,
      artifacts: [],
      groupId: 'mock_group_1',
      sessionId: 'mock_session_1',
      hasSubTask: false,
      subTaskId: null,
      stepTraces: [],
      acceptanceResult: null,
    };
    const onClose = jest.fn();

    render(
      <GroupDrillDownPanel
        nodes={[node]}
        activeNodeId={node.id}
        bcsBaseUrl=""
        apiBaseUrl=""
        onSelect={jest.fn()}
        onClose={onClose}
      />,
    );
    const tab = screen.getByRole('tab');
    expect(tab).toHaveStyle('width: 190px');
    expect(tab).toHaveStyle('background: transparent');

    const closeButton = screen.getByRole('button', { name: '关闭 风险模型协同验证' });
    expect(closeButton.querySelector('svg')).toHaveAttribute('width', '15');
    fireEvent.mouseEnter(closeButton);
    expect(closeButton.querySelector('svg')).toHaveAttribute('width', '14');
    await waitFor(() => expect(screen.getAllByText('风险模型协同验证').length).toBeGreaterThanOrEqual(2));
    fireEvent.click(closeButton);

    await waitFor(() => expect(onClose).toHaveBeenCalledWith(node.id));
  });
});
