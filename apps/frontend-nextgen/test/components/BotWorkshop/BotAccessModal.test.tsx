/** @jest-environment jsdom */

import { BotAccessModal } from '@/components/BotWorkshop/BotAccessModal';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const bot = {
  ...mapBotDto({ bot_id: 'bot-1', bot_name: '团队 Bot', engine: 'openclaw', actions: ['view'] }).item,
  ownership: 'team' as const,
};

const baseProps = {
  bot,
  spaces: [],
  loading: false,
  collaborators: [{ id: 1, userId: '1001', name: '成员甲', role: 'member' as const }],
  members: [{ userId: '149608', name: '小明' }],
  autoApproveEditorRequests: false,
  policyError: undefined,
  onClose: jest.fn(),
  onChangeSpace: jest.fn().mockResolvedValue(undefined),
  onCreateTeamAndChangeSpace: jest.fn().mockResolvedValue(undefined),
  onAddCollaborator: jest.fn().mockResolvedValue(true),
  onUpdateCollaborator: jest.fn().mockResolvedValue(undefined),
  onRemoveCollaborator: jest.fn().mockResolvedValue(undefined),
  onEditorRequestPolicyChange: jest.fn().mockResolvedValue(undefined),
  onRequestAccess: jest.fn().mockResolvedValue(undefined),
};

test('授权管理展示编辑权限申请自动通过策略并即时提交', () => {
  render(<BotAccessModal {...baseProps} mode="authorize" />);

  const policySwitch = screen.getByRole('switch', { name: '开启编辑权限申请自动通过' });
  fireEvent.click(policySwitch);

  expect(baseProps.onEditorRequestPolicyChange).toHaveBeenCalledWith(true);
});

test('授权为即时落库语义并在角色更新时展示局部加载', () => {
  render(<BotAccessModal {...baseProps} mode="authorize" operation="update:1" />);

  expect(screen.getByRole('button', { name: '完成' })).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: '成员甲 权限' })).toBeDisabled();
  expect(screen.getByLabelText('角色更新中')).toBeInTheDocument();
});

test('空间成员下拉选择后携带姓名和工号添加成员', () => {
  render(<BotAccessModal {...baseProps} mode="authorize" />);

  fireEvent.focus(screen.getByRole('textbox', { name: '搜索空间成员' }));
  fireEvent.click(screen.getByRole('button', { name: '小明（149608）' }));
  expect(screen.getByText(/小明（149608）/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '添加' }));

  expect(baseProps.onAddCollaborator).toHaveBeenCalledWith('149608', '小明', 'member');
});

test('变更归属空间支持创建新团队', () => {
  render(<BotAccessModal {...baseProps} mode="space" />);

  fireEvent.click(screen.getByRole('button', { name: '创建新团队' }));
  fireEvent.change(screen.getByPlaceholderText('新团队名称'), { target: { value: '研发团队' } });
  fireEvent.click(screen.getByRole('button', { name: '确认' }));

  expect(baseProps.onCreateTeamAndChangeSpace).toHaveBeenCalledWith('研发团队');
});

test('申请编辑权限时说明自动通过分支并防止重复提交', () => {
  render(<BotAccessModal {...baseProps} mode="request" operation="request" />);

  expect(screen.getByText(/Owner 已开启自动通过/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '处理中…' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
});
