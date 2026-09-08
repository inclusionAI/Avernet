/** @jest-environment jsdom */

import { CapabilityPickerModal } from '@/components/BotWorkshop/Editor/CapabilityPickerModal';
import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getBotSkillPickerSources: () => ({ status: 'available', value: ['market', 'workshop', 'mine'] }),
  }),
}));

const skills = [
  { id: 'skill-1', name: '已添加 Skill', active: false },
  { id: 'skill-2', name: '可添加 Skill', active: false },
];

test('引用工坊 Skill 保留接口返回的已添加项并明确标识', async () => {
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={skills}
      myItems={[]}
      existingIds={['skill-1']}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
    />,
  );

  await userEvent.click(screen.getByRole('button', { name: '引用工坊 Skill' }));

  expect(screen.getByText('已添加 Skill')).toBeInTheDocument();
  expect(screen.getByText('可添加 Skill')).toBeInTheDocument();
  expect(screen.getByText('已添加')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /\u5df2\u6dfb\u52a0 Skill/ })).toBeDisabled();
});

test('切换能力来源时清空上一个 Tab 的搜索词', async () => {
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={skills}
      myItems={[]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
    />,
  );
  const search = screen.getByPlaceholderText('搜索市场中的 Skill');
  await userEvent.type(search, '不存在');
  await userEvent.click(screen.getByRole('button', { name: '引用工坊 Skill' }));

  expect(screen.getByPlaceholderText('搜索能力工坊中的 Skill')).toHaveValue('');
  expect(screen.getByText('可添加 Skill')).toBeInTheDocument();
});

test('添加 MCP 只展示引用市场 MCP，不展示引用工坊 MCP', () => {
  render(
    <CapabilityPickerModal
      kind="mcp"
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

  expect(screen.getByRole('button', { name: '引用市场 MCP' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '引用工坊 MCP' })).not.toBeInTheDocument();
});

test('MCP 按内部和开放平台分 Tab，并将搜索交给服务端查询', async () => {
  const searchMcp = jest.fn().mockResolvedValue(undefined);
  render(
    <CapabilityPickerModal
      kind="mcp"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={[]}
      myItems={[]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
      onSearchMcp={searchMcp}
    />,
  );

  expect(screen.getByRole('button', { name: '内部 MCP' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '开放平台' })).toBeInTheDocument();
  await waitFor(() => expect(searchMcp).toHaveBeenCalledWith('internal', ''));

  await userEvent.click(screen.getByRole('button', { name: '开放平台' }));
  await userEvent.type(screen.getByPlaceholderText('搜索市场中的 MCP'), '天气');
  await waitFor(() => expect(searchMcp).toHaveBeenLastCalledWith('open-platform', '天气'));
});
