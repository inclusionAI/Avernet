/** @jest-environment jsdom */
// 「返回旧版」入口（OpenSpec add-gray-release-nextgen-switch 跨版本切流）：
// 只关心「capability 驱动渲染判定 + 点击写偏好成功才跳 / 失败 toast 不跳」。
// 后端判定/打点细节在 internal overlay 的 grayReleaseService（组装树内测试覆盖）。
// 注意：@jest/globals 须最先 import——组件 import 求值会触发 hoisted mock factory，
// 其访问的 jest 绑定由 @jest/globals 变换注入，晚于此 import 的 factory 拿到 undefined。
import { beforeEach, expect, it, jest } from '@jest/globals';
import { extendCapabilities, type AppCapabilities } from '@/capabilities';
import { LegacyVersionSwitch } from '@/shell/LegacyVersionSwitch';
import { redirectReplace } from '@/utils/urlNavigation';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/utils/urlNavigation', () => ({ redirectReplace: jest.fn() }));
// toast 由协议层 notify 观察者/组件直调 sonner，mock 掉以断言「失败提示不跳转」
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const { toast } = jest.requireMock('sonner') as { toast: { error: jest.Mock } };

const mockLoad = jest.fn<() => Promise<{ oldUrl: string; hitRule: string } | null>>();
const mockSave = jest.fn<(version: 'old' | 'new') => Promise<boolean>>();

function withSpec(spec: { load?: typeof mockLoad; save?: typeof mockSave } | null) {
  extendCapabilities({
    getLegacyVersionSwitch: () =>
      spec
        ? {
            status: 'available' as const,
            value: {
              loadLegacyVersionDecision: spec.load ?? mockLoad,
              saveLegacyVersionPreference: spec.save ?? mockSave,
            },
          }
        : { status: 'available' as const, value: null },
  } satisfies Partial<AppCapabilities>);
}

const DECISION = { oldUrl: 'https://old.example.com', hitRule: 'default' };

describe('LegacyVersionSwitch', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders nothing (and never fetches) when the capability is null (Open Core 形态)', () => {
    withSpec(null);
    const { container } = render(<LegacyVersionSwitch />);
    expect(container).toBeEmptyDOMElement();
    expect(mockLoad).not.toHaveBeenCalled();
  });

  it('renders nothing when the decision is null (fail-open：判定失败静默不渲染)', async () => {
    withSpec({ load: jest.fn(async () => null) });
    const { container } = render(<LegacyVersionSwitch />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('renders nothing when oldUrl is blank (URL 留白阶段不出死入口)', async () => {
    withSpec({ load: jest.fn(async () => ({ oldUrl: '', hitRule: 'default' })) });
    const { container } = render(<LegacyVersionSwitch />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('shows the link once the decision arrives with a usable oldUrl', async () => {
    withSpec({ load: jest.fn(async () => DECISION) });
    render(<LegacyVersionSwitch />);
    expect(await screen.findByRole('button', { name: '返回旧版' })).toBeInTheDocument();
  });

  it('writes preference=old first, then redirects on success', async () => {
    withSpec({ load: jest.fn(async () => DECISION), save: jest.fn(async () => true) });
    render(<LegacyVersionSwitch />);
    fireEvent.click(await screen.findByRole('button', { name: '返回旧版' }));

    await waitFor(() => expect(redirectReplace).toHaveBeenCalledWith('https://old.example.com'));
    expect(redirectReplace).toHaveBeenCalledTimes(1);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('toasts and does NOT redirect when the preference write fails', async () => {
    withSpec({
      load: jest.fn(async () => DECISION),
      save: jest.fn(async () => false),
    });
    render(<LegacyVersionSwitch />);
    fireEvent.click(await screen.findByRole('button', { name: '返回旧版' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(redirectReplace).not.toHaveBeenCalled();
  });

  it('collapsed variant renders an icon-only button', async () => {
    withSpec({ load: jest.fn(async () => DECISION) });
    render(<LegacyVersionSwitch collapsed />);
    expect(await screen.findByRole('button', { name: '返回旧版' })).toBeInTheDocument();
  });
});