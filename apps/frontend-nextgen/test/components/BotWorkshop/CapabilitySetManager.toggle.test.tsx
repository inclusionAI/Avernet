/** @jest-environment jsdom */
import { CapabilitySetManager } from '@/components/BotWorkshop/Editor/CapabilitySetManager';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

jest.mock('@/capabilities', () => ({ getCapabilities: () => ({ getBotMcpPickerEnabled: () => ({ value: true }) }) }));

const props = {
  sets: [{ id: 'set-1', name: '个人能力集', isDefault: false, active: true, skills: [], mcps: [], clis: [] }],
  mySkills: [],
  marketSkills: [],
  skillCenterSkills: [],
  workshopSkills: [],
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

test.each([
  [{ id: 'set-1', active: false, slow: false }, '正在停用…'],
  [{ id: 'set-1', active: true, slow: false }, '正在启用…'],
  [{ id: 'set-1', active: false, slow: true }, '停用耗时较长，仍在等待结果…'],
] as const)('启停展示行级反馈并保持开关原状态', (pendingSkillSetToggle, text) => {
  render(<CapabilitySetManager {...props} pendingSkillSetToggle={pendingSkillSetToggle} />);
  expect(screen.getByRole('status')).toHaveTextContent(text);
  expect(screen.getByRole('switch', { name: '停用个人能力集' })).toBeChecked();
  expect(screen.getByRole('switch', { name: '停用个人能力集' })).toBeDisabled();
  expect(screen.getByText('个人能力集')).toBeInTheDocument();
});
