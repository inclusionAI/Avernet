/** @jest-environment jsdom */

import { CapabilitySetManager } from '@/components/BotWorkshop/Editor/CapabilitySetManager';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

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
