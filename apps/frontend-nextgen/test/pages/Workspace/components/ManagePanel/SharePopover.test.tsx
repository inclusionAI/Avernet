/** @jest-environment jsdom */
import { SharePopover } from '@/pages/Workspace/components/ManagePanel/SharePopover';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

function makeOnCreate() {
  return jest.fn(async (expiresInSeconds: number) => ({
    ok: true as const,
    data: { invitationUrl: `http://example.com/invite?expires=${expiresInSeconds}` },
  }));
}

/** 渲染锚定气泡（气泡经 virtualRef 挂在锚点元素下，测试提供空 span/button 皆可）。 */
function renderSharePopover(
  onCreate: (seconds: number) => Promise<{ ok: true; data: { invitationUrl: string } }>,
  title = '会话',
) {
  const anchorRef = React.createRef<HTMLSpanElement>();
  return render(
    <>
      <span ref={anchorRef} />
      <SharePopover open anchorRef={anchorRef} title={title} onCreate={onCreate} onClose={jest.fn()} />
    </>,
  );
}

/** spec: collaboration-session-share —— 气泡形态：有效期分段 → 生成 → 收据复制，打开不自动生成。 */
describe('SharePopover 气泡 有效期流程', () => {
  it('打开不自动生成：默认选 1 天，点「生成链接」以 86400 秒发起请求', async () => {
    const onCreate = makeOnCreate();
    renderSharePopover(onCreate);
    expect(onCreate).not.toHaveBeenCalled();

    // 气泡头部与说明随目标名渲染
    expect(screen.getByText('分享会话')).toBeInTheDocument();
    expect(screen.getByText('选择有效期并生成链接，其他用户可通过链接加入该会话，直到过期')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '生成链接' }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith(86400));
  });

  it('点「7 天」分段后生成，以 604800 秒发起请求', async () => {
    const onCreate = makeOnCreate();
    renderSharePopover(onCreate, '协作群');
    expect(screen.getByText('分享协作群')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '7 天' }));
    fireEvent.click(screen.getByRole('button', { name: '生成链接' }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith(604800));
  });

  it('生成成功后以收据展示链接并可复制，脚注标注有效期', async () => {
    const onCreate = makeOnCreate();
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderSharePopover(onCreate);

    fireEvent.click(screen.getByRole('button', { name: '生成链接' }));
    await waitFor(() => expect(screen.getByLabelText('邀请链接')).toBeInTheDocument());
    expect(screen.getByText('链接 1 天后过期')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '复制链接' }));
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('http://example.com/invite?expires=86400'),
    );
  });

  it('生成失败保留气泡可重试，不出现链接；成功后可「重新生成」换有效期', async () => {
    let calls = 0;
    const onCreate = jest.fn(async (expiresInSeconds: number) => {
      calls += 1;
      if (calls === 1) {
        return { ok: false as const, error: { code: 'X', friendlyMessage: '生成失败', canRetry: false } };
      }
      return { ok: true as const, data: { invitationUrl: `http://example.com/ok?e=${expiresInSeconds}` } };
    });
    renderSharePopover(onCreate);

    fireEvent.click(screen.getByRole('button', { name: '生成链接' }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText('邀请链接')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成链接' })).toBeInTheDocument();

    // 重试成功 → 收据出现，改分段后「重新生成」以新有效期发起
    fireEvent.click(screen.getByRole('button', { name: '生成链接' }));
    await waitFor(() => expect(screen.getByLabelText('邀请链接')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '30 天' }));
    fireEvent.click(screen.getByRole('button', { name: '重新生成' }));
    await waitFor(() => expect(onCreate).toHaveBeenLastCalledWith(30 * 24 * 60 * 60));
  });
});