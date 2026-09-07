/** @jest-environment jsdom */
import { InviteCodeBindingModal } from '@/components/InviteCodeBindingModal';
import * as inviteCodeService from '@/services/collaboration/inviteCodeService';
import { useInviteCodeGateStore } from '@/stores/inviteCodeGateStore';
import { reloadCurrentTab } from '@/utils/redirectCurrentTab';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

// 仅 mock 两个异步接口，保留 InviteCodeServiceError 与纯函数真实（与 hook 测试同模式）。
jest.mock('@/services/collaboration/inviteCodeService', () => {
  const actual = jest.requireActual('@/services/collaboration/inviteCodeService');
  return { ...actual, getMyInviteCodeBinding: jest.fn(), bindInviteCode: jest.fn() };
});
jest.mock('@/utils/redirectCurrentTab');
const service = inviteCodeService as jest.Mocked<typeof inviteCodeService>;
const mockedReload = reloadCurrentTab as jest.Mock;

beforeEach(() => {
  useInviteCodeGateStore.getState().reset();
  service.bindInviteCode.mockClear();
  mockedReload.mockClear();
});

afterEach(() => {
  // no-op（reloadCurrentTab 为自动 mock，无 location 副作用需恢复）
});

describe('InviteCodeBindingModal', () => {
  it('prompt 信号 → 弹窗显示标题、邀请码输入与「提交邀请码」CTA', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    render(<InviteCodeBindingModal />);
    expect(screen.getByRole('heading', { name: '输入邀请码以开始使用 Avernet' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: '邀请码' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '提交邀请码' })).toBeTruthy();
  });

  it('无 prompt 信号 → 不弹', () => {
    render(<InviteCodeBindingModal />);
    expect(screen.queryByRole('heading', { name: '输入邀请码以开始使用 Avernet' })).toBeNull();
  });

  it('空输入 → 提交按钮禁用（idle 态）', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    render(<InviteCodeBindingModal />);
    expect((screen.getByRole('button', { name: '提交邀请码' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('输入邀请码后 → 提交按钮启用', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    render(<InviteCodeBindingModal />);
    fireEvent.change(screen.getByRole('textbox', { name: '邀请码' }), { target: { value: 'ABC123' } });
    expect((screen.getByRole('button', { name: '提交邀请码' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('不可关闭：无关闭按钮，无退出按钮，唯一出路是「提交邀请码」', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    render(<InviteCodeBindingModal />);
    expect(screen.queryByLabelText('关闭弹窗')).toBeNull();
    expect(screen.queryByRole('button', { name: '稍后再说' })).toBeNull();
    expect(screen.getByRole('button', { name: '提交邀请码' })).toBeTruthy();
  });

  it('ESC 关闭意图不生效（弹窗保持打开）', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    render(<InviteCodeBindingModal />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('heading', { name: '输入邀请码以开始使用 Avernet' })).toBeTruthy();
  });

  it('遮罩/外部点击关闭意图不生效（弹窗保持打开）', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    render(<InviteCodeBindingModal />);
    fireEvent.pointerDown(document.body);
    fireEvent.click(document.body);
    expect(screen.getByRole('heading', { name: '输入邀请码以开始使用 Avernet' })).toBeTruthy();
  });

  it('submitError（store 置位）→ 显示内联错误文案（role=alert）', () => {
    useInviteCodeGateStore.getState().requestPrompt();
    useInviteCodeGateStore
      .getState()
      .setSubmitError({ code: 'invite_code_unavailable', message: '邀请码无效，请检查后重试' });
    render(<InviteCodeBindingModal />);
    expect(screen.getByRole('alert').textContent ?? '').toContain('邀请码无效');
  });

  it('点击「提交邀请码」→ 调用 bindInviteCode（清洗后 code）并 reload 回流', async () => {
    service.bindInviteCode.mockResolvedValue({ bound: true, bound_at: 1730000000 });
    useInviteCodeGateStore.getState().requestPrompt();
    render(<InviteCodeBindingModal />);
    fireEvent.change(screen.getByRole('textbox', { name: '邀请码' }), { target: { value: ' abc 123 ' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '提交邀请码' }));
    });
    await waitFor(() => expect(service.bindInviteCode).toHaveBeenCalledWith('ABC123'));
    await waitFor(() => expect(mockedReload).toHaveBeenCalled());
  });
});
