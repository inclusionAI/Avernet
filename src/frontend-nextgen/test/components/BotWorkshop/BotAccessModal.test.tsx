/** @jest-environment jsdom */

import { BotAccessModal } from '@/components/BotWorkshop/BotAccessModal';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/components/Admin/SpaceMemberList/UserSearchDropdown', () => ({
  UserSearchDropdown: ({ onSelect }: { onSelect: (user: { userId: string; displayName: string }) => void }) => (
    <button type="button" onClick={() => onSelect({ userId: '149608', displayName: '小明' })}>
      选择员工
    </button>
  ),
}));

const bot = {
  ...mapBotDto({ bot_id: 'bot-1', bot_name: '团队 Bot', engine: 'openclaw', actions: ['view'] }).item,
  ownership: 'team' as const,
};

const baseProps = {
  bot,
  spaces: [],
  loading: false,
  collaborators: [{ id: 1, userId: '1001', name: '成员甲', role: 'member' as const }],
  onClose: jest.fn(),
  onChangeSpace: jest.fn().mockResolvedValue(undefined),
  onCreateTeamAndChangeSpace: jest.fn().mockResolvedValue(undefined),
  onAddCollaborator: jest.fn().mockResolvedValue(true),
  onUpdateCollaborator: jest.fn().mockResolvedValue(undefined),
  onRemoveCollaborator: jest.fn().mockResolvedValue(undefined),
  onRequestAccess: jest.fn().mockResolvedValue(undefined),
};

test('授权为即时落库语义并在角色更新时展示局部加载', () => {
  render(<BotAccessModal {...baseProps} mode="authorize" operation="update:1" />);

  expect(screen.getByRole('button', { name: '完成' })).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: '成员甲 权限' })).toBeDisabled();
  expect(screen.getByLabelText('角色更新中')).toBeInTheDocument();
});

test('员工搜索选择后携带姓名和工号添加成员', () => {
  render(<BotAccessModal {...baseProps} mode="authorize" />);

  fireEvent.click(screen.getByRole('button', { name: '选择员工' }));
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
