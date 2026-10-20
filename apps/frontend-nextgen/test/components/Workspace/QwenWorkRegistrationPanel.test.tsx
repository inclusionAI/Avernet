/** @jest-environment jsdom */
import { QwenWorkRegistrationPanel } from '@/components/Workspace/IdentitySelector/QwenWorkRegistrationPanel';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

let mockEnvironment = 'PRE';
let mockDisplayName: string | null = null;

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({ identity: mockDisplayName ? { displayName: mockDisplayName } : null }),
}));
let mockInstallation: { installCommand: string; binary: string; profiles?: boolean; guideUrl?: string; personalizeBotName?: boolean; buildOnboardingCommand?: (input: any) => string } | null = null;

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getRuntimeEnvironment: () => ({ status: 'available', value: mockEnvironment }),
    getQwenWorkBridgeInstallation: () => ({ status: 'available', value: mockInstallation }),
    getQwenWorkBotRegistrationConfig: () => ({
      status: 'available',
      value: { providerSlug: 'local', apiUrl: 'https://bcs.example.com' },
    }),
  }),
}));
jest.mock('@/services/workspace/botRegistrationService', () => ({
  resolveBcsEndpoint: () => 'https://bcs.example.com',
}));
jest.mock('@/pages/Workspace/hooks/useBotRegistrationToken', () => ({
  useBotRegistrationToken: () => ({
    token: 'test-token',
    expiresAt: null,
    isLoading: false,
    error: null,
    retry: jest.fn(),
  }),
}));
it('provides a pinned installer download and separate installation before registration', async () => {
  render(<QwenWorkRegistrationPanel open />);
  expect(screen.getByRole('link', { name: '下载安装脚本' })).toHaveAttribute(
    'href',
    'https://github.com/inclusionAI/Avernet/releases/download/bcs-bridge-v0.1.4-beta.2/bcs-bridge-installer.sh',
  );
  expect(screen.getByRole('button', { name: '复制下载命令' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '复制校验与安装命令' })).toBeEnabled();
  expect(screen.queryByText('正在查询最新 Bridge…')).not.toBeInTheDocument();

  expect(screen.queryByLabelText(/项目目录/)).not.toBeInTheDocument();
  expect(screen.queryByText(/--cwd/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '复制接入命令' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: '复制启动命令' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '复制初始化命令' })).not.toBeInTheDocument();
  expect(screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)).toEqual(['1. 安装 Bridge', '2. 接入千问 Bot']);
  expect(screen.queryByRole('button', { name: '复制Bridge 配置模板' })).not.toBeInTheDocument();
  expect(screen.getByText(/0.1.4-beta.2 已包含 init/)).toBeInTheDocument();
  expect(screen.queryByText(/尚不支持/)).not.toBeInTheDocument();
});


it('uses deployment installation and executable throughout the onboarding flow', () => {
  mockInstallation = { installCommand: 'deployment-package-manager install bridge', binary: 'bcs-bridge' };
  render(<QwenWorkRegistrationPanel open />);
  expect(screen.queryByRole('button', { name: '复制安装命令' })).not.toBeInTheDocument();
  expect(screen.getByText(/deployment-package-manager install bridge/)).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '下载安装脚本' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '查看其他发布版本' })).not.toBeInTheDocument();
  expect(screen.queryByText(/0.1.4-beta.2 已包含 init/)).not.toBeInTheDocument();

  expect(screen.getByText(/bcs-bridge --config.*register/)).toBeInTheDocument();
  expect(screen.getByText(/if bcs-bridge init --help/)).toBeInTheDocument();
  expect(screen.getByText(/bcs-bridge --config.*start --daemon/)).toBeInTheDocument();
  expect(screen.getByText('bcs-bridge stop')).toBeInTheDocument();
  expect(screen.getByText(/command -v bcs-bridge/)).toBeInTheDocument();
  mockInstallation = null;
});


it.each([['LOCAL', 'local'], ['PRE', 'pre'], ['PROD', 'prod']])(
  'uses the %s environment profile without showing the target information card', (environment, profile) => {
    mockEnvironment = environment;
    mockInstallation = { installCommand: 'package-manager install bridge', binary: 'bcs-bridge', profiles: true };
    render(<QwenWorkRegistrationPanel open />);
    expect(screen.queryByRole('region', { name: '接入目标环境' })).not.toBeInTheDocument();
    expect(screen.queryByText(/接入目标：|BCS 地址：/)).not.toBeInTheDocument();

    const commands = Array.from(document.querySelectorAll('pre')).map((node) => node.textContent || '');
    expect(commands.some((command) => command.includes(`--profile ${profile} register`))).toBe(true);
    expect(commands.some((command) => command.includes(`--profile ${profile} init --engine qwenwork`))).toBe(true);
    expect(commands.some((command) => command.includes(`bcs-bridge stop &&\nbcs-bridge --profile ${profile} start`))).toBe(true);
    expect(commands.some((command) => command.includes('--config'))).toBe(false);
    mockInstallation = null;
    mockEnvironment = 'PRE';
  },
);

it('validates the Bot name without requiring a project directory', () => {
  render(<QwenWorkRegistrationPanel open />);
  fireEvent.change(screen.getByLabelText('Bot 名称'), { target: { value: ' ' } });
  expect(screen.getByText('Bot 名称需要填写 2–64 个字符。')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '复制接入命令' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Bot 名称'), { target: { value: '测试千问' } });
  expect(screen.getByRole('button', { name: '复制接入命令' })).toBeEnabled();
});

it('replaces detailed instructions with the deployment-provided guide link', () => {
  mockInstallation = {
    installCommand: 'package-manager install bridge', binary: 'bcs-bridge', profiles: true,
    guideUrl: 'https://docs.example.com/qwenwork-onboarding',
  };
  render(<QwenWorkRegistrationPanel open />);
  expect(screen.getByRole('link', { name: '千问办公渠道配置指南' })).toHaveAttribute('href', mockInstallation.guideUrl);
  expect(screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)).toEqual([
    '1. 接入千问 Bot', '2. 配置千问办公渠道',
  ]);
  expect(screen.getByRole('link', { name: '千问办公渠道配置指南' }).closest('p')).toHaveTextContent(
    '执行接入命令后，请按照千问办公渠道配置指南在千问办公中配置并启用渠道，完成本步骤后才能连接 Bot。',
  );
  expect(screen.queryByText(/复制接入命令，在终端执行一次/)).not.toBeInTheDocument();
  expect(screen.queryByText(/填写命令输出的 WebSocket URL/)).not.toBeInTheDocument();
  expect(screen.queryByText('数据库无法自动识别时')).not.toBeInTheDocument();
  expect(screen.queryByText(/qwen_connected=true/)).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '完整接入指南与版本兼容说明' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '复制接入命令' })).toBeEnabled();
  mockInstallation = null;
});

it('uses the logged-in display name for internal onboarding and preserves manual edits', () => {
  mockInstallation = { installCommand: 'package-manager install bridge', binary: 'bcs-bridge', personalizeBotName: true };
  const { rerender } = render(<QwenWorkRegistrationPanel open />);
  expect(screen.getByLabelText('Bot 名称')).toHaveValue('我的千问办公');
  mockDisplayName = '卓人';
  rerender(<QwenWorkRegistrationPanel open />);
  expect(screen.getByLabelText('Bot 名称')).toHaveValue('卓人的千问办公');
  expect(screen.getByText(/--bot-name.*卓人的千问办公/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Bot 名称'), { target: { value: '自定义千问' } });
  mockDisplayName = '另一个昵称';
  rerender(<QwenWorkRegistrationPanel open />);
  expect(screen.getByLabelText('Bot 名称')).toHaveValue('自定义千问');
  mockInstallation = null;
  mockDisplayName = null;
});

it('keeps the public default Bot name even when a user identity is available', () => {
  mockDisplayName = '卓人';
  render(<QwenWorkRegistrationPanel open />);
  expect(screen.getByLabelText('Bot 名称')).toHaveValue('我的千问办公');
  mockDisplayName = null;
});

it('uses a deployment-owned onboarding command when provided', () => {
  const buildOnboardingCommand = jest.fn(() => 'bash verified-onboarding-script --engine qwenwork');
  mockInstallation = { installCommand: 'package-manager install bridge', binary: 'bcs-bridge', profiles: true, buildOnboardingCommand };
  try {
    render(<QwenWorkRegistrationPanel open />);
    expect(screen.getByText(/bash verified-onboarding-script/)).toBeInTheDocument();
    expect(buildOnboardingCommand).toHaveBeenCalledWith(expect.objectContaining({ token: 'test-token', environment: 'PRE', profile: 'pre', apiUrl: 'https://bcs.example.com' }));
    expect(screen.queryByText(/--profile pre register/)).not.toBeInTheDocument();
  } finally { mockInstallation = null; }
});
