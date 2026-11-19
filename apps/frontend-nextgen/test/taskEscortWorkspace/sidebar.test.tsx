/** @jest-environment jsdom */
import { GuardSidebar } from '@/pages/TaskEscort/Navigation';
import { SIDEBAR_WIDTH_KEY } from '@/pages/TaskEscort/useSidebarResize';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';

let desktop = true;
let mediaChange: () => void;
let captured: number | null;
const release = jest.fn(() => {
  captured = null;
});
const handle = () => screen.getByRole('separator');
const width = () => Number(handle().getAttribute('aria-valuenow'));
const sidebar = (page: 'dashboard' | 'editor' = 'dashboard') => <GuardSidebar page={page} navigate={jest.fn()} />;
function pointer(type: string, x: number, id = 1) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, { clientX: x, pointerId: id, button: 0 });
  fireEvent(handle(), event);
}
beforeEach(() => {
  localStorage.clear();
  desktop = true;
  captured = null;
  release.mockClear();
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: jest.fn(() => ({
      matches: desktop,
      addEventListener: (_: string, listener: () => void) => {
        mediaChange = listener;
      },
      removeEventListener: jest.fn(),
    })),
  });
  HTMLElement.prototype.setPointerCapture = function (id: number) {
    captured = id;
  };
  HTMLElement.prototype.hasPointerCapture = function (id: number) {
    return captured === id;
  };
  HTMLElement.prototype.releasePointerCapture = release;
});
afterEach(() => {
  cleanup();
  jest.restoreAllMocks();
});
it('drags right to grow, clamps both bounds, releases capture and restores persisted width', () => {
  render(sidebar());
  expect(width()).toBe(264);
  pointer('pointerdown', 264);
  pointer('pointermove', 300);
  expect(width()).toBe(300);
  pointer('pointermove', 999);
  expect(width()).toBe(360);
  pointer('pointermove', -999);
  expect(width()).toBe(220);
  pointer('pointermove', 310);
  pointer('pointerup', 310);
  expect(release).toHaveBeenCalledWith(1);
  expect(localStorage.getItem(SIDEBAR_WIDTH_KEY)).toBe('310');
  cleanup();
  render(sidebar());
  expect(width()).toBe(310);
});
it('supports arrows, Home and End with persisted bounded values', () => {
  render(sidebar());
  fireEvent.keyDown(handle(), { key: 'ArrowRight' });
  expect(width()).toBe(274);
  fireEvent.keyDown(handle(), { key: 'ArrowLeft' });
  expect(width()).toBe(264);
  fireEvent.keyDown(handle(), { key: 'Home' });
  fireEvent.keyDown(handle(), { key: 'ArrowLeft' });
  expect(width()).toBe(220);
  fireEvent.keyDown(handle(), { key: 'End' });
  fireEvent.keyDown(handle(), { key: 'ArrowRight' });
  expect(width()).toBe(360);
  expect(localStorage.getItem(SIDEBAR_WIDTH_KEY)).toBe('360');
});
it.each(['pointercancel', 'lostpointercapture', 'blur', 'responsive', 'unmount', 'compact'])(
  'cleans up drag on %s without userSelect leakage',
  (ending) => {
    document.body.style.userSelect = 'text';
    const view = render(sidebar());
    pointer('pointerdown', 264);
    pointer('pointermove', 280);
    if (ending === 'blur') fireEvent.blur(window);
    else if (ending === 'responsive')
      act(() => {
        desktop = false;
        mediaChange();
      });
    else if (ending === 'unmount') view.unmount();
    else if (ending === 'compact') view.rerender(sidebar('editor'));
    else pointer(ending, 280);
    expect(captured).toBeNull();
    expect(document.body.style.userSelect).toBe('text');
    if (ending !== 'unmount' && ending !== 'compact') {
      pointer('pointermove', 350);
    }
    expect(localStorage.getItem(SIDEBAR_WIDTH_KEY)).toBe('280');

    document.body.style.userSelect = '';
  },
);
it('keeps compact editor without overwriting expanded width', () => {
  localStorage.setItem(SIDEBAR_WIDTH_KEY, '320');
  const view = render(sidebar('editor'));
  expect(screen.queryByRole('separator')).toBeNull();
  expect(view.container.querySelector('.guard-sidebar-compact')).not.toBeNull();
  view.rerender(sidebar());
  expect(width()).toBe(320);
});
it('ignores mobile drags and unrelated pointer IDs', () => {
  render(sidebar());
  desktop = false;
  pointer('pointerdown', 264);
  pointer('pointermove', 320);
  expect(width()).toBe(264);
  desktop = true;
  pointer('pointerdown', 264);
  pointer('pointermove', 320, 2);
  pointer('pointerup', 320, 2);
  expect(width()).toBe(264);
  expect(captured).toBe(1);
});
it.each(['garbage', 'NaN', '', 'Infinity'])('rejects invalid stored width %s', (value) => {
  localStorage.setItem(SIDEBAR_WIDTH_KEY, value);
  render(sidebar());
  expect(width()).toBe(264);
});
it('survives storage access and quota failures', () => {
  jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota');
  });
  render(sidebar());
  expect(width()).toBe(264);
  fireEvent.keyDown(handle(), { key: 'End' });
  expect(width()).toBe(360);
  pointer('pointerdown', 360);
  pointer('pointercancel', 360);
  expect(captured).toBeNull();
});

it('uses actual viewport width without matchMedia and cleans up on resize', () => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined });
  const originalWidth = window.innerWidth;
  try {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 });
    render(sidebar());
    fireEvent.keyDown(handle(), { key: 'ArrowRight' });
    expect(width()).toBe(274);
    pointer('pointerdown', 274);
    pointer('pointermove', 300);
    expect(width()).toBe(300);
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 600 });
    fireEvent(window, new Event('resize'));
    expect(captured).toBeNull();
    pointer('pointermove', 340);
    pointer('pointerdown', 300);
    pointer('pointermove', 340);
    fireEvent.keyDown(handle(), { key: 'End' });
    expect(width()).toBe(300);
    expect(captured).toBeNull();
  } finally {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth });
  }
});
it('does not start a drag when pointer capture is unavailable', () => {
  render(sidebar());
  Object.defineProperty(handle(), 'setPointerCapture', { configurable: true, value: undefined });
  pointer('pointerdown', 264);
  pointer('pointermove', 340);
  pointer('pointerup', 340);
  expect(width()).toBe(264);
  expect(captured).toBeNull();
  fireEvent.keyDown(handle(), { key: 'ArrowRight' });
  expect(width()).toBe(274);
});
