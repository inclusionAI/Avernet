/** @jest-environment jsdom */
import { QwenWorkRegistrationPanel } from '@/components/Workspace/IdentitySelector/QwenWorkRegistrationPanel';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
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
    'https://github.com/inclusionAI/Avernet/releases/download/bcs-bridge-v0.1.4-beta.1/bcs-bridge-installer.sh',
  );
  expect(screen.getByRole('button', { name: '复制下载命令' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '复制校验与安装命令' })).toBeEnabled();
  expect(screen.queryByText('正在查询最新 Bridge…')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('项目目录（绝对路径或 ~/ 开头）'), { target: { value: '~/project' } });
  expect(screen.getByRole('button', { name: '复制注册命令' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '复制启动命令' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '复制初始化命令' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '复制Bridge 配置模板' })).not.toBeInTheDocument();
  expect(screen.getByText(/0.1.4-beta.1 尚不支持/)).toBeInTheDocument();
});
