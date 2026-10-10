/** @jest-environment jsdom */

import { CapabilityPickerModal } from '@/components/BotWorkshop/Editor/CapabilityPickerModal';
import '@testing-library/jest-dom';
import { act, render, screen, waitFor } from '@testing-library/react';
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

test('引用空间 Skill 保留接口返回的已添加项并明确标识', async () => {
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

  await userEvent.click(screen.getByRole('button', { name: '引用空间 Skill' }));

  expect(screen.getByText('已添加 Skill')).toBeInTheDocument();
  expect(screen.getByText('可添加 Skill')).toBeInTheDocument();
  expect(screen.getByText('已添加')).toBeInTheDocument();
  expect(screen.getByText('已添加 Skill').closest('button')).toBeDisabled();
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
  await userEvent.click(screen.getByRole('button', { name: '引用空间 Skill' }));

  expect(screen.getByPlaceholderText('搜索能力工坊中的 Skill')).toHaveValue('');
  expect(screen.getByText('可添加 Skill')).toBeInTheDocument();
});

test('桌面本地 Skill 的上传、启停和删除管理收敛到添加 Skill 弹窗', async () => {
  const onLocalToggle = jest.fn().mockResolvedValue(undefined);
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={[]}
      myItems={[{ id: 'local-1', name: '本地报告 Skill', source: 'local', active: true }]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
      onLocalToggle={onLocalToggle}
      onLocalDelete={jest.fn().mockResolvedValue(undefined)}
      onLocalUpload={jest.fn().mockResolvedValue(undefined)}
    />,
  );

  await userEvent.click(screen.getByRole('button', { name: '我的 Skill' }));

  expect(screen.getByText('本地 Skill 管理')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '上传 / 替换 ZIP' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '停用' }));
  expect(onLocalToggle).toHaveBeenCalledWith(expect.objectContaining({ id: 'local-1' }));
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

test('添加 MCP 渲染市场接口返回的候选项', () => {
  render(
    <CapabilityPickerModal
      kind="mcp"
      open
      marketItems={[
        {
          serverCode: 'mcp.ant.faas.skylarkmcpserver.skylarkmcpserver',
          name: '语雀 MCP',
          description: '语雀 MCP 服务，覆盖文档读写与知识库管理。',
          active: false,
        },
      ]}
      skillCenterItems={[]}
      workshopItems={[]}
      myItems={[]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
    />,
  );

  expect(screen.getByText('语雀 MCP')).toBeInTheDocument();
  expect(screen.getByText('语雀 MCP 服务，覆盖文档读写与知识库管理。')).toBeInTheDocument();
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

test('父组件重渲染并更换回调引用时不会重复查询 MCP', async () => {
  jest.useFakeTimers();
  const firstSearch = jest.fn().mockResolvedValue(undefined);
  const secondSearch = jest.fn().mockResolvedValue(undefined);
  const commonProps = {
    kind: 'mcp' as const,
    open: true,
    marketItems: [],
    skillCenterItems: [],
    workshopItems: [],
    myItems: [],
    existingIds: [],
    onOpenChange: jest.fn(),
    onConfirm: jest.fn(),
  };
  const { rerender } = render(<CapabilityPickerModal {...commonProps} onSearchMcp={firstSearch} />);

  await act(async () => jest.advanceTimersByTime(300));
  expect(firstSearch).toHaveBeenCalledTimes(1);

  rerender(<CapabilityPickerModal {...commonProps} onSearchMcp={secondSearch} />);
  await act(async () => jest.advanceTimersByTime(300));
  expect(secondSearch).not.toHaveBeenCalled();
  jest.useRealTimers();
});

test('引用空间 Skill 的详情操作独立于勾选', async () => {
  const onViewSkill = jest.fn();
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={[{ id: 'workshop-1', name: '工坊示例', active: false, source: 'workshop' }]}
      myItems={[]}
      existingIds={[]}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
      onViewSkill={onViewSkill}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: '引用空间 Skill' }));
  await userEvent.click(screen.getByRole('button', { name: '查看工坊示例 Skill 详情' }));
  expect(onViewSkill).toHaveBeenCalledWith(expect.objectContaining({ id: 'workshop-1', source: 'workshop' }));
  expect(screen.getByRole('button', { name: '添加' })).toBeDisabled();
});

test('我的 Skill 未添加时可查看详情，已添加时两个操作均禁用', async () => {
  const onViewSkill = jest.fn();
  render(
    <CapabilityPickerModal
      kind="skill"
      open
      marketItems={[]}
      skillCenterItems={[]}
      workshopItems={[]}
      myItems={[
        { id: 'local-1', name: '可查看', active: false, source: 'local' },
        { id: 'local-2', name: '已添加', active: true, source: 'local' },
      ]}
      existingIds={['local-2']}
      onOpenChange={jest.fn()}
      onConfirm={jest.fn()}
      onViewSkill={onViewSkill}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: '我的 Skill' }));
  expect(screen.getByRole('button', { name: '查看已添加 Skill 详情' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: '查看可查看 Skill 详情' }));
  expect(onViewSkill).toHaveBeenCalledWith(expect.objectContaining({ id: 'local-1', source: 'local' }));
  expect(screen.getByRole('button', { name: '添加' })).toBeDisabled();
});
