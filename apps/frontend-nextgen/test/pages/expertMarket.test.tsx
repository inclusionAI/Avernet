/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TextDecoder, TextEncoder } from 'node:util';
import ExpertCard, { ExpertCardSkeleton } from '@/components/CollaborationSquare/ExpertCard';
import ExpertMarketPage from '@/pages/CollaborationSquare/Experts';
import { getExpert, listExpertCategories, listExperts } from '@/services/expertMarketService';

// jsdom 环境缺 TextEncoder/TextDecoder，renderToStaticMarkup（react-dom/server）需要。
Object.assign(globalThis, { TextDecoder, TextEncoder });

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
  expect(screen.getByRole('link', { name: '公开 Bot' })).toHaveAttribute('href', '/collaboration-square/bots');
  expect(screen.getByRole('link', { name: '公开协作群' })).toHaveAttribute('href', '/collaboration-square/groups');
});


test('a request failure is an error with retry, never a successful empty result', async () => {
  list.mockRejectedValueOnce(new Error('目录暂不可用')).mockResolvedValueOnce({ total: 0, items: [] });
  render(<ExpertMarketPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent('目录暂不可用');
  expect(screen.queryByText('没有找到匹配的专家')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
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
  // 滚动加载后页码不再是 URL 态：输入新关键词会清掉历史遗留的 page_num。
  expect(params.has('page_num')).toBe(false);
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
  expect(params.has('page_num')).toBe(false);
  expect(params.has('expert_id')).toBe(false);
});

test('cards keep filters in detail links and category/sort changes restart from the first page', async () => {
  mockSearch = '?keyword=PRD&category=产品&sort=hot&page_num=2';
  list.mockResolvedValue({ total: 25, items: [{
    expert_id: 'reviewer', name: '文档评审', description: '检查需求', category: '产品', tags: ['PRD'],
    creation_count: 0, first_added_at: '2026-10-27T00:00:00Z',
  }] });
  render(<ExpertMarketPage />);
  const link = await screen.findByRole('link', { name: '查看详情' });
  const target = new URL(link.getAttribute('href')!, 'https://example.test').searchParams;
  expect(target.get('keyword')).toBe('PRD');
  expect(target.get('category')).toBe('产品');
  expect(target.has('page_num')).toBe(false);
  expect(target.get('expert_id')).toBe('reviewer');
  fireEvent.keyDown(screen.getByRole('combobox', { name: '专家分类' }), { key: 'Enter' });
  fireEvent.click(screen.getByRole('option', { name: '全部分类' }));
  // 筛选变化不携带页码：页码已不进 URL，回到第 1 页重拉由 feed 自身保证（hook 测试锁定）。
  const patched = new URL(mockPush.mock.calls[0][0], 'https://example.test').searchParams;
  expect(patched.get('category')).toBe('');
  expect(patched.has('page_num')).toBe(false);
  fireEvent.keyDown(screen.getByRole('combobox', { name: '专家排序' }), { key: 'Enter' });
  fireEvent.click(screen.getByRole('option', { name: '最新' }));
  const sorted = new URL(mockPush.mock.calls[1][0], 'https://example.test').searchParams;
  expect(sorted.get('sort')).toBe('newest');
  expect(sorted.has('page_num')).toBe(false);
});

test('滚动到页底自动追加下一页，追加失败不吞错且可从断点页重试', async () => {
  const pageOne = Array.from({ length: 20 }, (_, index) => ({
    expert_id: `expert-${index}`, name: `首批专家${index + 1}`, description: '检查需求', category: '产品',
    tags: ['PRD'], creation_count: 0, first_added_at: '2026-10-27T00:00:00Z',
  }));
  list
    .mockResolvedValueOnce({ total: 25, items: pageOne })
    .mockRejectedValueOnce(new Error('网络波动'))
    .mockResolvedValueOnce({ total: 25, items: [{
      expert_id: 'expert-20', name: '追加专家', description: '检查需求', category: '产品',
      tags: ['PRD'], creation_count: 0, first_added_at: '2026-10-27T00:00:00Z',
    }] });
  render(<ExpertMarketPage />);
  expect(await screen.findByText('首批专家1')).toBeVisible();
  fireEvent.scroll(screen.getByRole('main'));
  // 追加失败：已载内容保持原样，错误行可见且带断点重试。
  expect(await screen.findByRole('alert')).toHaveTextContent('网络波动');
  expect(screen.getByText('首批专家1')).toBeVisible();
  expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page_num: 2 }), expect.anything());
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('追加专家')).toBeVisible();
  expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page_num: 2 }), expect.anything());
});

test('专家卡与骨架逐区镜像公开Bot/协作群卡语言（白底描边卡、胶囊、border-t 底行）', () => {
  const { renderToStaticMarkup } = require('react-dom/server') as typeof import('react-dom/server');
  const cardHtml = renderToStaticMarkup(
    <ExpertCard
      expert={{
        expert_id: 'product-manager', name: '产品经理', description: '评审产品文档',
        category: '产品', tags: ['PRD', '评审', '演讲', '协调'], creation_count: 3,
        first_added_at: '2026-10-27T00:00:00Z',
      }}
      detailHref="/collaboration-square/experts?expert_id=product-manager"
    />,
  );
  // 与群卡同规格：白底 1px #EEE 描边（border-divider）、r16 圆角、底部 border-t + hover-only CTA。
  expect(cardHtml).toContain('border-divider');
  expect(cardHtml).toContain('bg-card');
  expect(cardHtml).toContain('rounded-2xl');
  expect(cardHtml).toContain('border-t');
  expect(cardHtml).toContain('查看详情');
  expect(cardHtml).toContain('已创建 3 个 Bot');
  // 标签超过 3 个折叠为 +N，保证网格行高节奏稳定。
  expect(cardHtml).toContain('+1');
  // 卡组件文件不得直写脉冲类，占位由 ui/Skeleton 承担。
  expect(cardHtml).not.toContain('animate-');

  const skeletonHtml = renderToStaticMarkup(<ExpertCardSkeleton />);
  // 骨架逐区镜像真实卡：名称行 + 右上胶囊位、两行描述、标签 chip 位、border-t 底行（计数 + CTA 位）。
  expect(skeletonHtml).toContain('w-[65px]');
  expect(skeletonHtml).toContain('border-t');
  expect(skeletonHtml.match(/h-6 w-1[24] rounded-md/g)?.length).toBeGreaterThan(0);
  expect(skeletonHtml).toContain('h-4 w-28');
});
