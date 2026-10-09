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
    expect(screen.getByRole('heading', { name: '登录后继续使用 Avernet' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '立即登录' })).toBeTruthy();
  });

  it('无 prompt 信号 → 不弹', () => {
    render(<ExternalLoginPromptModal />);
    expect(screen.queryByRole('heading', { name: '登录后继续使用 Avernet' })).toBeNull();
  });

  it('点击「立即登录」→ login() 拉 /openapi/v1/auth/url 并 navigateToUrl(providerUrl)', async () => {
    mockedRequest.mockResolvedValue({ providers: [{ name: 'alipay', url: 'https://login.example/a' }] });
    useLoginRedirectStore.getState().requestPrompt();
    render(<ExternalLoginPromptModal />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '立即登录' }));
    });

    expect(mockedRequest).toHaveBeenCalledWith('/openapi/v1/auth/url', expect.objectContaining({ method: 'GET' }));
    expect(mockedNavigateToUrl).toHaveBeenCalledWith('https://login.example/a');
  });

  // 不可关闭(add-external-oauth-login 8.8,spec「未登录时以不可关闭提示弹窗处置」):
  // 无关闭按钮、无「稍后再说」类退出路径,「立即登录」是唯一出路。
  it('弹窗不可关闭:无关闭按钮与退出按钮,唯一出路是「立即登录」', () => {
    useLoginRedirectStore.getState().requestPrompt();
    render(<ExternalLoginPromptModal />);
    expect(screen.queryByRole('button', { name: '稍后再说' })).toBeNull();
    expect(screen.queryByLabelText('关闭弹窗')).toBeNull();
    expect(screen.getByRole('button', { name: '立即登录' })).toBeTruthy();
  });

  it('ESC 关闭意图不生效(弹窗保持打开)', () => {
    useLoginRedirectStore.getState().requestPrompt();
    render(<ExternalLoginPromptModal />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('heading', { name: '登录后继续使用 Avernet' })).toBeTruthy();
  });

  it('遮罩/外部点击关闭意图不生效(弹窗保持打开)', () => {
    useLoginRedirectStore.getState().requestPrompt();
    render(<ExternalLoginPromptModal />);
    fireEvent.pointerDown(document.body);
    fireEvent.click(document.body);
    expect(screen.getByRole('heading', { name: '登录后继续使用 Avernet' })).toBeTruthy();
  });
});
