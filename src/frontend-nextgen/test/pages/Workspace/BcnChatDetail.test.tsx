/** @jest-environment jsdom */
import { BcnChatDetail } from '@/pages/Workspace/BcnChatDetail';
import { useBcnChatDetailRedirect } from '@/pages/Workspace/hooks/useBcnChatDetailRedirect';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { useNavigate } from 'react-router-dom';

jest.mock('@umijs/max', () => ({
  history: { replace: jest.fn() },
  useSearchParams: jest.fn(),
}));
jest.mock('@/pages/Workspace/hooks/useBcnChatDetailRedirect');
jest.mock('react-router-dom', () => ({ useNavigate: jest.fn() }));

const mockedHook = useBcnChatDetailRedirect as jest.MockedFunction<typeof useBcnChatDetailRedirect>;
const mockedNavigate = useNavigate as jest.MockedFunction<typeof useNavigate>;

beforeEach(() => {
  jest.clearAllMocks();
  mockedNavigate.mockReturnValue(jest.fn() as unknown as ReturnType<typeof useNavigate>);
});

it('定位中：展示加载态', () => {
  mockedHook.mockReturnValue({ status: 'redirecting' });
  render(<BcnChatDetail />);
  expect(screen.getByText('正在打开协作会话…')).toBeInTheDocument();
});

it('参数不完整：空态提示 + 返回对话协作按钮', () => {
  mockedHook.mockReturnValue({ status: 'invalid' });
  render(<BcnChatDetail />);
  expect(screen.getByText('链接参数不完整')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '返回对话协作' })).toBeInTheDocument();
});

it('路由已注册：/workspace/bcn/chat/detail → BcnChatDetail', () => {
  const routesSource = readFileSync('config/routes.ts', 'utf8');
  expect(routesSource).toContain("'/workspace/bcn/chat/detail'");
  expect(routesSource).toContain("'@/pages/Workspace/BcnChatDetail'");
});
