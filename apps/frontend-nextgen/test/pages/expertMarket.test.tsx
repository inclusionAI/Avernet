/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ExpertMarketPage from '@/pages/CollaborationSquare/Experts';
import { getExpert, listExpertCategories, listExperts } from '@/services/expertMarketService';

let mockSearch = '';
const mockPush = jest.fn();
jest.mock('@umijs/max', () => ({
  useLocation: () => ({ pathname: '/collaboration-square/experts', search: mockSearch }),
  history: { push: (...args: unknown[]) => mockPush(...args), replace: (...args: unknown[]) => mockPush(...args) },
  Link: ({ to, children, ...props }: any) => <a href={to} {...props}>{children}</a>,
}));
jest.mock('@/services/expertMarketService', () => ({
  listExperts: jest.fn(), getExpert: jest.fn(), listExpertCategories: jest.fn(),
}));
const list = jest.mocked(listExperts);
const categories = jest.mocked(listExpertCategories);
const detail = jest.mocked(getExpert);

beforeEach(() => {
  jest.clearAllMocks();
  mockSearch = '';
  categories.mockResolvedValue(['产品']);
});

test('renders successful zero results separately from loading and preserves public resource links', async () => {
  list.mockResolvedValue({ total: 0, items: [] });
  render(<ExpertMarketPage />);
  expect(screen.getByRole('status')).toHaveTextContent('正在加载专家');
  expect(await screen.findByText('没有找到匹配的专家')).toBeVisible();
  expect(screen.getByRole('link', { name: '公开Bot' })).toHaveAttribute('href', '/collaboration-square/bots');
  expect(screen.getByRole('link', { name: '公开协作群' })).toHaveAttribute('href', '/collaboration-square/groups');
});


test('a request failure is an error with retry, never a successful empty result', async () => {
  list.mockRejectedValueOnce(new Error('目录暂不可用')).mockResolvedValueOnce({ total: 0, items: [] });
  render(<ExpertMarketPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent('目录暂不可用');
  expect(screen.queryByText('没有找到匹配的专家')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('没有找到匹配的专家')).toBeVisible();
});

test('composition does not search intermediate input, and its completed keyword resets pagination', async () => {
  mockSearch = '?page_num=3&category=产品&sort=newest';
  list.mockResolvedValue({ total: 0, items: [] });
  render(<ExpertMarketPage />);
  await screen.findByText('没有找到匹配的专家');
  const input = screen.getByRole('textbox', { name: '搜索专家' });
  fireEvent.compositionStart(input);
  fireEvent.change(input, { target: { value: 'chan' } });
  await new Promise((resolve) => { setTimeout(resolve, 350); });
  expect(mockPush).not.toHaveBeenCalled();
  fireEvent.compositionEnd(input, { data: '产品', target: { value: '产品' } });
  await waitFor(() => expect(mockPush).toHaveBeenCalledTimes(1));
  const params = new URL(mockPush.mock.calls[0][0], 'https://example.test').searchParams;
  expect(params.get('keyword')).toBe('产品');
  expect(params.get('page_num')).toBe('1');
  expect(params.get('category')).toBe('产品');
  expect(params.get('sort')).toBe('newest');
});

test('detail renders authored Markdown and real counts, then returns with the original filters', async () => {
  mockSearch = '?keyword=PRD&category=产品&sort=newest&page_num=2&expert_id=product-manager';
  list.mockResolvedValue({ total: 0, items: [] });
  detail.mockResolvedValue({
    expert_id: 'product-manager', name: '产品经理', description: '评审产品文档', category: '产品',
    tags: ['PRD'], creation_count: 7, first_added_at: '2026-10-27T00:00:00Z',
    identity: [{ type: 'AGENTS.md', content: '# 专业身份\n\n<script>unsafe()</script>\n\n[危险链接](javascript:alert(1))' }],
    skills: [{ name: 'prd-review', description: '检查验收标准' }],
    mcp: [{ server_code: 'project-tools' }],
  });
  render(<ExpertMarketPage />);
  expect(await screen.findByRole('heading', { name: '产品经理' })).toBeVisible();
  expect(screen.getByText('已创建 7 个 Bot')).toBeVisible();
  expect(screen.getByRole('heading', { name: '专业身份' })).toBeVisible();
  expect(screen.getByText('检查验收标准')).toBeVisible();
  expect(screen.getByText('Local Skill')).toBeVisible();
  expect(screen.getByRole('heading', { name: '模板声明的 MCP' })).toBeVisible();
  expect(screen.getByText('project-tools')).toBeVisible();
  expect(screen.queryByText('已授权')).not.toBeInTheDocument();
  expect(document.querySelector('script')).toBeNull();
  expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '返回专家列表' }));
  const params = new URL(mockPush.mock.calls[0][0], 'https://example.test').searchParams;
  expect(params.get('keyword')).toBe('PRD');
  expect(params.get('category')).toBe('产品');
  expect(params.get('sort')).toBe('newest');
  expect(params.get('page_num')).toBe('2');
  expect(params.has('expert_id')).toBe(false);
});

test('cards keep filters in detail links and category/sort changes return to the first page', async () => {
  mockSearch = '?keyword=PRD&category=产品&sort=hot&page_num=2';
  list.mockResolvedValue({ total: 25, items: [{
    expert_id: 'reviewer', name: '文档评审', description: '检查需求', category: '产品', tags: ['PRD'],
    creation_count: 0, first_added_at: '2026-10-27T00:00:00Z',
  }] });
  render(<ExpertMarketPage />);
  const link = await screen.findByRole('link', { name: '查看 文档评审' });
  const target = new URL(link.getAttribute('href')!, 'https://example.test').searchParams;
  expect(target.get('keyword')).toBe('PRD');
  expect(target.get('page_num')).toBe('2');
  expect(target.get('expert_id')).toBe('reviewer');
  fireEvent.keyDown(screen.getByRole('combobox', { name: '专家分类' }), { key: 'Enter' });
  fireEvent.click(screen.getByRole('option', { name: '全部分类' }));
  expect(new URL(mockPush.mock.calls[0][0], 'https://example.test').searchParams.get('page_num')).toBe('1');
  fireEvent.keyDown(screen.getByRole('combobox', { name: '专家排序' }), { key: 'Enter' });
  fireEvent.click(screen.getByRole('option', { name: '最新' }));
  const sorted = new URL(mockPush.mock.calls[1][0], 'https://example.test').searchParams;
  expect(sorted.get('page_num')).toBe('1');
  expect(sorted.get('sort')).toBe('newest');
});
