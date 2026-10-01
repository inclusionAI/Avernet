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
