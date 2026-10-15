/** @jest-environment jsdom */
/**
 * BotRegistrationDialog Poolab Tab 门控用例：
 * - getPoolabBridgeInstallation() 返回 null（Open Core 默认）→ 不渲染 Poolab Tab；
 * - 返回 {oneClickScriptUrl, guideUrl} → 渲染 Poolab Tab，且可切换；
 * - 仅 Poolab Tab 激活时才渲染 PoolabRegistrationPanel。
 */
import { extendCapabilities } from '@/capabilities';
import { BotRegistrationDialog } from '@/components/Workspace/IdentitySelector/BotRegistrationDialog';
import { botRegistrationService, resolveBcsEndpoint } from '@/services/workspace/botRegistrationService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/services/workspace/botRegistrationService');
jest.mock('@/pages/Workspace/hooks/useBotRegistrationToken', () => ({
  useBotRegistrationToken: jest.fn(() => ({
    token: null,
    expiresAt: null,
    isLoading: false,
    error: null,
    bcsEndpoint: null,
    retry: jest.fn(),
  })),
}));
jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({ identity: null, status: 'ready' }),
}));

const getRegistrationTokenMock = botRegistrationService.getRegistrationToken as jest.MockedFunction<
  typeof botRegistrationService.getRegistrationToken
>;
const resolveBcsEndpointMock = resolveBcsEndpoint as jest.MockedFunction<typeof resolveBcsEndpoint>;

describe('BotRegistrationDialog Poolab Tab 门控', () => {
  beforeEach(() => {
    getRegistrationTokenMock.mockReset();
    resolveBcsEndpointMock.mockReset();
    resolveBcsEndpointMock.mockReturnValue('https://bcs.example.com');
  });

  it('Open Core 默认（installation=null）不渲染 Poolab Tab', () => {
    extendCapabilities({
      getPoolabBridgeInstallation: () => ({ status: 'available', value: null }),
      getBotRegistrationEnabled: () => ({ status: 'available', value: true }),
    });
    render(<BotRegistrationDialog open onClose={() => {}} />);

    expect(screen.getByRole('button', { name: 'OpenClaw' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '千问办公' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Poolab' })).not.toBeInTheDocument();
  });

  it('installation 非 null 时显示 Poolab Tab，点击后渲染 PoolabRegistrationPanel', () => {
    extendCapabilities({
      getPoolabBridgeInstallation: () => ({
        status: 'available',
        value: {
          oneClickScriptUrl: 'http://oss.example.com/poolab-onboard.sh',
          guideUrl: 'https://yuque.example.com/poolab',
        },
      }),
      getBotRegistrationEnabled: () => ({ status: 'available', value: true }),
      getRuntimeEnvironment: () => ({ status: 'available', value: 'PRE' }),
    });
    render(<BotRegistrationDialog open onClose={() => {}} />);

    expect(screen.getByRole('button', { name: 'Poolab' })).toBeInTheDocument();
    // 默认仍是 OpenClaw Tab
    expect(screen.getByText(/如何接入 OpenClaw/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Poolab' }));
    // PoolabRegistrationPanel 标记文本
    expect(screen.getByText('1. 安装 bcs-bridge')).toBeInTheDocument();
    expect(screen.getByLabelText('Bot 名称')).toBeInTheDocument();
  });

  it('Poolab Tab 不影响 OpenClaw / 千问办公 Tab 内容切换', () => {
    extendCapabilities({
      getPoolabBridgeInstallation: () => ({
        status: 'available',
        value: {
          oneClickScriptUrl: 'http://oss.example.com/poolab-onboard.sh',
          guideUrl: 'https://yuque.example.com/poolab',
        },
      }),
      getBotRegistrationEnabled: () => ({ status: 'available', value: true }),
      getRuntimeEnvironment: () => ({ status: 'available', value: 'PRE' }),
    });
    render(<BotRegistrationDialog open onClose={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: 'Poolab' }));
    expect(screen.getByText('1. 安装 bcs-bridge')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'OpenClaw' }));
    expect(screen.getByText(/如何接入 OpenClaw/)).toBeInTheDocument();
    expect(screen.queryByText('1. 安装 bcs-bridge')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '千问办公' }));
    // QwenWorkRegistrationPanel 存在：渲染「接入千问 Bot」分组标题
    expect(screen.getByText(/接入千问 Bot/)).toBeInTheDocument();
  });
});
