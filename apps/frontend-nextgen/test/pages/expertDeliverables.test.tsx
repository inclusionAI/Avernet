/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { ExpertDetails } from '@/pages/CollaborationSquare/Experts/ExpertDetails';
import type { ExpertDetail } from '@/domain/expertMarket';

let mockExpert: ExpertDetail;
jest.mock('@/hooks/useExpertDetails', () => ({
  useExpertDetails: () => ({ expert: mockExpert, error: '', loading: false, reload: jest.fn() }),
}));
jest.mock('@/hooks/useSpaceContext', () => ({
  initSpaceContext: jest.fn(),
  useSpaceContext: (select: (state: { spaces: unknown[] }) => unknown) => select({ spaces: [] }),
}));
jest.mock('@/components/BotWorkshop/CreateBotModal', () => () => null);

beforeEach(() => {
  mockExpert = {
    expert_id: 'reviewer', name: '文档专家', description: '评审文档', category: '内容',
    tags: [], creation_count: 0, first_added_at: '2026-10-29', identity: [], skills: [],
  };
});

test('shows resource destinations as declarations with actual delivery semantics', () => {
  mockExpert = {
    ...mockExpert,
    resources: [{ path: 'reference/guide.txt' }, { path: 'templates/' }],
  };
  render(<ExpertDetails expertId="reviewer" onBack={jest.fn()} />);
  expect(screen.queryByRole('heading', { name: '模板声明的 CLI' })).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: '模板声明的资源' })).toBeVisible();
  expect(screen.getByText('reference/guide.txt')).toBeVisible();
  expect(screen.getByText('templates/')).toBeVisible();
  expect(screen.getByText(/先清理工作区非保护项/)).toBeVisible();
  expect(screen.getByText(/普通用户输出也可能被清理/)).toBeVisible();
  expect(screen.queryByText('已安装')).not.toBeInTheDocument();
});

test('an explicit empty resource declaration shows clearing while an omission makes no claim', () => {
  const { rerender } = render(<ExpertDetails expertId="reviewer" onBack={jest.fn()} />);
  expect(screen.queryByRole('heading', { name: '模板声明的资源' })).not.toBeInTheDocument();
  mockExpert = { ...mockExpert, resources: [] };
  rerender(<ExpertDetails expertId="reviewer" onBack={jest.fn()} />);
  expect(screen.getByText('模板声明清理资源，不写入新文件。')).toBeVisible();
});
