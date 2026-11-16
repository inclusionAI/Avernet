/** @jest-environment jsdom */
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

const mockSonnerToaster = jest.fn<JSX.Element, [unknown]>(() => (
  <div data-testid="sonner-toaster" />
));

jest.mock('sonner', () => ({
  Toaster: (props: unknown) => mockSonnerToaster(props),
}));

import { Toaster } from '@/components/ui/sonner';

describe('global Toaster', () => {
  beforeEach(() => {
    mockSonnerToaster.mockClear();
  });

  it('renders toast messages at the top center of the page without a close button', () => {
    render(<Toaster />);

    const props = mockSonnerToaster.mock.calls[0][0] as Record<string, unknown>;
    expect(props).toEqual(
      expect.objectContaining({
        position: 'top-center',
        richColors: true,
      }),
    );
    expect(props.closeButton).toBeUndefined();
  });

  it('portals to document.body so toasts stay above Radix Modal overlays', () => {
    render(<Toaster />);

    // sonner 原位渲染会停留在应用根容器内；qiankun 宿主容器的层叠上下文会把
    // toast 压到 body 级 Modal 遮罩之下。断言 toaster 是 body 直接子节点。
    const toaster = screen.getByTestId('sonner-toaster');
    expect(toaster.parentElement).toBe(document.body);
  });

  it('unmount removes the portaled toaster node from document.body', () => {
    const { unmount } = render(<Toaster />);

    unmount();
    expect(document.querySelector('[data-testid="sonner-toaster"]')).toBeNull();
  });
});
