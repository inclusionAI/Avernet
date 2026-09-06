/** @jest-environment jsdom */
import type { OpenSourceExperienceNoticeSpec } from '@/capabilities';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const mockAcknowledge = jest.fn();
const mockNotice: OpenSourceExperienceNoticeSpec = {
  version: 'open-source-experience-v1',
  message: '本环境仅供开源版本进行功能体验，不提供正式生产服务。请不要上传敏感数据。',
  acknowledgeLabel: '我已知悉',
};
let mockVisible = true;

jest.mock('@/hooks/useOpenSourceExperienceNotice', () => ({
  useOpenSourceExperienceNotice: () => ({ notice: mockNotice, visible: mockVisible, acknowledge: mockAcknowledge }),
}));

const { OpenSourceExperienceNotice } =
  require('@/shell/OpenSourceExperienceNotice') as typeof import('@/shell/OpenSourceExperienceNotice');

describe('OpenSourceExperienceNotice', () => {
  beforeEach(() => {
    mockVisible = true;
    mockAcknowledge.mockClear();
  });

  it('展示完整文案、装饰 Icon 与可访问确认按钮', () => {
    render(<OpenSourceExperienceNotice />);

    const region = screen.getByRole('status', { name: '开源体验环境提示' });
    expect(region).toHaveTextContent(mockNotice.message);
    expect(screen.getByRole('button', { name: '我已知悉' })).toBeInTheDocument();
    expect(region.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
  });

  it('使用不透明语义背景，滚动时不透出页面内容', () => {
    render(<OpenSourceExperienceNotice />);
    const region = screen.getByRole('status', { name: '开源体验环境提示' });

    expect(region).toHaveClass('bg-background');
    expect(region).not.toHaveClass('bg-primary/5');
    expect(region).toHaveStyle({
      backgroundColor: 'color-mix(in srgb, hsl(var(--primary)) 5%, hsl(var(--background)))',
    });
  });

  it('确认按钮触发 acknowledge', () => {
    render(<OpenSourceExperienceNotice />);
    fireEvent.click(screen.getByRole('button', { name: '我已知悉' }));
    expect(mockAcknowledge).toHaveBeenCalledTimes(1);
  });

  it('窄屏结构保持垂直居中，Icon 与按钮不压缩，文案允许换行', () => {
    render(<OpenSourceExperienceNotice />);
    const region = screen.getByRole('status', { name: '开源体验环境提示' });
    const icon = region.querySelector('svg');
    const message = screen.getByText(mockNotice.message);
    const button = screen.getByRole('button', { name: '我已知悉' });

    expect(region).toHaveClass('flex', 'items-center');
    expect(icon).toHaveClass('shrink-0');
    expect(message).toHaveClass('min-w-0', 'whitespace-normal');
    expect(button).toHaveClass('shrink-0');
  });

  it('不可见时不保留布局占位', () => {
    mockVisible = false;
    const { container } = render(<OpenSourceExperienceNotice />);
    expect(container).toBeEmptyDOMElement();
  });
});
