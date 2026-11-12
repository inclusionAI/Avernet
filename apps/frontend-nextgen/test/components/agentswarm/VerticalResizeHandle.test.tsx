/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { VerticalResizeHandle } from '@/components/agentswarm/VerticalResizeHandle';

const renderHandle = (over: Partial<React.ComponentProps<typeof VerticalResizeHandle>> = {}) => {
  const onHeightChange = jest.fn();
  render(
    <VerticalResizeHandle
      height={260}
      onHeightChange={onHeightChange}
      min={160}
      max={640}
      label="调整高度"
      {...over}
    />,
  );
  return { onHeightChange, handle: screen.getByRole('separator') };
};

test('the handle exposes its current height and bounds', () => {
  const { handle } = renderHandle();
  expect(handle).toHaveAttribute('aria-orientation', 'horizontal');
  expect(handle).toHaveAttribute('aria-valuenow', '260');
  expect(handle).toHaveAttribute('aria-valuemin', '160');
  expect(handle).toHaveAttribute('aria-valuemax', '640');
});

test('dragging down grows the editor', () => {
  const { onHeightChange, handle } = renderHandle();
  fireEvent.mouseDown(handle, { clientY: 300 });
  fireEvent.mouseMove(window, { clientY: 380 });
  expect(onHeightChange).toHaveBeenCalledWith(340);
  fireEvent.mouseUp(window);
});

test('dragging up shrinks it', () => {
  const { onHeightChange, handle } = renderHandle();
  fireEvent.mouseDown(handle, { clientY: 300 });
  fireEvent.mouseMove(window, { clientY: 240 });
  expect(onHeightChange).toHaveBeenCalledWith(200);
  fireEvent.mouseUp(window);
});

test('a drag past the bounds is clamped', () => {
  const { onHeightChange, handle } = renderHandle();
  fireEvent.mouseDown(handle, { clientY: 300 });
  fireEvent.mouseMove(window, { clientY: 5000 });
  expect(onHeightChange).toHaveBeenLastCalledWith(640);
  fireEvent.mouseUp(window);
});

test('no height is reported after the drag ends', () => {
  const { onHeightChange, handle } = renderHandle();
  fireEvent.mouseDown(handle, { clientY: 300 });
  fireEvent.mouseUp(window);
  onHeightChange.mockClear();
  fireEvent.mouseMove(window, { clientY: 400 });
  expect(onHeightChange).not.toHaveBeenCalled();
});

test('the keyboard adjusts the height', () => {
  const { onHeightChange, handle } = renderHandle();
  fireEvent.keyDown(handle, { key: 'ArrowDown' });
  expect(onHeightChange).toHaveBeenCalledWith(284);
  fireEvent.keyDown(handle, { key: 'ArrowUp' });
  expect(onHeightChange).toHaveBeenCalledWith(236);
  fireEvent.keyDown(handle, { key: 'ArrowDown', shiftKey: true });
  expect(onHeightChange).toHaveBeenCalledWith(320);
});

test('Home and End jump to the bounds', () => {
  const { onHeightChange, handle } = renderHandle();
  fireEvent.keyDown(handle, { key: 'Home' });
  expect(onHeightChange).toHaveBeenCalledWith(160);
  fireEvent.keyDown(handle, { key: 'End' });
  expect(onHeightChange).toHaveBeenCalledWith(640);
});