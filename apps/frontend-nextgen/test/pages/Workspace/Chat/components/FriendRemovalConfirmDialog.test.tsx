/** @jest-environment jsdom */
// 移除好友确认弹窗契约(PRD v1.5 AC-02/AC-03):完整告知后果;取消不触发任何变更。
import { FriendRemovalConfirmDialog } from '@/pages/Workspace/Chat/components/FriendRemovalConfirmDialog';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

describe('FriendRemovalConfirmDialog', () => {
  it('完整告知移除后果', () => {
    render(
      <FriendRemovalConfirmDialog open botName="Alpha" onConfirm={jest.fn()} onCancel={jest.fn()} />,
    );

    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '移除好友' })).toBeInTheDocument();
    expect(screen.getByText('确定要移除好友「Alpha」吗？')).toBeInTheDocument();
    expect(screen.getByText('· 如需继续协作，须重新发起添加好友申请。')).toBeInTheDocument();
    expect(screen.getByText('· 该 Bot 将从「好友 Bot」分组消失。')).toBeInTheDocument();
    expect(
      screen.getByText('· 历史单聊会话将从会话列表隐藏且无法再发起新会话；重新添加好友后恢复显示。'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        '· 不影响你通过「我管理的 Bot」或「我协作的 Bot」获得的关系，也不影响 Bot 的公开配置和已共同所在的协作群。',
      ),
    ).toBeInTheDocument();
  });

  it('取消只关闭弹窗，不触发确认', () => {
    const onConfirm = jest.fn();
    const onCancel = jest.fn();
    render(<FriendRemovalConfirmDialog open onConfirm={onConfirm} onCancel={onCancel} />);

    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('确认按钮文案为「移除好友」并触发确认', () => {
    const onConfirm = jest.fn();
    render(<FriendRemovalConfirmDialog open onConfirm={onConfirm} onCancel={jest.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '移除好友' }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});