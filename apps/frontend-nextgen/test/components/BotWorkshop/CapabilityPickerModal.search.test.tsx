/** @jest-environment jsdom */
import { CapabilityPickerModal } from '@/components/BotWorkshop/Editor/CapabilityPickerModal';
import { useSkillCenterPicker } from '@/hooks/useSkillCenterPicker';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({ getBotSkillPickerSources: () => ({ value: ['market', 'mine'] }) }),
}));
jest.mock('@/hooks/useSkillCenterPicker');
it('服务端命中不二次过滤，选择最多20项且可以取消选择', async () => {
  jest.mocked(useSkillCenterPicker).mockReturnValue({
    items: Array.from({ length: 21 }, (_, i) => ({ id: `sc-${i}`, name: `Result ${i}`, active: false })),
    loading: false,
    error: '',
    hasMore: true,
    loadMore: jest.fn(),
    retry: jest.fn(),
  });
  const confirm = jest.fn().mockResolvedValue(undefined);
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={[]}
      myItems={[]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={confirm}
    />,
  );
  fireEvent.change(screen.getByPlaceholderText('搜索市场中的 Skill'), { target: { value: 'server-only-match' } });
  expect(screen.getByText('Result 20')).toBeInTheDocument();
  for (let i = 0; i < 20; i++) fireEvent.click(screen.getByText(`Result ${i}`));
  expect(screen.getByText('Result 20').closest('button')).toBeDisabled();
  fireEvent.click(screen.getByText('Result 0'));
  expect(screen.getByText('Result 20').closest('button')).not.toBeDisabled();
  fireEvent.click(screen.getByText('Result 20'));
  fireEvent.click(screen.getByRole('button', { name: '添加（20）' }));
  await waitFor(() => expect(confirm).toHaveBeenCalled());
  expect(confirm.mock.calls[0][0]).toHaveLength(20);
});

test('SkillCenter 详情操作不改变已选集合', () => {
  jest.mocked(useSkillCenterPicker).mockReturnValue({
    items: [{ id: 'sc-code', name: 'SkillCenter 示例', active: false, source: 'skillcenter-market', version: '1.0' }],
    loading: false,
    error: '',
    hasMore: false,
    loadMore: jest.fn(),
    retry: jest.fn(),
  });
  const onViewSkill = jest.fn();
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={[]}
      myItems={[]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
      onViewSkill={onViewSkill}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: '查看SkillCenter 示例 Skill 详情' }));
  expect(onViewSkill).toHaveBeenCalledWith(expect.objectContaining({ id: 'sc-code', version: '1.0' }));
  expect(screen.getByRole('button', { name: '添加' })).toBeDisabled();
});

test('SkillCenter 卡片按 isOfficial/isRecommended/tagList 渲染官方/推荐/标签（与 /market/skill MarketItemCard 一致）', () => {
  jest.mocked(useSkillCenterPicker).mockReturnValue({
    items: [
      {
        id: 'sc-tagger',
        name: '官方推荐 Skill',
        active: false,
        source: 'skillcenter-market',
        isOfficial: true,
        isRecommended: true,
        tags: ['数据分析', '内部共创'],
      },
    ],
    loading: false,
    error: '',
    hasMore: false,
    loadMore: jest.fn(),
    retry: jest.fn(),
  });
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={[]}
      myItems={[]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
    />,
  );
  expect(screen.getByText('官方')).toBeInTheDocument();
  expect(screen.getByText('推荐')).toBeInTheDocument();
  // tagList 拼成单 chip："数据分析 / 内部共创"
  expect(screen.getByText('数据分析 / 内部共创')).toBeInTheDocument();
});

test('无 isOfficial/isRecommended/tagList 的 Skill 不渲染对应徽标', () => {
  jest.mocked(useSkillCenterPicker).mockReturnValue({
    items: [{ id: 'sc-plain', name: '普通 Skill', active: false, source: 'skillcenter-market' }],
    loading: false,
    error: '',
    hasMore: false,
    loadMore: jest.fn(),
    retry: jest.fn(),
  });
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={[]}
      myItems={[]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
    />,
  );
  expect(screen.queryByText('官方')).not.toBeInTheDocument();
  expect(screen.queryByText('推荐')).not.toBeInTheDocument();
});
