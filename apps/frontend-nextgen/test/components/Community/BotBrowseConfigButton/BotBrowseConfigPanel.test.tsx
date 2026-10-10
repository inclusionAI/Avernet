/** @jest-environment jsdom */
import { BotBrowseConfigPanel } from '@/components/Community/BotBrowseConfigButton/BotBrowseConfigPanel';
import type { UseBotBrowseConfigResult } from '@/hooks/useBotBrowseConfig';
import { useBotBrowseConfig } from '@/hooks/useBotBrowseConfig';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/hooks/useBotBrowseConfig', () => ({
  useBotBrowseConfig: jest.fn(),
}));

const mockHook = useBotBrowseConfig as jest.MockedFunction<typeof useBotBrowseConfig>;

function setResult(overrides: Partial<UseBotBrowseConfigResult> = {}): UseBotBrowseConfigResult {
  return {
    rows: [],
    loading: false,
    error: null,
    gated: false,
    reload: jest.fn(),
    // 组件对 toggle/saveNote 的返回值链式 .then/.catch，mock 必须返回已决 Promise。
    toggle: jest.fn().mockResolvedValue(undefined),
    saveNote: jest.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe('BotBrowseConfigPanel', () => {
  beforeEach(() => jest.clearAllMocks());

  test('loading 时渲染骨架占位（aria-busy）', () => {
    mockHook.mockReturnValue(setResult({ loading: true }));
    render(<BotBrowseConfigPanel />);
    expect(screen.getByRole('status', { name: '加载社区配置' })).toBeInTheDocument();
  });

  test('gated（Bot 身份）提示切回用户身份', () => {
    mockHook.mockReturnValue(setResult({ gated: true }));
    render(<BotBrowseConfigPanel />);
    expect(screen.getByText('需在「我的身份」下配置')).toBeInTheDocument();
  });

  test('无 Bot 行时展示空态', () => {
    mockHook.mockReturnValue(setResult());
    render(<BotBrowseConfigPanel />);
    expect(screen.getByText('暂无 Bot')).toBeInTheDocument();
  });

  test('渲染 Bot 行（名称/真实 id/开关/备注）并响应开关：已关闭→点开', () => {
    const toggle = jest.fn().mockResolvedValue(undefined);
    mockHook.mockReturnValue(
      setResult({
        rows: [{ botId: 'b1', botName: '小红', note: '', enabled: false, saving: false }],
        toggle,
      }),
    );
    render(<BotBrowseConfigPanel />);
    expect(screen.getByText('小红')).toBeInTheDocument();
    expect(screen.getByText('b1')).toBeInTheDocument();
    const sw = screen.getByRole('switch', { name: '小红 周期性逛社区' });
    // 开关当前为关；点击后 radix onCheckedChange 回调传「下一个状态 = true」。
    expect(sw).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(sw);
    expect(toggle).toHaveBeenCalledWith('b1', true);
  });

  test('备注输入 blur 触发 saveNote（已开启行）', () => {
    const saveNote = jest.fn().mockResolvedValue(undefined);
    mockHook.mockReturnValue(
      setResult({
        rows: [{ botId: 'b2', botName: '小蓝', note: '', enabled: true, saving: false }],
        saveNote,
      }),
    );
    render(<BotBrowseConfigPanel />);
    const input = screen.getByRole('textbox', { name: '小蓝 备注' }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '新备注' } });
    fireEvent.blur(input);
    expect(saveNote).toHaveBeenCalledWith('b2', '新备注');
  });

  test('未开启行的备注输入被禁用，blur 不触发 saveNote', () => {
    const saveNote = jest.fn().mockResolvedValue(undefined);
    mockHook.mockReturnValue(
      setResult({
        rows: [{ botId: 'b3', botName: '小灰', note: '', enabled: false, saving: false }],
        saveNote,
      }),
    );
    render(<BotBrowseConfigPanel />);
    const input = screen.getByRole('textbox', { name: '小灰 备注' }) as HTMLInputElement;
    expect(input).toBeDisabled();
    fireEvent.blur(input);
    expect(saveNote).not.toHaveBeenCalled();
  });

  test('错误态展示重试按钮，点击调用 reload', () => {
    const reload = jest.fn();
    mockHook.mockReturnValue(setResult({ error: '加载失败', reload }));
    render(<BotBrowseConfigPanel />);
    fireEvent.click(screen.getByRole('button', { name: '重试加载' }));
    expect(reload).toHaveBeenCalled();
  });
});
