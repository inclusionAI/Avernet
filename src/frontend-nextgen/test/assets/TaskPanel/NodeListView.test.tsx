/** @jest-environment jsdom */
import { NodeListView } from '@/assets/TaskPanel/NodeListView';
import type { TaskNodeView } from '@/assets/TaskPanel/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const rootNode: TaskNodeView = {
  id: 'task-root',
  name: '根节点',
  sequence: 1,
  status: 'running',
  executor: null,
  executorColor: null,
  runMode: 'single_bot',
  sessionId: 'session-root',
  assignee: 'root-bot',
  hasSubTask: false,
  subTaskId: null,
  stepTraces: [],
  acceptanceResult: null,
  artifacts: [],
};

describe('NodeListView root session drill-down', () => {
  it('根节点没有 executor/name 时仍能通过 assignee 打开下钻 tab', () => {
    const onOpenGroupSession = jest.fn();
    render(
      <NodeListView
        nodes={[rootNode]}
        ownerBotId="root-bot"
        onViewNodeDetail={jest.fn()}
        onOpenGroupSession={onOpenGroupSession}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '查看执行会话 Bot执行会话' }));
    expect(onOpenGroupSession).toHaveBeenCalledWith(rootNode);
  });
});

const missNode: TaskNodeView = {
  id: 'comp_market_share',
  name: '市场份额分析',
  sequence: 3,
  status: 'hung',
  executor: null,
  executorColor: null,
  runMode: null,
  sessionId: null,
  assignee: null,
  missEvents: ['rule_single_candidate_random_miss'],
  hungReason: 'claim_on 筛选未命中',
  hasSubTask: false,
  subTaskId: null,
  stepTraces: [],
  acceptanceResult: null,
  artifacts: [],
};

describe('NodeListView MISS(派发未命中)节点展示', () => {
  it('MISS 节点执行人显示「未分配」,不回退为任务归属 bot,且不可下钻', () => {
    const onOpenGroupSession = jest.fn();
    render(
      <NodeListView
        nodes={[missNode]}
        ownerBotId="20260826_20rphqo0"
        onViewNodeDetail={jest.fn()}
        onOpenGroupSession={onOpenGroupSession}
      />,
    );

    expect(screen.getByText('未分配')).toBeInTheDocument();
    // 不得回退任务归属 bot id 当执行人
    expect(screen.queryByText('20260826_20rphqo0')).toBeNull();
    // 未分配节点 session 为空 → 不渲染可下钻入口
    expect(screen.queryByRole('button', { name: /查看执行会话/ })).toBeNull();
  });
});

const assignedWithMissNode: TaskNodeView = {
  id: 'sub_entry_opportunity',
  name: '存储行业进入机会分析',
  sequence: 4,
  status: 'running',
  executor: null,
  executorColor: null,
  runMode: 'coop_group',
  groupId: 'bcs_grp_f4d2eceff8e54b4381e3d06fa9c6f6d0',
  groupName: 'BCS协作群',
  sessionId: 'bcs_grp_f4d2eceff8e54b4381e3d06fa9c6f6d0:a612806b',
  assignee: 'bcs_grp_f4d2eceff8e54b4381e3d06fa9c6f6d0',
  missEvents: ['rule_single_candidate_random_miss'],
  hasSubTask: false,
  subTaskId: null,
  stepTraces: [],
  acceptanceResult: null,
  artifacts: [],
};

describe('NodeListView 已派发节点(带 miss_events)', () => {
  it('assignee 已派发时不显示「未分配」,按群名展示且可下钻', () => {
    const onOpenGroupSession = jest.fn();
    render(
      <NodeListView
        nodes={[assignedWithMissNode]}
        ownerBotId="20260826_20rphqo0"
        onViewNodeDetail={jest.fn()}
        onOpenGroupSession={onOpenGroupSession}
      />,
    );

    expect(screen.getByText('协作群会话')).toBeInTheDocument();
    expect(screen.queryByText('未分配')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '查看执行会话 协作群会话' }));
    expect(onOpenGroupSession).toHaveBeenCalledWith(assignedWithMissNode);
  });
});

const singleBotViaGroupNode: TaskNodeView = {
  id: 'single_bot_via_group',
  name: '技术栈概览',
  sequence: 2,
  status: 'running',
  executor: '技术栈概览Bot',
  executorColor: '#165DFF',
  runMode: 'single_bot',
  groupId: 'bcs_grp_single_override',
  groupName: 'BCS协作群',
  sessionId: 'bcs_grp_single_override:a612806b',
  assignee: 'bcs_grp_single_override',
  assigneeName: '技术栈概览Bot',
  hasSubTask: false,
  subTaskId: null,
  stepTraces: [],
  acceptanceResult: null,
  artifacts: [],
};

describe('NodeListView 群绕过单 bot(actual_run_mode 覆盖)', () => {
  it('真实单 bot 但物理走群 session:执行者显示 bot 名,跳转走协作群 URL(tab=group)', () => {
    const onOpenGroupSession = jest.fn();
    const { container } = render(
      <NodeListView
        nodes={[singleBotViaGroupNode]}
        ownerBotId="20260826_20rphqo0"
        onViewNodeDetail={jest.fn()}
        onOpenGroupSession={onOpenGroupSession}
      />,
    );
    // 执行模态标签按真实模式显示「单Bot」
    expect(screen.getByText('单Bot')).toBeInTheDocument();
    // 执行者展示 bot 名(非群名)
    expect(screen.getByText('技术栈概览Bot')).toBeInTheDocument();
    expect(screen.queryByText('BCS协作群')).toBeNull();
    // 跳转链接按物理群 session 走协作群视图(tab=group),而非单 bot tab=chat
    expect(container.querySelector('a[href*="tab=group"]')).not.toBeNull();
    expect(container.querySelector('a[href*="tab=chat"]')).toBeNull();
    // 可下钻点击触发副屏会话 tab
    fireEvent.click(screen.getByRole('button', { name: '查看执行会话 技术栈概览Bot' }));
    expect(onOpenGroupSession).toHaveBeenCalledWith(singleBotViaGroupNode);
  });
});

const bbsNode: TaskNodeView = {
  id: 'bbs-node',
  name: '内容接力创作',
  sequence: 2,
  status: 'running',
  executor: 'bcs_grp_e3cb538e1ece43869f00a9946835be8c',
  executorColor: '#F53F3F',
  runMode: 'bbs',
  groupId: 'bcs_grp_e3cb538e1ece43869f00a9946835be8c',
  groupName: 'BCS协作群',
  sessionId: 'bcs_grp_e3cb538e1ece43869f00a9946835be8c:round1',
  assignee: 'bcs_grp_e3cb538e1ece43869f00a9946835be8c',
  hasSubTask: false,
  subTaskId: null,
  stepTraces: [],
  acceptanceResult: null,
  artifacts: [],
};

describe('NodeListView bbs 节点执行者展示', () => {
  it('bbs(actual_run_mode)节点执行者统一展示「BBS执行会话」,不展示无意义的 bcs 群 id', () => {
    const onOpenGroupSession = jest.fn();
    render(
      <NodeListView
        nodes={[bbsNode]}
        ownerBotId="20260826_20rphqo0"
        onViewNodeDetail={jest.fn()}
        onOpenGroupSession={onOpenGroupSession}
      />,
    );
    expect(screen.getByText('BBS执行会话')).toBeInTheDocument();
    // 执行者位不展示 bcs 群 id
    expect(screen.queryByText('bcs_grp_e3cb538e1ece43869f00a9946835be8c')).toBeNull();
  });
});

const bbsWithAssigneeNameNode: TaskNodeView = {
  id: 'bbs-with-name',
  name: '内容接力创作',
  sequence: 3,
  status: 'running',
  executor: 'bcs_grp_bbs_named',
  executorColor: '#F53F3F',
  runMode: 'bbs',
  groupId: 'bcs_grp_bbs_named',
  groupName: 'BCS协作群',
  sessionId: 'bcs_grp_bbs_named:round1',
  assignee: 'bcs_grp_bbs_named',
  assigneeName: '内容接力Bot',
  hasSubTask: false,
  subTaskId: null,
  stepTraces: [],
  acceptanceResult: null,
  artifacts: [],
};

describe('NodeListView bbs 节点带 assignee_name', () => {
  it('bbs 绕过群且有 assignee_name 时执行者显示 bot 名(而非 BBS执行会话 占位)', () => {
    render(
      <NodeListView
        nodes={[bbsWithAssigneeNameNode]}
        ownerBotId="20260826_20rphqo0"
        onViewNodeDetail={jest.fn()}
        onOpenGroupSession={jest.fn()}
      />,
    );
    expect(screen.getByText('内容接力Bot')).toBeInTheDocument();
    expect(screen.queryByText('BBS执行会话')).toBeNull();
  });
});
