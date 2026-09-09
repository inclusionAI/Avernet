/** @jest-environment jsdom */
import { CreateGroupSessionModal } from '@/components/CollaborationSquare/CreateGroupSessionModal';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const group = {
  id: 'g1',
  name: '产品共创群',
  ownerBotName: '群主助手',
  ownerUserName: '示例用户',
  typeLabel: '自由聊天',
  memberCount: 2,
  goal: '推进产品共创',
  memberListVisibility: 'visible' as const,
  canCreateSession: true,
} as PublicGroup;

function renderModal() {
  return render(
    <CreateGroupSessionModal open group={group} loading={false} onClose={jest.fn()} onSubmit={jest.fn()} />,
  );
}

describe('CreateGroupSessionModal（对齐「创建云端 Bot」表单样式）', () => {
  it('不再展示「填写会话名称与协作目标…」描述提示', () => {
    renderModal();
    expect(screen.queryByText('填写会话名称与协作目标，创建后跳转到该会话。')).toBeNull();
  });

  it('字段为 label 包裹 + 必填星号 + 字数计数器', () => {
    renderModal();
    expect(screen.getByText('会话名称', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('协作目标', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('0/100')).toBeInTheDocument();
    expect(screen.getByText('0/2000')).toBeInTheDocument();
    // 两个必填字段均有红色星号标记。
    expect(screen.getAllByText('*').length).toBeGreaterThanOrEqual(2);
  });

  it('底部按钮：取消为 secondary 线框，提交为「创建会话」且空表单禁用', () => {
    renderModal();
    expect(screen.getByRole('button', { name: '取消' })).toHaveClass('border-input');
    const submit = screen.getByRole('button', { name: '创建会话' });
    expect(submit).toBeDisabled();
    expect(submit).toHaveClass('bg-primary');
  });
});
