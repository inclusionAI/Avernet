/** @jest-environment jsdom */
// BCS 独立好友发现的外壳冷启动集成（bcs-standalone-friend-discovery Task 2→3）。
// 从空 workspace Store 挂载真实 AppShell + 完整发现页（SquarePageShell resource="bot"
// 页头内含 SquareSearchBar 与 SquareIdentityPicker）+ 角色选择器，保留
// workspaceService.initWorkspace、useSpaceContext 与 capabilities 的真实实现，只在网络
// 出口（Channel B backendRequest）供给 OAuth 用户（store）、bots/mine、BCS 候选/搜索
// 与好友连接响应。BCS-only 冷启动、30 秒轮询窗口与 hidden→visible 期间完整页面
// 不得出现任何 Backend（/openapi/v1/bots*）请求。
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { AnchorHTMLAttributes, ReactNode } from 'react';

const mockLocation = { pathname: '/collaboration-square/bots' };
const mockHistoryPush = jest.fn();
const mockNavigate = jest.fn();

jest.mock('@umijs/max', () => ({
  history: { push: (...args: unknown[]) => mockHistoryPush(...args) },
  useLocation: () => mockLocation,
  useNavigate: () => mockNavigate,
  Link: ({ to, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string; children: ReactNode }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

// 只替换请求出口 backendRequest：身份（bots/mine、engine 补充）、空间初始化、未读轮询、
// BCS 候选列表/搜索与好友连接回填都经真实 Controller/Service 走到这里；
// 真实 BackendRequestError/useErrorNotifyStore 保留。
const mockBackendRequest = jest.fn<Promise<unknown>, [string, unknown]>();

jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: (...args: [string, unknown]) => mockBackendRequest(...args),
}));

const { AppShell } = require('@/shell/AppShell') as typeof import('@/shell/AppShell');
const { SquarePageShell } = require('@/components/CollaborationSquare/SquarePageShell') as typeof import(
  '@/components/CollaborationSquare/SquarePageShell'
);
type SquareIdentityPickerModule = typeof import('@/components/CollaborationSquare/SquareIdentityPicker');
const { SquareIdentityPicker } = require('@/components/CollaborationSquare/SquareIdentityPicker') as SquareIdentityPickerModule;

/** 开源形态（oauth-provider）登录供给：经 externalAuthStore 提供 OAuth 用户，不走网络。 */
const OAUTH_USER = {
  userId: '7',
  displayName: '验收用户七',
  provider: 'oauth-provider',
  avatarUrl: 'https://t.example/avatar-7.png',
};

/** bots/mine：真实 human_7 与一个受管物理 Bot（engine 由 mine 直接提供）。 */
const MINE_ENVELOPE = {
  code: 20000,
  message: 'ok',
  request_id: 'r-mine',
  data: {
    items: [
      { kind: 'human', bot_id: 'human_7', name: '验收用户七', avatar_url: null, status: 'online' },
      {
        kind: 'bot',
        bot_id: 'assistant:owner-7',
        name: '受管机器人',
        avatar_url: null,
        status: 'online',
        engine: 'openclaw',
      },
    ],
    total: 2,
    offset: 0,
    limit: 50,
  },
};

/** BCS /bots/{actor}/candidates：1 条 online 公开候选 + 1 条 hidden 候选（hidden 不额外排除）。 */
const CANDIDATES_ENVELOPE = {
  code: 20000,
  message: 'ok',
  request_id: 'r-candidates',
  data: {
    items: [
      {
        bot: {
          bot_id: 'planner:owner-8',
          name: '规划发现助手',
          avatar_url: null,
          created_by: '8',
          descriptor: { summary: '擅长任务规划与分解' },
          visibility: 'public',
          user_visibility: 'public',
          status: 'online',
        },
        is_friend: false,
      },
      {
        bot: {
          bot_id: 'secret:owner-8',
          name: '隐身发现候选',
          avatar_url: null,
          created_by: '8',
          descriptor: { summary: '已隐藏的候选' },
          visibility: 'public',
          user_visibility: 'public',
          status: 'hidden',
        },
        is_friend: false,
      },
    ],
    total: 2,
    offset: 0,
    limit: 100,
  },
};

/** BCS 好友连接申请（sent/pending）空页：发现列表回填“申请中”走 BCS，不走 Backend。 */
const EMPTY_FRIEND_CONNECTION_ENVELOPE = {
  code: 20000,
  message: 'ok',
  request_id: 'r-friend-requests',
  data: { items: [], total: 0 },
};

/** full 模式的外壳响应：Backend 身份补充、个人空间初始化、工单未读数。 */
const FULL_MODE_RESPONSES: Record<string, unknown> = {
  '/openapi/v1/bots': {
    code: 200000,
    message: 'ok',
    request_id: 'r-bots',
    data: {
      items: [{ bot_id: 'assistant:owner-7', engine: 'claude_code', bot_type: 'service' }],
      total: 1,
      offset: 0,
      limit: 100,
    },
  },
  '/openapi/v1/bots/spaces/personal/initialize': {
    code: 200000,
    message: 'ok',
    request_id: 'r-space',
    data: { space_id: 10000, space_name: '个人空间' },
  },
  '/openapi/v1/bots/work-order-notifications/unread-count': {
    code: 200000,
    message: 'ok',
    request_id: 'r-unread',
    data: { unread_count: 0, pending_approval_count: 0, unread_notice_count: 0, badge_count: 0 },
  },
};

/** BCS-only 允许的同源路径（发现页冷启动清单）：mine、公开候选、好友连接申请。 */
const BCS_ONLY_ALLOWED_PATHS = new Set([
  '/openapi/v1/collaboration/bots/mine',
  '/openapi/v1/collaboration/bots/human_7/candidates',
  '/openapi/v1/collaboration/bots/assistant%3Aowner-7/candidates',
  '/api/v1/collaboration/bots/human_7/candidates/search',
  '/api/v1/collaboration/bots/assistant%3Aowner-7/candidates/search',
  '/openapi/v1/collaboration/friend-connections/requests',
  '/openapi/v1/collaboration/friend-connections',
]);

/** 网络边界路由：BCS-only 之外的任何请求都直接失败，暴露违规冷启动行为。 */
function routeBackendRequests({ fullMode }: { fullMode: boolean }) {
  mockBackendRequest.mockImplementation(async (url: string) => {
    if (url === '/openapi/v1/collaboration/bots/mine') return MINE_ENVELOPE;
    const fullModeResponse = FULL_MODE_RESPONSES[url];
    if (fullMode && fullModeResponse) return fullModeResponse;
    if (BCS_ONLY_ALLOWED_PATHS.has(url)) {
      switch (url) {
        case '/api/v1/collaboration/bots/human_7/candidates/search':
        case '/api/v1/collaboration/bots/assistant%3Aowner-7/candidates/search':
          // Task 3 语义智能搜索（本冷启动用例不清空关键词，不会被调用；提供空页护航）。
          return { code: 20000, message: 'ok', request_id: 'r-search', data: { items: [], search_mode: 'empty_query' } };
        case '/openapi/v1/collaboration/friend-connections':
        case '/openapi/v1/collaboration/friend-connections/requests':
          return EMPTY_FRIEND_CONNECTION_ENVELOPE;
        default:
          return CANDIDATES_ENVELOPE;
      }
    }
    throw new Error(`当前冷启动配置不允许的 Backend 请求: ${url}`);
  });
}

function callCount(url: string, method: string): number {
  return mockBackendRequest.mock.calls.filter(
    ([calledUrl, options]) => calledUrl === url && (options as { method?: string }).method === method,
  ).length;
}

function unreadPullCount(): number {
  return callCount('/openapi/v1/bots/work-order-notifications/unread-count', 'GET');
}

/** 切换 document.hidden/visibilityState 并派发 visibilitychange（jsdom 需自有属性覆盖）。 */
function setDocumentVisibility(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { value: hidden, configurable: true });
  Object.defineProperty(document, 'visibilityState', { value: hidden ? 'hidden' : 'visible', configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

function restoreDocumentVisibility() {
  delete (document as { hidden?: boolean }).hidden;
  delete (document as { visibilityState?: string }).visibilityState;
}

/** flush 微任务链（mock 的请求立即 resolve，链路只有微任务跳数，无真实 IO）。 */
async function flushAsync(times = 12) {
  for (let index = 0; index < times; index++) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

/**
 * 推进 0ms 防抖 timer 并 flush 链路；身份落 store 会重跑列表 effect，需要两轮「tick + flush」
 * （首轮触发挂载期请求/门控，次轮接住 identities 更新后的重载 timer）。
 */
async function flushListLoad(times = 20) {
  for (let round = 0; round < 2; round += 1) {
    await act(async () => {
      jest.advanceTimersByTime(0);
      await Promise.resolve();
    });
    await flushAsync(times);
  }
}

beforeEach(() => {
  mockLocation.pathname = '/collaboration-square/bots';
  useWorkspaceStore.getState().reset();
  useSquareIdentityStore.getState().reset();
  useExternalAuthStore.getState().reset();
  useExternalAuthStore.getState().setAuthenticated(OAUTH_USER);
  window.localStorage.clear();
  mockBackendRequest.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
  restoreDocumentVisibility();
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
  useWorkspaceStore.getState().reset();
  useSquareIdentityStore.getState().reset();
  useExternalAuthStore.getState().reset();
  mockBackendRequest.mockReset();
});

describe('BCS-only 冷启动（运行时 PUBLIC_BOT_BACKEND_ENABLED=false）', () => {
  it('完整发现页可展示 BCS 候选；冷启动、30 秒轮询窗口与 hidden→visible 期间 Backend 请求均为零', async () => {
    Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', {
      value: { PUBLIC_BOT_BACKEND_ENABLED: false },
      configurable: true,
    });
    routeBackendRequests({ fullMode: false });
    jest.useFakeTimers();

    // 真实 AppShell + 完整发现页（页头含搜索栏与角色选择器）；只在 backendRequest 出口 mock。
    render(
      <AppShell>
        <SquarePageShell resource="bot" />
      </AppShell>,
    );

    // 冷启动：BCS bots/mine 装配身份，BCS candidates 拉公开候选，好友申请回填走 BCS；
    // 无 GET /openapi/v1/bots、metadata/queries、个人空间 initialize 或通知请求。
    await flushListLoad(20);
    expect(screen.getByText('规划发现助手')).toBeInTheDocument();
    // hidden 候选按普通列表口径展示（不额外排除）。
    expect(screen.getByText('隐身发现候选')).toBeInTheDocument();
    const outsideBcsPaths = mockBackendRequest.mock.calls.map(([url]) => url).filter((url) => !BCS_ONLY_ALLOWED_PATHS.has(url));
    expect(outsideBcsPaths).toEqual([]);
    expect(callCount('/openapi/v1/bots', 'GET')).toBe(0);
    expect(callCount('/openapi/v1/bots/metadata/queries', 'POST')).toBe(0);
    
    expect(callCount('/openapi/v1/bots/spaces/personal/initialize', 'POST')).toBe(0);
    expect(unreadPullCount()).toBe(0);

    // Human + 受管 Bot 均可选择（真实 SquareIdentityPicker 弹层），切换后仍只有 BCS 请求。
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '当前协作身份：验收用户七' }));
      await Promise.resolve();
    });
    const botOption = await screen.findByRole('button', { name: /受管机器人/ });
    await act(async () => {
      fireEvent.click(botOption);
      await Promise.resolve();
    });
    expect(useSquareIdentityStore.getState().selectedIdentityId).toBe('assistant:owner-7');
    await flushListLoad(20);
    // 切到 Bot 视角后候选以所选 Bot 身份重读。
    expect(callCount('/openapi/v1/collaboration/bots/assistant%3Aowner-7/candidates', 'GET')).toBe(1);
    expect(mockBackendRequest.mock.calls.map(([url]) => url).filter((url) => !BCS_ONLY_ALLOWED_PATHS.has(url))).toEqual([]);

    // 推进 fake timer 超过 30 秒，再触发 hidden→visible：仍不得出现通知轮询或 Backend 身份补拉。
    act(() => {
      jest.advanceTimersByTime(31_000);
    });
    setDocumentVisibility(true);
    act(() => {
      jest.advanceTimersByTime(31_000);
    });
    setDocumentVisibility(false);
    await flushAsync(20);

    expect(
      mockBackendRequest.mock.calls.map(([url]) => url).filter((url) => !BCS_ONLY_ALLOWED_PATHS.has(url)),
    ).toEqual([]);
    expect(callCount('/openapi/v1/bots', 'GET')).toBe(0);
    expect(callCount('/openapi/v1/bots/spaces/personal/initialize', 'POST')).toBe(0);
    expect(unreadPullCount()).toBe(0);
    // 铃铛未挂载：通知轮询随 AppSidebar 条件挂载自然停止。
    expect(screen.queryByRole('button', { name: '通知中心' })).toBeNull();
  });

  it('角色选择器单独挂载时（无发现页）冷启动仍只有 bots/mine', async () => {
    Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', {
      value: { PUBLIC_BOT_BACKEND_ENABLED: false },
      configurable: true,
    });
    routeBackendRequests({ fullMode: false });
    jest.useFakeTimers();

    render(
      <AppShell>
        <SquareIdentityPicker />
      </AppShell>,
    );
    await flushAsync();

    expect(mockBackendRequest.mock.calls.map(([url]) => url)).toEqual(['/openapi/v1/collaboration/bots/mine']);
    expect(unreadPullCount()).toBe(0);
  });
});

describe('full 模式外壳（无运行时覆盖，默认全栈开启）', () => {
  it('空间初始化与 Backend 身份补充保留；铃铛首拉、30 秒轮询与可见性恢复继续运行', async () => {
    routeBackendRequests({ fullMode: true });
    jest.useFakeTimers();

    render(
      <AppShell>
        <SquareIdentityPicker />
      </AppShell>,
    );
    await flushAsync();

    // App 入口个人空间初始化一次 + Backend 身份补充（GET /openapi/v1/bots 显式带 Human user_id）。
    expect(callCount('/openapi/v1/bots/spaces/personal/initialize', 'POST')).toBe(1);
    expect(callCount('/openapi/v1/bots', 'GET')).toBe(1);
    const listBotsCall = mockBackendRequest.mock.calls.find(([url]) => url === '/openapi/v1/bots');
    expect(listBotsCall?.[1]).toMatchObject({
      method: 'GET',
      params: expect.objectContaining({ user_id: '7' }),
    });

    // 铃铛首拉已发生；30 秒轮询继续运行。
    const firstPullCount = unreadPullCount();
    expect(firstPullCount).toBeGreaterThan(0);
    act(() => {
      jest.advanceTimersByTime(30_000);
    });
    await flushAsync();
    expect(unreadPullCount()).toBeGreaterThan(firstPullCount);

    // hidden 暂停轮询；恢复可见补拉一次并继续轮询。
    const hiddenBaseline = unreadPullCount();
    setDocumentVisibility(true);
    act(() => {
      jest.advanceTimersByTime(30_000);
    });
    await flushAsync();
    expect(unreadPullCount()).toBe(hiddenBaseline);
    setDocumentVisibility(false);
    await flushAsync();
    expect(unreadPullCount()).toBe(hiddenBaseline + 1);
    act(() => {
      jest.advanceTimersByTime(30_000);
    });
    await flushAsync();
    expect(unreadPullCount()).toBeGreaterThan(hiddenBaseline + 1);
    expect(screen.getByRole('button', { name: '通知中心' })).toBeInTheDocument();
  });
});
