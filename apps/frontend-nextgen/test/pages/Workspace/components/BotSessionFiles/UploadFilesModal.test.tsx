/** @jest-environment jsdom */
import { BotUploadFilesModal } from '@/pages/Workspace/components/BotSessionFiles/UploadFilesModal';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

function buildProps() {
  return {
    open: true,
    onClose: jest.fn(),
    queue: [],
    isUploading: false,
    stageFiles: jest.fn().mockReturnValue([]),
    submit: jest.fn().mockResolvedValue(undefined),
    onAddToSession: jest.fn(),
    removeTask: jest.fn(),
  };
}

describe('BotUploadFilesModal（v1.4 治理：dropzone 可点击语义）', () => {
  it('拖拽区以 role=button 暴露可点击语义（global.css cursor 兜底命中，业务代码不再手写 cursor-pointer）', () => {
    render(<BotUploadFilesModal {...buildProps()} />);
    const dropzone = screen.getByRole('button', { name: /点击或拖拽选择文件/ });
    expect(dropzone).toHaveAttribute('tabindex', '0');
    expect(dropzone).not.toHaveClass('cursor-pointer');
  });

  it('Enter 键触发文件选择（键盘可达）', () => {
    const clickSpy = jest.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
    render(<BotUploadFilesModal {...buildProps()} />);
    fireEvent.keyDown(screen.getByRole('button', { name: /点击或拖拽选择文件/ }), { key: 'Enter' });
    expect(clickSpy).toHaveBeenCalled();
    clickSpy.mockRestore();
  });
});
