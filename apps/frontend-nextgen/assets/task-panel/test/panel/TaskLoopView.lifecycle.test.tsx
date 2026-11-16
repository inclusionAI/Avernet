import React from 'react';
import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { TaskLoopView } from '../../src/panel/TaskLoopView';
import { createTaskPanelController, type TaskPanelController } from '../../src/controller/createTaskPanelController';
import { makeHost, tab } from './taskSwitcherFixtures';

const controllers: TaskPanelController[] = [];
afterEach(() => {
  act(() => { controllers.forEach(controller => controller.dispose()); });
  controllers.length = 0;
  vi.restoreAllMocks();
});

function setup() {
  const host = makeHost();
  const controller = createTaskPanelController(host);
  controllers.push(controller);
  const original = vi.mocked(host.transport.requestJson).getMockImplementation()!;
  const requests: { path: string; signal: AbortSignal }[] = [];
  vi.mocked(host.transport.requestJson).mockImplementation(async (path, signal) => {
    // The switcher's unpaginated list is separate from TaskPanelFetcher metadata.
    if (path.includes('/list?') && !path.includes('&page=')) {
      requests.push({ path, signal });
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
      });
    }
    return original(path, signal);
  });
  return { host, controller, requests };
}

it('waits for conversation binding before starting the switcher list request', async () => {
  const { host, controller, requests } = setup();
  render(<TaskLoopView host={host} controller={controller} taskId="t1" apiBaseUrl="" />);
  await act(async () => {});
  expect(requests).toHaveLength(0);

  await act(async () => {
    await controller.enterConversation({ sessionId: 'agent:main:session:A:user:owner-A', kind: 'bot' }, tab('t1'), { recover: false });
  });
  expect(requests).toHaveLength(1);
  expect(requests[0].path).toContain('user_id=owner-A');
  expect(requests[0].signal.aborted).toBe(false);
});

it('aborts the old list on leave without starting an unbound request, and resumes after reentry', async () => {
  vi.useFakeTimers();
  const { host, controller, requests } = setup();
  await controller.enterConversation({ sessionId: 'agent:main:session:A:user:owner-A', kind: 'bot' }, tab('t1'), { recover: false });
  const view = render(<TaskLoopView host={host} controller={controller} taskId="t1" apiBaseUrl="" />);
  await act(async () => {});
  expect(requests).toHaveLength(1);

  act(() => controller.leaveConversation());
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(120_000);
  });
  expect(requests[0].signal.aborted).toBe(true);
  expect(requests).toHaveLength(1);

  await act(async () => {
    await controller.enterConversation({ sessionId: 'agent:main:session:B:user:owner-B', kind: 'bot' }, tab('t1'), { recover: false });
  });
  expect(requests).toHaveLength(2);
  expect(requests[1].path).toContain('user_id=owner-B');
  expect(requests[1].signal.aborted).toBe(false);

  view.unmount();
  expect(requests[1].signal.aborted).toBe(true);
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(120_000);
  });
  expect(requests).toHaveLength(2);
});
