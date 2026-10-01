/** @jest-environment jsdom */
import { SquareIdentityPicker } from '@/components/CollaborationSquare/SquareIdentityPicker';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useSquareIdentityStore } from '@/stores/squareIdentityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import '@testing-library/jest-dom';
import { act, render, screen } from '@testing-library/react';

jest.mock('@/components/Workspace/IdentitySelector', () => ({
  WorkspaceIdentitySelector: jest.fn(() => <div data-testid="identity-selector" />),
}));
jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getUserProfilePresentation: () => ({ value: { preferAuthenticatedUserProfile: false } }),
  }),
}));

const mockedUseHumanIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;
const mockedSelector = WorkspaceIdentitySelector as jest.MockedFunction<typeof WorkspaceIdentitySelector>;

const IDENTITIES = [
  { id: 'human_900003', kind: 'user' as const, displayName: '当前用户', online: true },
  { id: 'bot-1:900003', kind: 'bot' as const, displayName: '协作 Bot', online: true },
];

function selectorProps() {
  const lastCall = mockedSelector.mock.calls[mockedSelector.mock.calls.length - 1];
  return lastCall[0];
}

describe('SquareIdentityPicker', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useWorkspaceStore.getState().reset();
    useSquareIdentityStore.getState().reset();
    useWorkspaceStore.getState().setIdentities(IDENTITIES, 'human_900003');
    mockedUseHumanIdentity.mockReturnValue({
      identity: { userId: '900003', displayName: '当前用户', online: true },
      status: 'ready',
    });
    mockedSelector.mockClear();
  });

  it('默认渲染身份选择器，选中项为登录用户身份，头部标签定制为「为 Ta 加好友：」', () => {
    render(<SquareIdentityPicker />);

    expect(screen.getByTestId('identity-selector')).toBeInTheDocument();
    const props = selectorProps();
    expect(props.identities).toHaveLength(2);
    expect(props.activeId).toBe('human_900003');
    expect(props.identityStatus).toBe('ready');
    expect(props.headerLabel).toBe('为 Ta 加好友：');
    expect(props.headerTooltip).toBe('每个身份（用户或 Bot）都拥有各自独立的好友关系。');
    expect(props.layout).toBe('sidebar');
    expect(props.hideBotRegistration).toBe(true);
    expect(props.triggerClassName).toContain('bg-background');
  });

  it('模块级选择 Bot 身份后选中项切换为该 Bot', () => {
    act(() => useSquareIdentityStore.getState().selectIdentity('bot-1:900003'));
    render(<SquareIdentityPicker />);

    expect(selectorProps().activeId).toBe('bot-1:900003');
  });

  it('onChange 选中 Bot 身份时写入模块级身份并持久化', () => {
    render(<SquareIdentityPicker />);
    act(() => selectorProps().onChange('bot-1:900003'));

    expect(useSquareIdentityStore.getState().selectedIdentityId).toBe('bot-1:900003');
    expect(window.localStorage.getItem('teamclaw:square:identityId')).toBe('bot-1:900003');
  });

  it('onChange 选回用户身份时回到默认（清除持久化）', () => {
    act(() => useSquareIdentityStore.getState().selectIdentity('bot-1:900003'));
    render(<SquareIdentityPicker />);
    act(() => selectorProps().onChange('human_900003'));

    expect(useSquareIdentityStore.getState().selectedIdentityId).toBeNull();
    expect(window.localStorage.getItem('teamclaw:square:identityId')).toBeNull();
  });

  it('身份列表加载失败时向选择器传递 error 态并提供页内重试回调（AC-6）', () => {
    useWorkspaceStore.getState().reset();
    mockedUseHumanIdentity.mockReturnValue({ identity: null, status: 'error', error: '身份未加载' });
    render(<SquareIdentityPicker />);

    const props = selectorProps();
    expect(props.identityStatus).toBe('error');
    expect(props.identityError).toBe('身份未加载');
    expect(typeof props.onRetry).toBe('function');
  });
});
