/** @jest-environment jsdom */

import { CapabilityPickerModal } from '@/components/BotWorkshop/Editor/CapabilityPickerModal';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

test('Open Core / 阿里云部署添加 Skill 时仅展示我的 Skill', () => {
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={[]}
      myItems={[{ id: 'mine-1', name: '我的本地 Skill', active: true }]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
    />,
  );

  expect(screen.getByRole('button', { name: '我的 Skill' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '引用市场 Skill' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '引用工坊 Skill' })).not.toBeInTheDocument();
  expect(screen.getByText('我的本地 Skill')).toBeInTheDocument();
});
