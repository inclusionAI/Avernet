/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen, within } from '@testing-library/react';
import { CapabilityPanel } from '@/components/BotWorkshop/Editor/CapabilityPanel';

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({ getBotMcpPickerEnabled: () => ({ value: true }) }),
}));

const props = () => ({
  botId: 'bot-with-capabilities', skillSets: [], mySkills: [{ id: 'local-1', name: 'writing-review', source: 'local' as const, active: false }],
  currentMcps: [{ serverCode: 'project-tools', name: 'project-tools', active: true }],
  availableMcps: [], marketSkills: [], skillCenterSkills: [], workshopSkills: [], editable: true,
  onCreate: jest.fn(), onDelete: jest.fn(), onActive: jest.fn(), onSkill: jest.fn(),
  onSkillCenterReferences: jest.fn(), onMcp: jest.fn(), onUploadSkillFolder: jest.fn(), onLoadCandidates: jest.fn(),
});

test('cloud management shows actual ungrouped Local Skill and MCP facts without claiming runtime or authorization', () => {
  render(<CapabilityPanel {...props()} />);
  const current = screen.getByRole('region', { name: 'Bot 当前能力' });
  expect(within(current).getByText('writing-review')).toBeVisible();
  expect(within(current).getByText('本地 Skill')).toBeVisible();
  expect(within(current).getByText('未启用')).toBeVisible();
  expect(within(current).getByText('project-tools')).toBeVisible();
  expect(within(current).getByText('已添加')).toBeVisible();
  expect(within(current).queryByText('已授权')).not.toBeInTheDocument();
  expect(within(current).queryByText('默认能力集')).not.toBeInTheDocument();
  expect(within(current).queryByRole('button')).not.toBeInTheDocument();
});

test('management retains the original read failure instead of presenting an empty success', () => {
  render(<CapabilityPanel {...props()} mySkills={[]} currentMcps={[]} loadError="该 MCP 需先完成用户授权" />);
  expect(screen.getByRole('alert')).toHaveTextContent('该 MCP 需先完成用户授权');
  expect(screen.queryByText('暂无能力集以外的本地 Skill 或 MCP 配置')).not.toBeInTheDocument();
});

test('a member already shown in a real set is not presented again as an ungrouped source', () => {
  const facts = props();
  render(<CapabilityPanel {...facts} skillSets={[{
    id: 'real-set', name: '评审能力', isDefault: false, active: true,
    skills: facts.mySkills, mcps: facts.currentMcps, clis: [],
  }]} />);
  // 全部成员已归入能力集时整块隐藏，不在能力面板顶部常驻空态文案
  expect(screen.queryByRole('region', { name: 'Bot 当前能力' })).not.toBeInTheDocument();
});
