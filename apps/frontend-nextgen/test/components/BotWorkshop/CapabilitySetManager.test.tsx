/** @jest-environment jsdom */

import { CapabilitySetManager } from '@/components/BotWorkshop/Editor/CapabilitySetManager';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({ getBotMcpPickerEnabled: () => ({ status: 'available', value: true }) }),
}));

test('能力集以统一成员样式展示聚合接口返回的 CLI', () => {
  render(
    <CapabilitySetManager
      sets={[
        {
          id: '600005',
          name: '默认能力集',
          isDefault: true,
          active: true,
          skills: [],
          mcps: [],
          clis: [{ code: 'claude', name: 'Claude CLI', description: 'AI Coding CLI' }],
        },
      ]}
      mySkills={[]}
      marketSkills={[]}
      skillCenterSkills={[]}
      workshopSkills={[]}
      marketMcps={[]}
      editable
      onCreate={jest.fn()}
      onDelete={jest.fn()}
      onActive={jest.fn()}
      onSkill={jest.fn()}
      onSkillCenterReferences={jest.fn()}
      onMcp={jest.fn()}
      onUploadSkillFolder={jest.fn()}
      onLoadCandidates={jest.fn()}
    />,
  );

  expect(screen.getByText('0 Skill · 0 MCP · 1 CLI')).toBeInTheDocument();
  expect(screen.getByText('CLIs')).toBeInTheDocument();
  expect(screen.getByText('Claude CLI')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '移除Claude CLI' })).not.toBeInTheDocument();
});

test('系统默认能力集隐藏 Skill、MCP 和 CLI 的添加入口', () => {
  render(
    <CapabilitySetManager
      sets={[
        {
          id: '600005',
          name: '默认能力集',
          isDefault: true,
          active: true,
          skills: [{ id: 'skill-1', name: '内置 Skill', active: true }],
          mcps: [{ serverCode: 'mcp.default', name: '内置 MCP', active: true }],
          clis: [{ code: 'claude', name: 'Claude CLI' }],
        },
      ]}
      mySkills={[]}
      marketSkills={[]}
      skillCenterSkills={[]}
      workshopSkills={[]}
      marketMcps={[]}
      editable
      onCreate={jest.fn()}
      onDelete={jest.fn()}
      onActive={jest.fn()}
      onSkill={jest.fn()}
      onSkillCenterReferences={jest.fn()}
      onMcp={jest.fn()}
      onUploadSkillFolder={jest.fn()}
      onLoadCandidates={jest.fn()}
    />,
  );

  expect(screen.getByText('内置 Skill')).toBeInTheDocument();
  expect(screen.getByText('内置 MCP')).toBeInTheDocument();
  expect(screen.getByText('Claude CLI')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '添加' })).not.toBeInTheDocument();
  expect(screen.getByText('默认能力集')).toBeInTheDocument();
  expect(screen.getByTestId('default-capability-set-switch')).toBeInTheDocument();
});

test('默认能力集即使后端仍返回旧名称也统一展示新名称', () => {
  render(
    <CapabilitySetManager
      sets={[
        {
          id: '600005',
          name: '默认技能集',
          isDefault: true,
          active: true,
          skills: [],
          mcps: [],
          clis: [],
        },
      ]}
      mySkills={[]}
      marketSkills={[]}
      skillCenterSkills={[]}
      workshopSkills={[]}
      marketMcps={[]}
      editable
      onCreate={jest.fn()}
      onDelete={jest.fn()}
      onActive={jest.fn()}
      onSkill={jest.fn()}
      onSkillCenterReferences={jest.fn()}
      onMcp={jest.fn()}
      onUploadSkillFolder={jest.fn()}
      onLoadCandidates={jest.fn()}
    />,
  );

  expect(screen.getByText('默认能力集')).toBeInTheDocument();
  expect(screen.queryByText('默认技能集')).not.toBeInTheDocument();
});

test('移除 Skill 前要求二次确认', async () => {
  const onSkill = jest.fn().mockResolvedValue(undefined);
  render(
    <CapabilitySetManager
      sets={[
        {
          id: 'custom-set',
          name: '研发能力集',
          isDefault: false,
          active: true,
          skills: [{ id: 'skill-1', name: '代码审查', active: true }],
          mcps: [],
          clis: [],
        },
      ]}
      mySkills={[]}
      marketSkills={[]}
      skillCenterSkills={[]}
      workshopSkills={[]}
      marketMcps={[]}
      editable
      onCreate={jest.fn()}
      onDelete={jest.fn()}
      onActive={jest.fn()}
      onSkill={onSkill}
      onSkillCenterReferences={jest.fn()}
      onMcp={jest.fn()}
      onUploadSkillFolder={jest.fn()}
      onLoadCandidates={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '移除代码审查' }));
  expect(onSkill).not.toHaveBeenCalled();
  const dialog = await screen.findByRole('alertdialog');
  fireEvent.click(within(dialog).getByRole('button', { name: '确认移除' }));
  expect(onSkill).toHaveBeenCalledWith('custom-set', 'skill-1', false);
});

test('非默认能力集仍展示 Skill 和 MCP 的添加入口', () => {
  render(
    <CapabilitySetManager
      sets={[
        {
          id: 'custom-set',
          name: '自定义能力集',
          isDefault: false,
          active: true,
          skills: [],
          mcps: [],
          clis: [],
        },
      ]}
      mySkills={[]}
      marketSkills={[]}
      skillCenterSkills={[]}
      workshopSkills={[]}
      marketMcps={[]}
      editable
      onCreate={jest.fn()}
      onDelete={jest.fn()}
      onActive={jest.fn()}
      onSkill={jest.fn()}
      onSkillCenterReferences={jest.fn()}
      onMcp={jest.fn()}
      onUploadSkillFolder={jest.fn()}
      onLoadCandidates={jest.fn()}
    />,
  );

  expect(screen.getAllByRole('button', { name: '添加' })).toHaveLength(2);
});
