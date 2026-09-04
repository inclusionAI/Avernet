/** @jest-environment jsdom */

import { CapabilitySetManager } from '@/components/BotWorkshop/Editor/CapabilitySetManager';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getBotCapabilitySetVisibility: () => ({ status: 'available', value: { mcp: false, cli: false } }),
  }),
}));

test('Open Core / 阿里云能力集不渲染 MCP 和 CLI 区块', () => {
  render(
    <CapabilitySetManager
      sets={[
        {
          id: 'custom-set',
          name: '自定义能力集',
          isDefault: false,
          active: true,
          skills: [],
          mcps: [{ serverCode: 'private-mcp', name: '内部 MCP', active: true }],
          clis: [{ code: 'private-cli', name: '内部 CLI' }],
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

  expect(screen.getAllByRole('button', { name: '添加' })).toHaveLength(1);
  expect(screen.queryByText('MCPs')).not.toBeInTheDocument();
  expect(screen.queryByText('CLIs')).not.toBeInTheDocument();
  expect(screen.queryByText('内部 MCP')).not.toBeInTheDocument();
  expect(screen.queryByText('内部 CLI')).not.toBeInTheDocument();
});
