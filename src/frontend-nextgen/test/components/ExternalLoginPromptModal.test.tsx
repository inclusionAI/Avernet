/** @jest-environment jsdom */
import { ExternalLoginPromptModal } from '@/components/ExternalLoginPromptModal';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { navigateToUrl } from '@/utils/redirectCurrentTab';
import { act, fireEvent, render, screen } from '@testing-library/react';

// useExternalAuth → authApiController(umi request) + notify + navigateToUrl;均 mock 掉。
jest.mock('@umijs/max', () => ({ request: jest.fn() }));
jest.mock('@/components/ui/notify');
jest.mock('@/utils/redirectCurrentTab');

import { request } from '@umijs/max';

const mockedRequest = request as jest.Mock;
const mockedNavigateToUrl = navigateToUrl as jest.MockedFunction<typeof navigateToUrl>;

beforeEach(() => {
  useLoginRedirectStore.getState().reset();
  mockedRequest.mockReset();
  mockedNavigateToUrl.mockClear();
});

afterEach(() => {
  useLoginRedirectStore.getState().reset();
  useExternalAuthStore.getState().reset();
});

describe('ExternalLoginPromptModal', () => {
  it('prompt 信号 → 弹窗显示标题与「立即登录」', () => {
    useLoginRedirectStore.getState().requestPrompt();
    render(<ExternalLoginPromptModal />);
    expect(screen.getByRole('heading', { name: '登录后继续使用 TeamClaw' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '立即登录' })).toBeTruthy();
  });

  it('无 prompt 信号 → 不弹', () => {
    render(<ExternalLoginPromptModal />);
    expect(screen.queryByRole('heading', { name: '登录后继续使用 TeamClaw' })).toBeNull();
  });

  it('点击「立即登录」→ login() 拉 /auth/url 并 navigateToUrl(providerUrl)', async () => {
    mockedRequest.mockResolvedValue({ providers: [{ name: 'alipay', url: 'https://login.example/a' }] });
    useLoginRedirectStore.getState().requestPrompt();
    render(<ExternalLoginPromptModal />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '立即登录' }));
    });

    expect(mockedRequest).toHaveBeenCalledWith('/auth/url', expect.objectContaining({ method: 'GET' }));
    expect(mockedNavigateToUrl).toHaveBeenCalledWith('https://login.example/a');
  });

  it('点击「稍后再说」→ 关闭弹窗', () => {
    useLoginRedirectStore.getState().requestPrompt();
    render(<ExternalLoginPromptModal />);
    fireEvent.click(screen.getByRole('button', { name: '稍后再说' }));
    expect(screen.queryByRole('heading', { name: '登录后继续使用 TeamClaw' })).toBeNull();
  });
});
