/** @jest-environment jsdom */
import { SpaceCard } from '@/components/Admin/SpaceCard';
import { SpaceCreateForm } from '@/components/Admin/SpaceCreateForm';
import { SpaceMemberList } from '@/components/Admin/SpaceMemberList';
import { mapSpaceDto, mapSpaceMemberDto } from '@/domain/admin/mappers';
import type { Space } from '@/domain/admin/models';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const space: Space = {
  spaceId: 1,
  spaceCode: 'team',
  spaceName: '研发空间',
  spaceType: 'TEAM',
  memberCount: 0,
  botCount: 2,
  ownerCount: 1,
  gmtModified: '2026-11-10T08:00:00+08:00',
  creatorUserName: '张三',
  currentUserRole: 'ADMIN',
};

describe('Figma 空间管理', () => {
  it('保留真实 0，统计项和状态放入卡片，整卡支持键盘访问', () => {
    const onOpen = jest.fn();
    render(<SpaceCard space={space} isCurrent onOpenDetail={onOpen} />);
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('更新时间')).toBeInTheDocument();
    expect(screen.getByText('创建者')).toBeInTheDocument();
    expect(screen.getByText('张三')).toBeInTheDocument();
    expect(screen.queryByText('Bot')).not.toBeInTheDocument();
    expect(screen.queryByText('管理员')).not.toBeInTheDocument();
    expect(screen.getByText('已加入')).toBeInTheDocument();
    expect(screen.getByText('当前空间')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看空间 研发空间' }));
    expect(onOpen).toHaveBeenCalledWith(space);
  });

  it('接口缺失计数时不伪造 0，真实返回的 0 仍保留', () => {
    const dto = { space_id: 1, space_name: '研发空间', space_type: 'TEAM', member_count: 3 };
    const { item } = mapSpaceDto(dto);
    expect(item.botCount).toBeUndefined();
    expect(item.ownerCount).toBeUndefined();
    expect(mapSpaceMemberDto({ user_id: 'u' }).item.botPermissionCount).toBeUndefined();
    expect(mapSpaceDto({ ...dto, bot_count: 0, owner_count: 0 }).item).toMatchObject({ botCount: 0, ownerCount: 0 });
    render(<SpaceCard space={item} />);
    expect(screen.getAllByText('—')).toHaveLength(2);
  });

  it('个人空间不提供申请入口；申请中的团队按钮禁用', () => {
    const { rerender } = render(
      <SpaceCard space={{ ...space, spaceType: 'PERSONAL', currentUserRole: undefined, isCreator: true }} />,
    );
    expect(screen.getByText('P')).toBeInTheDocument();
    expect(screen.queryByText('申请加入')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /查看空间/ })).toBeInTheDocument();
    rerender(<SpaceCard space={{ ...space, currentUserRole: undefined, joinStatus: 'APPLYING' }} />);
    expect(screen.getByRole('button', { name: '申请中' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /查看空间/ })).not.toBeInTheDocument();
  });

  it('未加入时只申请，不打开成员详情；失败后保留申请表单供重试', async () => {
    const submit = jest.fn().mockResolvedValue(false);
    const openDetail = jest.fn();
    render(
      <SpaceCard space={{ ...space, currentUserRole: undefined }} onOpenDetail={openDetail} onRequestJoin={submit} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '申请加入' }));
    fireEvent.click(screen.getByRole('button', { name: '提交申请' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(expect.objectContaining({ spaceId: 1 }), ''));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(openDetail).not.toHaveBeenCalled();
  });

  it('创建弹窗必填去空格，失败不关闭，成功关闭', async () => {
    const submit = jest.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const close = jest.fn();
    render(<SpaceCreateForm open onOpenChange={close} onSubmit={submit} />);
    const confirm = screen.getByRole('button', { name: '确定' });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/空间名称/), { target: { value: '  团队 A  ' } });
    fireEvent.click(confirm);
    await waitFor(() => expect(submit).toHaveBeenCalledWith('团队 A'));
    expect(close).not.toHaveBeenCalled();
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    await waitFor(() => expect(close).toHaveBeenCalledWith(false));
  });

  it('取消创建后清空未提交名称', () => {
    const { rerender } = render(<SpaceCreateForm open onOpenChange={jest.fn()} onSubmit={jest.fn()} />);
    fireEvent.change(screen.getByLabelText(/空间名称/), { target: { value: '未提交' } });
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    rerender(<SpaceCreateForm open onOpenChange={jest.fn()} onSubmit={jest.fn()} />);
    expect(screen.getByLabelText(/空间名称/)).toHaveValue('');
  });

  it('个人空间展示成员、权限、身份，无管理操作入口', () => {
    render(
      <SpaceMemberList
        space={{ ...space, spaceType: 'PERSONAL' }}
        loading={false}
        members={[
          { userId: 'u', userName: '张三', role: 'ADMIN', botPermissionCount: 0, isCreator: true, gmtModified: '' },
        ]}
        onAddMembers={jest.fn()}
        onRemoveMember={jest.fn()}
        onUpdateRole={jest.fn()}
      />,
    );
    expect(screen.getByText('权限')).toBeInTheDocument();
    expect(screen.getByText('身份')).toBeInTheDocument();
    expect(screen.getByText('0个Bot')).toBeInTheDocument();
    expect(screen.queryByText('操作')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '添加成员' })).not.toBeInTheDocument();
    expect(within(screen.getByRole('listitem')).getByRole('combobox')).toBeDisabled();
  });
});

it('最后一位管理员不能移除，也不能打开移除确认框', () => {
  render(
    <SpaceMemberList
      space={space}
      loading={false}
      members={[
        { userId: 'u', userName: '张三', role: 'ADMIN', botPermissionCount: 0, isCreator: false, gmtModified: '' },
      ]}
      onAddMembers={jest.fn()}
      onRemoveMember={jest.fn()}
      onUpdateRole={jest.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: '移除成员 张三' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '移除成员 张三' }));
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
});

it('创建标题旁问号可通过键盘聚焦显示设计稿的两行提示', async () => {
  render(<SpaceCreateForm open onOpenChange={jest.fn()} onSubmit={jest.fn()} />);
  const help = screen.getByRole('button', { name: '创建团队空间说明' });
  fireEvent.focus(help);
  const tip = await screen.findByRole('tooltip');
  expect(tip.textContent).toBe('个人空间为系统自动分配，无需创建。\n此处仅可创造团队空间。');
  fireEvent.blur(help);
  await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());
});

it('个人空间详情标题旁问号展示成员限制，团队空间不显示该提示入口', async () => {
  const props = {
    space: { ...space, spaceType: 'PERSONAL' as const },
    members: [],
    loading: false,
    onAddMembers: jest.fn(),
    onRemoveMember: jest.fn(),
    onUpdateRole: jest.fn(),
  };
  const { rerender } = render(<SpaceMemberList {...props} />);
  const help = screen.getByRole('button', { name: '个人空间说明' });
  fireEvent.focus(help);
  expect((await screen.findByRole('tooltip')).textContent).toBe('个人空间仅包含你本人，\n不可添加或移除成员');
  expect(screen.queryByRole('button', { name: '添加成员' })).not.toBeInTheDocument();
  fireEvent.blur(help);
  await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());
  rerender(<SpaceMemberList {...props} space={space} />);
  expect(screen.queryByRole('button', { name: '个人空间说明' })).not.toBeInTheDocument();
});
