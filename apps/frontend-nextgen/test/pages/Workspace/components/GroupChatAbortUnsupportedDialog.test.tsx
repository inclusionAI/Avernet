/** @jest-environment jsdom */
import { GroupChatAbortUnsupportedDialog } from '@/pages/Workspace/components/GroupChatAbortUnsupportedDialog';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

describe('GroupChatAbortUnsupportedDialog', () => {
  it('tells the user to restart the Bot and can be dismissed', () => {
    const onClose = jest.fn();
    render(<GroupChatAbortUnsupportedDialog open onClose={onClose} />);

    expect(screen.getByText('当前 Bot 暂不支持终止输出')).toBeInTheDocument();
    expect(screen.getByText('该 Bot 仍在使用旧版插件，请重启 Bot，待其重新上线后再试。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '我知道了' }));
    expect(onClose).toHaveBeenCalled();
  });
});
