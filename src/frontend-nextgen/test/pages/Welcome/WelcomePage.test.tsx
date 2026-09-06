/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import Welcome from '@/pages/Welcome';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { navigateToUrl } from '@/utils/redirectCurrentTab';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen } from '@testing-library/react';

// 欢迎页依赖面:@umijs/max(history.push 导航 + request 兜底)、authApiController(探活/登录地址)、
// notify、redirectCurrentTab;均 mock。链路模式与 test/components/ExternalLoginPromptModal.test.tsx 一致。
jest.mock('@umijs/max', () => ({ request: jest.fn(), history: { push: jest.fn() } }));
jest.mock('@/services/auth/authApiController');
jest.mock('@/components/ui/notify');
jest.mock('@/utils/redirectCurrentTab');

import { getAuthProviders, getCurrentAuthUser } from '@/services/auth/authApiController';
import { history } from '@umijs/max';

const mockedHistoryPush = history.push as jest.Mock;
const mockedGetCurrentAuthUser = getCurrentAuthUser as jest.Mock;
const mockedGetAuthProviders = getAuthProviders as jest.Mock;
const mockedNavigateToUrl = navigateToUrl as jest.MockedFunction<typeof navigateToUrl>;

const AUTH_USER_DTO = { user_id: 'u-1', name: '外部用户', provider: 'sso' };

beforeEach(() => {
  jest.clearAllMocks();
  useExternalAuthStore.getState().reset();
});

afterEach(() => {
  useExternalAuthStore.getState().reset();
});

describe('Welcome 欢迎页(Open 形态默认入口)', () => {
  it('体验提示与 WelcomeHeader 组成统一 sticky 顶部区域', async () => {
    mockedGetCurrentAuthUser.mockRejectedValueOnce({ response: { status: 401 } });
    await act(async () => {
      render(<Welcome />);
    });

    const notice = screen.getByRole('status', { name: '开源体验环境提示' });
    const header = document.querySelector('header');
    expect(header).not.toBeNull();
    expect(notice.compareDocumentPosition(header!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(notice.parentElement).toHaveClass('sticky', 'top-0', 'z-40');
    expect(header).not.toHaveClass('sticky');
  });

  it('Hero 渲染品牌名大标题与 tagline(产品名经 getProductBrand 插值)', async () => {
    // 挂载探活 401 → 稳定保持未登录态;render flush 进 act 以消化探活的异步 setState
    mockedGetCurrentAuthUser.mockRejectedValueOnce({ response: { status: 401 } });
    await act(async () => {
      render(<Welcome />);
    });
    expect(screen.getByRole('heading', { name: 'Avernet 组织级多智能体协作平台' })).toBeTruthy();
    expect(screen.getByText('让智能体像组织一样，在此协同、执行、持续进化。')).toBeTruthy();
  });

  it('CTA「进入 Avernet」点击 → history.push(/workspace),登录态交给既有登录链路', async () => {
    mockedGetCurrentAuthUser.mockRejectedValueOnce({ response: { status: 401 } });
    await act(async () => {
      render(<Welcome />);
    });
    fireEvent.click(screen.getByRole('button', { name: '进入 Avernet' }));
    expect(mockedHistoryPush).toHaveBeenCalledWith('/workspace');
  });

  it('GitHub 外链 Hero 与 Footer 两处均为开源仓地址 + target=_blank', async () => {
    mockedGetCurrentAuthUser.mockRejectedValueOnce({ response: { status: 401 } });
    await act(async () => {
      render(<Welcome />);
    });
    const links = screen.getAllByRole('link', { name: 'GitHub' });
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link.getAttribute('href')).toBe('https://github.com/inclusionAI/Avernet');
      expect(link.getAttribute('target')).toBe('_blank');
    }
  });

  it('ScenariosSection 渲染场景标题与截图资源(src/assets/Images/scenarios,四卡六图)', async () => {
    mockedGetCurrentAuthUser.mockRejectedValueOnce({ response: { status: 401 } });
    await act(async () => {
      render(<Welcome />);
    });
    expect(screen.getByRole('heading', { name: '协作是如何发生的' })).toBeTruthy();
    for (const title of ['Bot 发现', 'Bot 协作', '多种协作模式', 'Human 参与']) {
      expect(screen.getByRole('heading', { name: title })).toBeTruthy();
    }
    // 场景截图经 webpack asset import,jest 桩把所有 .png 的 src 统一成 'test-file-stub'
    // (与 Header/Footer 的 brand.Logo 同桩),故按 alt 而非 src 路径区分六张场景图。
    const SCENARIO_ALTS = [
      'Bot 发现示例',
      'Bot 协作示例',
      '协作模式示例',
      '自定义协作示例',
      'Human 参与示例一',
      'Human 参与示例二',
    ];
    const scenarioImages = screen
      .getAllByRole('img')
      .filter((image) => SCENARIO_ALTS.includes(image.getAttribute('alt') ?? ''));
    expect(scenarioImages).toHaveLength(6);
    for (const image of scenarioImages) {
      expect(image.getAttribute('src')).toBeTruthy();
    }
  });

  it('oauth-provider 未登录:Header 渲染「登录」,点击拉 provider url 并发起登录', async () => {
    mockedGetCurrentAuthUser.mockRejectedValueOnce({ response: { status: 401 } });
    mockedGetAuthProviders.mockResolvedValueOnce({
      providers: [{ name: 'sso', url: 'https://login.example/sso' }],
    });
    await act(async () => {
      render(<Welcome />);
    });
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '登录' }));
    });

    expect(mockedGetAuthProviders).toHaveBeenCalled();
    expect(mockedNavigateToUrl).toHaveBeenCalledWith('https://login.example/sso');
  });

  it('oauth-provider 已登录:隐藏「登录」按钮(一期不展示用户信息)', async () => {
    useExternalAuthStore.getState().setAuthenticated({ userId: 'u-1', displayName: '外部用户', provider: 'sso' });
    mockedGetCurrentAuthUser.mockResolvedValue(AUTH_USER_DTO);
    await act(async () => {
      render(<Welcome />);
    });
    expect(screen.queryByRole('button', { name: '登录' })).toBeNull();
  });
});

describe('非 oauth-provider 策略(纵深防御)', () => {
  it('ace-gateway 策略不渲染登录按钮', async () => {
    // extendCapabilities 合并后无法恢复,故本组置于文件末尾,不影响前述 oauth-provider 用例。
    // 探活仅在 oauth-provider 挂载,因此无需 mock getCurrentAuthUser。
    extendCapabilities({ getLoginStrategy: () => ({ status: 'available', value: 'ace-gateway' }) });
    useExternalAuthStore.getState().setUnauthenticated();
    await act(async () => {
      render(<Welcome />);
    });
    expect(screen.queryByRole('button', { name: '登录' })).toBeNull();
  });
});
