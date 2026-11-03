/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import { BotRegistrationDialog } from '@/components/Workspace/IdentitySelector/BotRegistrationDialog';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useBotRegistrationToken } from '@/pages/Workspace/hooks/useBotRegistrationToken';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity');
jest.mock('@/services/workspace/botRegistrationService', () => ({
  resolveBcsEndpoint: () => 'https://bcs.example.com',
}));
jest.mock('@/pages/Workspace/hooks/useBotRegistrationToken', () => ({
  useBotRegistrationToken: jest.fn(() => ({
    token: 'test-token',
    expiresAt: 1900000000000,
    isLoading: false,
    error: null,
    bcsEndpoint: 'https://bcs.example.com',
    retry: jest.fn(),
  })),
}));

const baseUrl =
  'https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/apps/bcs/crates/plugins/openclaw-channel-bcn/install-instructions';
const writeText = jest.fn().mockResolvedValue(undefined);
const enterName = (value: string) =>
  fireEvent.change(screen.getByRole('textbox', { name: 'Bot 名称' }), { target: { value } });
const commandText = () => document.querySelector('pre')?.textContent;

beforeEach(() => {
  jest.mocked(useBotRegistrationToken).mockClear();
  jest.mocked(useHumanIdentity).mockReturnValue({
    identity: { userId: 'test-user', displayName: '测试用户', online: true },
    status: 'ready',
  });
  writeText.mockClear();
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  extendCapabilities({
    getOpenClawRegistrationEnabled: () => ({ status: 'available', value: true }),
    getAdditionalBotRegistrationPanels: () => ({ status: 'available', value: [] }),
    getDeepSeekHarnessRegistrationEnabled: () => ({ status: 'available', value: false }),
    getQwenWorkBotRegistrationConfig: () => ({ status: 'unsupported', value: { providerSlug: '', apiUrl: '' } }),
    getPoolabBridgeInstallation: () => ({ status: 'available', value: null }),
  });
});

it('hides OpenClaw without fetching its token when no onboarding panels remain', () => {
  extendCapabilities({
    getOpenClawRegistrationEnabled: () => ({ status: 'available', value: false }),
  });
  render(<BotRegistrationDialog open onClose={() => {}} />);
  expect(screen.queryByRole('button', { name: 'OpenClaw' })).not.toBeInTheDocument();
  expect(screen.queryByLabelText('OpenClaw 接入方式')).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'Bot 名称' })).not.toBeInTheDocument();
  expect(screen.getByText('暂无可用的接入方式')).toBeInTheDocument();
  expect(screen.getByText('当前未启用 Bot 接入方式，请联系管理员。')).toBeInTheDocument();
  expect(screen.queryByLabelText('Bot 类型')).not.toBeInTheDocument();
  expect(useBotRegistrationToken).toHaveBeenCalledWith(false);
  expect(useBotRegistrationToken).not.toHaveBeenCalledWith(true);
});

it('falls back to an available panel when the selected OpenClaw tab is disabled', () => {
  extendCapabilities({
    getAdditionalBotRegistrationPanels: () => ({ status: 'available', value: [
      { id: 'example', label: 'Example Bot', Panel: () => <p>Example onboarding</p> },
    ] }),
  });
  const view = render(<BotRegistrationDialog open onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'OpenClaw' }));
  expect(screen.getByLabelText('OpenClaw 接入方式')).toBeInTheDocument();
  jest.mocked(useBotRegistrationToken).mockClear();
  extendCapabilities({
    getOpenClawRegistrationEnabled: () => ({ status: 'available', value: false }),
  });
  view.rerender(<BotRegistrationDialog open onClose={() => {}} />);
  expect(screen.queryByRole('button', { name: 'OpenClaw' })).not.toBeInTheDocument();
  expect(screen.queryByLabelText('OpenClaw 接入方式')).not.toBeInTheDocument();
  expect(screen.getByText('Example onboarding')).toBeInTheDocument();
  expect(useBotRegistrationToken).not.toHaveBeenCalledWith(true);
});

it.each(['在终端执行', '让 Bot 执行'])('%s 在未填写名称时显示提示且不能复制', (mode) => {
  render(<BotRegistrationDialog open onClose={() => {}} />);
  enterName('');
  fireEvent.click(screen.getByRole('button', { name: new RegExp(mode) }));
  expect(commandText()).toBe('请输入 Bot 名称');
  expect(screen.getByRole('button', { name: /^复制(命令|指引)$/ })).toBeDisabled();
  enterName('   ');
  expect(commandText()).toBe('请输入 Bot 名称');
});

it('填写名称后展示并复制包含 --bot-name 的新安装命令', async () => {
  render(<BotRegistrationDialog open onClose={() => {}} />);
  enterName('  ExampleBot  ');
  const command = `bash -c "$(curl -fsSL ${baseUrl}/install.sh || echo "exit 1")" install.sh --token test-token --bcs-endpoint https://bcs.example.com --bot-name ExampleBot`;
  expect(commandText()).toBe(command);
  fireEvent.click(screen.getByRole('button', { name: '复制命令' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(command));
  await screen.findByRole('button', { name: '已复制' });
  enterName('AnotherBot');
  expect(screen.getByRole('button', { name: '复制命令' })).toBeEnabled();
  expect(commandText()).toContain('--bot-name AnotherBot');
});

it('Bot 指引使用原始名称并保留名称中的替换符文本', async () => {
  render(<BotRegistrationDialog open onClose={() => {}} />);
  enterName('测试 Bot $& {token}');
  fireEvent.click(screen.getByRole('button', { name: /让 Bot 执行/ }));
  const guide = `Follow the instructions in ${baseUrl}/install.md to join BCN (Bot Coordination Network), your NAME is '测试 Bot $& {token}', your TOKEN is test-token and BCS_ENDPOINT is https://bcs.example.com.`;
  expect(commandText()).toBe(guide);
  fireEvent.click(screen.getByRole('button', { name: '复制指引' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(guide));
  enterName('');
  expect(commandText()).toBe('请输入 Bot 名称');
  expect(screen.getByRole('button', { name: '复制指引' })).toBeDisabled();
});

it('安装命令将空格、单引号和 Shell 符号作为一个名称参数', () => {
  render(<BotRegistrationDialog open onClose={() => {}} />);
  enterName("Test Bot's $(echo literal)");
  expect(commandText()).toContain(`--bot-name 'Test Bot'"'"'s $(echo literal)'`);
});

it('使用登录用户名填充默认名称，重新打开时恢复默认值', () => {
  const onClose = jest.fn();
  const view = render(<BotRegistrationDialog open onClose={onClose} />);
  expect(screen.getByRole('textbox', { name: 'Bot 名称' })).toHaveValue('测试用户的 OpenClaw');
  expect(commandText()).toContain("--bot-name '测试用户的 OpenClaw'");
  expect(screen.queryByText('如何接入 OpenClaw？')).not.toBeInTheDocument();
  enterName('ExampleBot');
  view.rerender(<BotRegistrationDialog open={false} onClose={onClose} />);
  view.rerender(<BotRegistrationDialog open onClose={onClose} />);
  expect(screen.getByRole('textbox', { name: 'Bot 名称' })).toHaveValue('测试用户的 OpenClaw');
});

it('身份晚到时填充默认名称，但不覆盖用户编辑或清空的名称', () => {
  jest.mocked(useHumanIdentity).mockReturnValue({ identity: null, status: 'loading' });
  const view = render(<BotRegistrationDialog open onClose={() => {}} />);
  expect(commandText()).toBe('请输入 Bot 名称');
  const updateIdentity = (displayName: string) => {
    jest.mocked(useHumanIdentity).mockReturnValue({
      identity: { userId: 'test-user', displayName, online: true }, status: 'ready',
    });
    view.rerender(<BotRegistrationDialog open onClose={() => {}} />);
  };
  updateIdentity('测试用户');
  expect(screen.getByRole('textbox', { name: 'Bot 名称' })).toHaveValue('测试用户的 OpenClaw');
  enterName('CustomBot');
  updateIdentity('测试用户乙');
  expect(screen.getByRole('textbox', { name: 'Bot 名称' })).toHaveValue('CustomBot');
  enterName('');
  updateIdentity('测试用户丙');
  expect(commandText()).toBe('请输入 Bot 名称');
});
