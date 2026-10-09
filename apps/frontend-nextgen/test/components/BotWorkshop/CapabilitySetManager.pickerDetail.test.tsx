/** @jest-environment jsdom */
import { CapabilitySetManager } from '@/components/BotWorkshop/Editor/CapabilitySetManager';
import { useSkillCenterPicker } from '@/hooks/useSkillCenterPicker';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const mockDetailUrl = jest.fn((skill: { source?: string }) => ({
  status: 'available',
  value: `/detail/${skill.source}`,
}));
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getBotMcpPickerEnabled: () => ({ value: true }),
    getBotSkillPickerSources: () => ({ value: ['market', 'workshop', 'mine'] }),
    getBotSkillPickerDetailUrl: (skill: { source?: string }) => mockDetailUrl(skill),
  }),
}));
jest.mock('@/hooks/useSkillCenterPicker');
jest.mock('@/components/BotWorkshop/Editor/CapabilityDetailDrawer', () => ({
  CapabilityDetailDrawer: ({
    target,
    botId,
    ownerId,
    onClose,
  }: {
    target?: { id: string };
    botId?: string;
    ownerId?: string;
    onClose: () => void;
  }) =>
    target ? (
      <div data-testid="local-detail" data-bot-id={botId} data-owner-id={ownerId}>
        {target.id}
        <button type="button" onClick={onClose}>
          返回选择器
        </button>
      </div>
    ) : null,
}));

const props = {
  botId: 'bot-1',
  ownerId: 'owner-1',
  sets: [{ id: 'set-1', name: '个人能力集', isDefault: false, active: true, skills: [], mcps: [], clis: [] }],
  mySkills: [{ id: '42', name: 'Local', active: false, source: 'local' as const }],
  marketSkills: [{ id: 'repo-1', name: 'Repo', active: false, source: 'teamclaw-market' as const }],
  skillCenterSkills: [],
  workshopSkills: [{ id: 'space-1', name: 'Space', active: false, source: 'workshop' as const }],
  marketMcps: [],
  editable: true,
  onCreate: jest.fn(),
  onDelete: jest.fn(),
  onActive: jest.fn(),
  onSkill: jest.fn(),
  onSkillCenterReferences: jest.fn(),
  onUploadSkillFolder: jest.fn(),
  onMcp: jest.fn(),
  onLoadCandidates: jest.fn(),
};

beforeEach(() => {
  jest
    .mocked(useSkillCenterPicker)
    .mockReturnValue({ items: [], loading: false, error: '', hasMore: false, loadMore: jest.fn(), retry: jest.fn() });
  mockDetailUrl.mockClear();
});

test('本地 Skill 复用当前 Bot content 详情抽屉，关闭后恢复原选择器', () => {
  render(<CapabilitySetManager {...props} />);
  fireEvent.click(screen.getAllByRole('button', { name: '添加' })[0]);
  fireEvent.click(screen.getByRole('button', { name: '我的 Skill' }));
  fireEvent.click(screen.getByText('Local'));
  expect(screen.getByRole('button', { name: '添加（1）' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '查看Local Skill 详情' }));
  expect(screen.getByTestId('local-detail')).toHaveAttribute('data-bot-id', 'bot-1');
  expect(screen.getByTestId('local-detail')).toHaveAttribute('data-owner-id', 'owner-1');
  expect(screen.queryByRole('dialog', { name: '添加 Skill' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '返回选择器' }));
  expect(screen.getByRole('dialog', { name: '添加 Skill' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '添加（1）' })).toBeEnabled();
  expect(mockDetailUrl).not.toHaveBeenCalled();
});

test('市场与工坊详情新开页，选择器保持打开', () => {
  const open = jest.spyOn(window, 'open').mockImplementation(() => null);
  render(<CapabilitySetManager {...props} />);
  fireEvent.click(screen.getAllByRole('button', { name: '添加' })[0]);
  fireEvent.click(screen.getByRole('button', { name: 'TeamClaw' }));
  fireEvent.click(screen.getByRole('button', { name: '查看Repo Skill 详情' }));
  expect(open).toHaveBeenCalledWith('/detail/teamclaw-market', '_blank', 'noopener,noreferrer');
  fireEvent.click(screen.getByRole('button', { name: '引用空间 Skill' }));
  fireEvent.click(screen.getByRole('button', { name: '查看Space Skill 详情' }));
  expect(open).toHaveBeenCalledWith('/detail/workshop', '_blank', 'noopener,noreferrer');
  expect(screen.getByRole('dialog', { name: '添加 Skill' })).toBeInTheDocument();
  open.mockRestore();
});
