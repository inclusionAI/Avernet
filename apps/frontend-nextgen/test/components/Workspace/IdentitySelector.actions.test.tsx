/** @jest-environment jsdom */
/**
 * IdentitySelector 测试拆分（actions 分册）：能力入口、接入外部 Bot 弹窗与 Popover 交互类用例。
 *
 * 拆分背景与口径见 IdentitySelector.display.test.tsx 头注。
 */
import { extendCapabilities } from '@/capabilities';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { BotRegistrationDialog } from '@/components/Workspace/IdentitySelector/BotRegistrationDialog';
import { botRegistrationService, resolveBcsEndpoint } from '@/services/workspace/botRegistrationService';
import type { Identity } from '@/services/workspace/workspaceModel';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// 原文件口径：jsdom 下 Radix 异步渲染开销大，保留 60s 上限（仅本文件生效）。
jest.setTimeout(60000);

jest.mock('@/services/workspace/botRegistrationService');

const getRegistrationTokenMock = botRegistrationService.getRegistrationToken as jest.MockedFunction<
  typeof botRegistrationService.getRegistrationToken
>;
const resolveBcsEndpointMock = resolveBcsEndpoint as jest.MockedFunction<typeof resolveBcsEndpoint>;

const identities: Identity[] = [
  {
    id: 'human_900004',
    name: '示例用户',
    kind: 'user',
    avatar: '风',
  },
  {
    id: 'bot-online',
    name: '协作 Bot',
    kind: 'bot',
    avatar: 'B',
    engine: 'OpenClaw',
    botType: 'personal',
    status: 'available',
    chatStatus: 'online',
    reachability: 'reachable',
  },
  {
    id: 'bot-hidden',
    name: '隐藏 Bot',
    kind: 'bot',
    avatar: 'H',
    engine: 'ClaudeCode',
    botType: 'service',
    status: 'unavailable',
    chatStatus: 'hidden',
    reachability: 'unreachable',
  },
];

describe('WorkspaceIdentitySelector 能力入口与 Popover 交互', () => {
  beforeEach(() => {
    extendCapabilities({
      getBotRegistrationEnabled: () => ({ status: 'available', value: true }),
    });
    getRegistrationTokenMock.mockReset();
    resolveBcsEndpointMock.mockReset();
    resolveBcsEndpointMock.mockReturnValue('http://127.0.0.1:21000');
  });

  it('身份下拉菜单不再提供协作权限入口（collab-permission-entry-migration：入口随旧页面退役）', async () => {
    render(<WorkspaceIdentitySelector identities={identities} activeId="human_900004" onChange={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：示例用户' }));
    await screen.findByText('切换工作身份');
    expect(screen.queryByRole('button', { name: /进入协作权限/ })).not.toBeInTheDocument();
  });

  it('身份列表为空时不渲染身份下拉菜单或协作权限入口', () => {
    render(<WorkspaceIdentitySelector identities={[]} activeId={null} onChange={() => {}} />);

    expect(screen.queryByRole('button', { name: /进入协作权限/ })).not.toBeInTheDocument();
    expect(screen.queryByText('切换工作身份')).not.toBeInTheDocument();
  });

  it('接入能力开启时身份选择器也不展示接入外部 Bot 入口', async () => {
    render(<WorkspaceIdentitySelector identities={identities} activeId="human_900004" onChange={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：示例用户' }));
    await screen.findByText('隐藏 Bot');

    expect(screen.queryByRole('button', { name: '接入外部 Bot' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('接入外部 Bot 说明')).not.toBeInTheDocument();
    expect(getRegistrationTokenMock).not.toHaveBeenCalled();
  });

  it('外部形态按需展示两种接入命令并复制真实 Token', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    getRegistrationTokenMock.mockResolvedValue({
      ok: true,
      data: {
        token: 'token-1',
        expiresAt: 1788272686000,
        note: 'Use this token for bot registration within 6 hours',
      },
    });
    render(<BotRegistrationDialog open onClose={() => {}} />);

    await waitFor(() => expect(getRegistrationTokenMock).toHaveBeenCalledTimes(1));
    const dialog = await screen.findByRole('dialog');
    expect(screen.queryByText('Bot 接入')).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent('接入外部 Bot');
    expect(dialog).toHaveTextContent('选择接入方式，复制命令后在对应环境中执行，即可完成外部 Bot 的初始化接入。');
    expect(dialog).toHaveTextContent('用户自助接入');
    expect(dialog).toHaveTextContent('install.sh --token token-1 --bcs-endpoint http://127.0.0.1:21000');
    expect(dialog).not.toHaveTextContent('Use this token for bot registration within 6 hours');
    expect(dialog).toHaveTextContent(/Token 有效期至：/);

    fireEvent.click(screen.getByRole('button', { name: 'Bot 自动接入' }));
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('your TOKEN is token-1'));
    fireEvent.click(screen.getByRole('button', { name: /复制/ }));

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        'Follow the instructions in https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/crates/plugins/openclaw-channel-bcn/install-instructions/install.md to join BCN (Bot Coordination Network), your TOKEN is token-1 and BCS_ENDPOINT is http://127.0.0.1:21000.',
      ),
    );
    expect(getRegistrationTokenMock).toHaveBeenCalledTimes(1);
  });

  it('打开入口和点击当前身份不会切换，点击其他身份后关闭 Popover', async () => {
    const onChange = jest.fn();
    render(<WorkspaceIdentitySelector identities={identities} activeId="human_900004" onChange={onChange} />);

    const trigger = screen.getByRole('button', { name: '当前协作身份：示例用户' });
    fireEvent.click(trigger);
    const currentOption = await screen.findByRole('button', { name: /示例用户 用户/ });
    fireEvent.click(currentOption);
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /协作 Bot/ }));
    expect(onChange).toHaveBeenCalledWith('bot-online');
    await waitFor(() => {
      expect(screen.queryByText('切换工作身份')).not.toBeInTheDocument();
    });
  });
});
