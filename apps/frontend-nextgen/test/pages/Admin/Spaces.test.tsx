/** @jest-environment jsdom */
import { AdminSpacesView } from '@/pages/Admin/Spaces';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const mockNavigate = jest.fn();
const mockKeyword = jest.fn();
const mockAdmin = {
  keyword: '',
  pageNo: 1,
  pageSize: 20,
  items: [],
  total: 0,
  loading: false,
  error: null,
  currentSpace: null,
  members: [],
  membersLoading: false,
  onKeywordChange: mockKeyword,
  changePage: jest.fn(),
  changePageSize: jest.fn(),
  createTeamSpace: jest.fn(),
  openSpaceDetail: jest.fn(),
  closeSpaceDetail: jest.fn(),
  deleteSpace: jest.fn(),
  addMembers: jest.fn(),
  removeMember: jest.fn(),
  updateRole: jest.fn(),
  requestJoin: jest.fn(),
  fetchList: jest.fn(),
};
jest.mock('@umijs/max', () => ({ useNavigate: () => mockNavigate }));
jest.mock('@/hooks/useAdmin', () => ({ useAdmin: () => mockAdmin }));
jest.mock('@/hooks/useSpaceContext', () => ({ useSpaceContext: () => 1 }));

it('空间页保留返回、搜索、创建入口，创建弹窗按需打开', () => {
  render(<AdminSpacesView />);
  fireEvent.click(screen.getByRole('button', { name: '返回' }));
  expect(mockNavigate).toHaveBeenCalledWith(-1);
  fireEvent.change(screen.getByRole('textbox', { name: '搜索空间' }), { target: { value: '研发' } });
  expect(mockKeyword).toHaveBeenCalledWith('研发');
  fireEvent.click(screen.getByRole('button', { name: '创建团队空间' }));
  expect(screen.getByRole('dialog', { name: '创建新团队空间' })).toBeInTheDocument();
});
