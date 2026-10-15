/** @jest-environment jsdom */
import { PoolabRegistrationPanel } from '@/components/Workspace/IdentitySelector/PoolabRegistrationPanel';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

let mockEnvironment: 'PRE' | 'PROD' | 'LOCAL' | 'DEV' = 'PRE';
let mockDisplayName: string | null = null;
let mockInstallation: { oneClickScriptUrl: string; guideUrl: string } | null = {
  oneClickScriptUrl: 'http://antsys.example.com/bcn/dist/poolab-onboard.sh',
  guideUrl: 'https://yuque.example.com/poolab-guide',
};

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({ identity: mockDisplayName ? { displayName: mockDisplayName } : null }),
}));

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getRuntimeEnvironment: () => ({ status: 'available', value: mockEnvironment }),
    getPoolabBridgeInstallation: () => ({ status: 'available', value: mockInstallation }),
  }),
}));

let mockToken: string | null = 'test-token';
let mockTokenError: string | null = null;
let mockTokenLoading = false;

jest.mock('@/pages/Workspace/hooks/useBotRegistrationToken', () => ({
  useBotRegistrationToken: jest.fn(() => ({
    token: mockToken,
    expiresAt: null,
    isLoading: mockTokenLoading,
    error: mockTokenError,
    bcsEndpoint: 'https://bcs.example.com',
    retry: jest.fn(),
  })),
}));

import { useBotRegistrationToken } from '@/pages/Workspace/hooks/useBotRegistrationToken';

beforeEach(() => {
  mockEnvironment = 'PRE';
  mockDisplayName = null;
  mockInstallation = {
    oneClickScriptUrl: 'http://antsys.example.com/bcn/dist/poolab-onboard.sh',
    guideUrl: 'https://yuque.example.com/poolab-guide',
  };
  mockToken = 'test-token';
  mockTokenError = null;
  mockTokenLoading = false;
  jest.clearAllMocks();
});

describe('PoolabRegistrationPanel 渲染门控', () => {
  it('installation 为 null 时不渲染任何内容', () => {
    mockInstallation = null;
    const { container } = render(<PoolabRegistrationPanel open />);
    expect(container.firstChild).toBeNull();
  });

  it('installation 非 null 时正常渲染安装步骤与 Bot 名称输入', () => {
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByText('1. 安装 bcs-bridge')).toBeInTheDocument();
    expect(screen.getByLabelText('Bot 名称')).toBeInTheDocument();
  });
});

describe('PoolabRegistrationPanel 环境感知', () => {
  it('PRE 环境启用完整 UI，一键命令带 SERVER_ENV=pre 前缀', () => {
    mockEnvironment = 'PRE';
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByText('一键接入（推荐）')).toBeInTheDocument();
    expect(screen.getByText(/SERVER_ENV=pre bash -c/)).toBeInTheDocument();
  });

  it('PROD 环境启用完整 UI，一键命令不带 SERVER_ENV 前缀', () => {
    mockEnvironment = 'PROD';
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByText('一键接入（推荐）')).toBeInTheDocument();
    expect(screen.queryByText(/SERVER_ENV=pre bash -c/)).not.toBeInTheDocument();
    expect(screen.getByText(/^bash -c/)).toBeInTheDocument();
  });

  it('LOCAL 环境显示禁用提示，不展示一键/手动 Segmented', () => {
    mockEnvironment = 'LOCAL';
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByRole('alert')).toHaveTextContent('当前部署环境不支持 Poolab 自助接入');
    expect(screen.queryByText('一键接入（推荐）')).not.toBeInTheDocument();
    expect(screen.queryByText('手动接入')).not.toBeInTheDocument();
  });

  it('DEV 环境同样禁用', () => {
    mockEnvironment = 'DEV';
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByRole('alert')).toHaveTextContent('当前部署环境不支持 Poolab 自助接入');
  });
});

describe('PoolabRegistrationPanel token 拉取', () => {
  it('LOCAL/DEV 环境不发起 token 请求', () => {
    mockEnvironment = 'LOCAL';
    render(<PoolabRegistrationPanel open />);
    expect(useBotRegistrationToken as jest.Mock).toHaveBeenCalledWith(false, 'poolab');
  });

  it('PRE/PROD 环境发起 poolab slug 的 token 请求', () => {
    mockEnvironment = 'PRE';
    render(<PoolabRegistrationPanel open />);
    expect(useBotRegistrationToken as jest.Mock).toHaveBeenCalledWith(true, 'poolab');
  });

  it('token 拉取失败时展示 inline 重试按钮', () => {
    mockToken = null;
    mockTokenError = 'BCN 域名不可达';
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByText('BCN 域名不可达')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重新获取接入 Token/ })).toBeInTheDocument();
  });

  it('token 加载中显示骨架屏', () => {
    mockToken = null;
    mockTokenLoading = true;
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});

describe('PoolabRegistrationPanel Bot 名称', () => {
  it('未登录时默认 我的Poolab', () => {
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByLabelText('Bot 名称')).toHaveValue('我的Poolab');
  });

  it('登录后默认 <displayName>的Poolab', () => {
    mockDisplayName = '卓人';
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByLabelText('Bot 名称')).toHaveValue('卓人的Poolab');
  });

  it('displayName 过长时截取前 57 字符补「的Poolab」，拼接后仍满足 ≤64 字符约束', () => {
    mockDisplayName = 'x'.repeat(80);
    render(<PoolabRegistrationPanel open />);
    const input = screen.getByLabelText('Bot 名称') as HTMLInputElement;
    expect(input.value).toBe(`${'x'.repeat(57)}的Poolab`);
    expect(input.value.length).toBeLessThanOrEqual(64);
  });

  it('编辑名称后使用新值，且不再被 displayName 覆盖', () => {
    mockDisplayName = '卓人';
    const { rerender } = render(<PoolabRegistrationPanel open />);
    fireEvent.change(screen.getByLabelText('Bot 名称'), { target: { value: '我的自定义Poolab' } });
    mockDisplayName = '另一个昵称';
    rerender(<PoolabRegistrationPanel open />);
    expect(screen.getByLabelText('Bot 名称')).toHaveValue('我的自定义Poolab');
  });

  it('显示名称过长时给出错误提示且不渲染命令', () => {
    render(<PoolabRegistrationPanel open />);
    fireEvent.change(screen.getByLabelText('Bot 名称'), { target: { value: 'a'.repeat(65) } });
    expect(screen.getByText(/Bot 名称需要填写/)).toBeInTheDocument();
    expect(screen.queryByText('一键接入（推荐）')).not.toBeInTheDocument();
  });

  it('显示名称过短时给出错误提示', () => {
    render(<PoolabRegistrationPanel open />);
    fireEvent.change(screen.getByLabelText('Bot 名称'), { target: { value: ' ' } });
    expect(screen.getByText(/Bot 名称需要填写/)).toBeInTheDocument();
  });
});

describe('PoolabRegistrationPanel 模式切换', () => {
  it('一键接入模式默认渲染一键命令', () => {
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByRole('button', { name: '复制一键接入命令' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '复制注册命令' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '复制启动命令' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '复制状态命令' })).not.toBeInTheDocument();
  });

  it('切到手动接入后展示三个独立命令块', () => {
    render(<PoolabRegistrationPanel open />);
    fireEvent.click(screen.getByText('手动接入'));
    expect(screen.getByRole('button', { name: '复制注册命令' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制启动命令' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制状态命令' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '复制一键接入命令' })).not.toBeInTheDocument();
  });

  it('手动模式 PRE 环境注册命令带前缀，启动/状态命令不带前缀', () => {
    mockEnvironment = 'PRE';
    render(<PoolabRegistrationPanel open />);
    fireEvent.click(screen.getByText('手动接入'));
    expect(screen.getByText(/SERVER_ENV=pre bcs-bridge register/)).toBeInTheDocument();
    expect(screen.getByText('bcs-bridge start --daemon')).toBeInTheDocument();
    expect(screen.getByText('bcs-bridge status')).toBeInTheDocument();
  });

  it('手动模式 PROD 环境注册命令不带前缀', () => {
    mockEnvironment = 'PROD';
    render(<PoolabRegistrationPanel open />);
    fireEvent.click(screen.getByText('手动接入'));
    expect(screen.getByText(/^bcs-bridge register/)).toBeInTheDocument();
    expect(screen.queryByText(/SERVER_ENV=pre/)).not.toBeInTheDocument();
  });

  it('切换模式不重新拉取 token：仅以相同入参调用 hook，不会重复请求后端（hook 内部起依赖不会重复发起请求）', () => {
    render(<PoolabRegistrationPanel open />);
    fireEvent.click(screen.getByText('手动接入'));
    fireEvent.click(screen.getByText('一键接入（推荐）'));
    // hook 会被重新调用（随渲染），但所有调用的入参都保持 (true, 'poolab')，不会引发重复请求
    const calls = (useBotRegistrationToken as jest.Mock).mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    calls.forEach(([calledOpen, calledSlug]) => {
      expect(calledOpen).toBe(true);
      expect(calledSlug).toBe('poolab');
    });
  });

  it('弹窗关闭重新打开后回到一键接入模式', () => {
    const { rerender } = render(<PoolabRegistrationPanel open />);
    fireEvent.click(screen.getByText('手动接入'));
    expect(screen.getByRole('button', { name: '复制注册命令' })).toBeInTheDocument();
    rerender(<PoolabRegistrationPanel open={false} />);
    rerender(<PoolabRegistrationPanel open />);
    expect(screen.getByRole('button', { name: '复制一键接入命令' })).toBeInTheDocument();
  });
});

describe('PoolabRegistrationPanel 命令内容', () => {
  it('一键命令中包含 OSS 脚本 URL 与 poolab-onboard.sh', () => {
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByText(new RegExp(mockInstallation!.oneClickScriptUrl.replace(/[.:/]/g, '\\$&')))).toBeInTheDocument();
    expect(screen.getByText(/poolab-onboard\.sh/)).toBeInTheDocument();
  });

  it('一键命令中包含 --token 和 --name 参数', () => {
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByText(/--token/)).toBeInTheDocument();
    expect(screen.getByText(/--name/)).toBeInTheDocument();
  });

  it('PRE 一键命令格式：SERVER_ENV=pre bash -c "$(curl -fsSL ... || echo \'exit 1\')" poolab-onboard.sh ...', () => {
    mockEnvironment = 'PRE';
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByText(/^SERVER_ENV=pre bash -c "\$\(curl -fsSL /)).toBeInTheDocument();
    expect(screen.getByText(/\|\| echo 'exit 1'/)).toBeInTheDocument();
  });

  it('PROD 一键命令格式：bash -c "$(curl -fsSL ... || echo \'exit 1\')" poolab-onboard.sh ...（无前缀）', () => {
    mockEnvironment = 'PROD';
    render(<PoolabRegistrationPanel open />);
    expect(screen.getByText(/^bash -c "\$\(curl -fsSL /)).toBeInTheDocument();
  });
});

describe('PoolabRegistrationPanel 文档指南', () => {
  it('展示内部语雀指南链接', () => {
    render(<PoolabRegistrationPanel open />);
    const link = screen.getByRole('link', { name: /Poolab 接入 BCN 指南/ });
    expect(link).toHaveAttribute('href', mockInstallation!.guideUrl);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });

  it('LOCAL/DEV 环境隐藏文档指南（避免误导）', () => {
    mockEnvironment = 'LOCAL';
    render(<PoolabRegistrationPanel open />);
    expect(screen.queryByRole('link', { name: /Poolab 接入 BCN 指南/ })).not.toBeInTheDocument();
  });
});
