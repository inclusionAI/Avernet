/** @jest-environment jsdom */
// UserSearchDropdown：覆盖 Open Core 降级手填工号 / 内部 overlay 搜索选中 / 已添加禁用。
import { extendCapabilities } from '@/capabilities';
import UserSearchDropdown from '@/components/Admin/SpaceMemberList/UserSearchDropdown';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const mockSearch = jest.fn();

beforeEach(() => {
  mockSearch.mockReset();
});

it('不支持员工目录时降级为手填工号：输入并回车 → onSelect 合成 {userId,displayName}', () => {
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'unsupported', value: null, reason: 'no directory' }),
  });
  const onSelect = jest.fn();
  render(<UserSearchDropdown onSelect={onSelect} />);
  const input = screen.getByLabelText('用户 ID') as HTMLInputElement;
  fireEvent.change(input, { target: { value: '10086' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onSelect).toHaveBeenCalledWith({ userId: '10086', displayName: '10086' });
});

it('支持搜索：输入关键词 → 防抖后展示候选 → 点击选中回完整 SearchedUser 并清空输入', async () => {
  mockSearch.mockResolvedValue([{ userId: '10086', displayName: '寻三', nickName: '寻三', email: 'x@alipay.com' }]);
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'available', value: { search: mockSearch } }),
  });
  const onSelect = jest.fn();
  render(<UserSearchDropdown onSelect={onSelect} />);
  const input = screen.getByLabelText('搜索员工') as HTMLInputElement;
  fireEvent.change(input, { target: { value: '寻三' } });
  await waitFor(() => expect(mockSearch).toHaveBeenCalledWith('寻三'));
  const option = await screen.findByRole('button', { name: /寻三\(10086\)/ });
  fireEvent.click(option);
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ userId: '10086', nickName: '寻三' }));
  expect(input.value).toBe('');
});

it('花名为空时展示真名(工号)：nickName 缺失/为空 → 显示 realName(userId)', async () => {
  mockSearch.mockResolvedValue([
    { userId: 'gl520932', displayName: '郭亮', nickName: '', realName: '郭亮', email: 'gl520932@alibaba-inc.com' },
    {
      userId: 'liang.guol',
      displayName: '郭亮',
      nickName: undefined,
      realName: '郭亮',
      email: 'liang.guol@antgroup.com',
    },
  ]);
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'available', value: { search: mockSearch } }),
  });
  const onSelect = jest.fn();
  render(<UserSearchDropdown onSelect={onSelect} />);
  const input = screen.getByLabelText('搜索员工') as HTMLInputElement;
  fireEvent.change(input, { target: { value: '郭亮' } });
  // 两条候选均显示真名(userId)
  await screen.findByRole('button', { name: /郭亮\(gl520932\)/ });
  fireEvent.click(screen.getByRole('button', { name: /郭亮\(liang\.guol\)/ }));
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ realName: '郭亮' }));
});

it('已添加成员在候选中显示「已添加」且不可选', async () => {
  mockSearch.mockResolvedValue([
    { userId: '10086', displayName: '寻三', nickName: '寻三' },
    { userId: '10087', displayName: '寻二', nickName: '寻二' },
  ]);
  extendCapabilities({
    getUserSearchCapability: () => ({ status: 'available', value: { search: mockSearch } }),
  });
  const onSelect = jest.fn();
  render(<UserSearchDropdown onSelect={onSelect} disabledUserIds={['10086']} />);
  const input = screen.getByLabelText('搜索员工') as HTMLInputElement;
  fireEvent.change(input, { target: { value: '寻三' } });
  await screen.findByRole('button', { name: /寻三\(10086\)/ });
  // 已添加项含「已添加」标记
  expect(screen.getByText('已添加')).toBeInTheDocument();
  // 点击未禁用项可选中
  fireEvent.click(screen.getByRole('button', { name: /寻二\(10087\)/ }));
  expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ userId: '10087' }));
});
