/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { PanelResizeHandle } from '@/components/agentswarm/PanelResizeHandle';

const renderHandle = (over: Partial<React.ComponentProps<typeof PanelResizeHandle>> = {}) => {
  const onWidthChange = jest.fn();
  render(
    <PanelResizeHandle
      width={440}
      onWidthChange={onWidthChange}
      min={380}
      max={720}
      label="调整宽度"
      {...over}
    />,
  );
  return { onWidthChange, handle: screen.getByRole('separator') };
};

test('the handle is exposed as a vertical separator with its current width', () => {
  const { handle } = renderHandle();
  expect(handle).toHaveAttribute('aria-orientation', 'vertical');
  expect(handle).toHaveAttribute('aria-valuenow', '440');
  expect(handle).toHaveAttribute('aria-valuemin', '380');
  expect(handle).toHaveAttribute('aria-valuemax', '720');
});

test('dragging left widens the panel, since it is pinned to the right', () => {
  const { onWidthChange, handle } = renderHandle();
  fireEvent.mouseDown(handle, { clientX: 500 });
  fireEvent.mouseMove(window, { clientX: 400 });
  expect(onWidthChange).toHaveBeenCalledWith(540);
  fireEvent.mouseUp(window);
});

test('dragging right narrows the panel', () => {
  const { onWidthChange, handle } = renderHandle();
  fireEvent.mouseDown(handle, { clientX: 500 });
  fireEvent.mouseMove(window, { clientX: 560 });
  expect(onWidthChange).toHaveBeenCalledWith(380);
  fireEvent.mouseUp(window);
});

test('a drag past the bounds is clamped rather than reported raw', () => {
  const { onWidthChange, handle } = renderHandle();
  fireEvent.mouseDown(handle, { clientX: 500 });
  fireEvent.mouseMove(window, { clientX: 0 });
  expect(onWidthChange).toHaveBeenLastCalledWith(720);
  fireEvent.mouseUp(window);
});

test('no width is reported once the drag ends', () => {
  const { onWidthChange, handle } = renderHandle();
  fireEvent.mouseDown(handle, { clientX: 500 });
  fireEvent.mouseUp(window);
  onWidthChange.mockClear();
  fireEvent.mouseMove(window, { clientX: 100 });
  expect(onWidthChange).not.toHaveBeenCalled();
});

test('the keyboard adjusts the width too', () => {
  const { onWidthChange, handle } = renderHandle();
  fireEvent.keyDown(handle, { key: 'ArrowLeft' });
  expect(onWidthChange).toHaveBeenCalledWith(456);
  fireEvent.keyDown(handle, { key: 'ArrowRight' });
  expect(onWidthChange).toHaveBeenCalledWith(424);
  fireEvent.keyDown(handle, { key: 'ArrowLeft', shiftKey: true });
  expect(onWidthChange).toHaveBeenCalledWith(480);
});

test('Home and End jump to the bounds', () => {
  const { onWidthChange, handle } = renderHandle();
  fireEvent.keyDown(handle, { key: 'Home' });
  expect(onWidthChange).toHaveBeenCalledWith(380);
  fireEvent.keyDown(handle, { key: 'End' });
  expect(onWidthChange).toHaveBeenCalledWith(720);
});