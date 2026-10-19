/** @jest-environment jsdom */
import { ChatLayoutPanelSlot } from '@/components/Workspace/ChatLayoutPanelSlot';
import type { PanelHandle } from '@tc-chat/core';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';

it('协作群口径 dismissOnOutside=false：外部点击不收起，ref 关闭（顶栏副屏钮/悬浮收起钮同通道）同步真实态且可重开', () => {
  const ref = createRef<PanelHandle>();
  const onOpen = jest.fn();
  render(
    <ChatLayout>
      <button type="button">消息区</button>
      <ChatLayoutPanelSlot
        ref={ref}
        defaultOpen
        autoCloseWhenEmpty={false}
        dismissOnOutside={false}
        onOpenStateChange={onOpen}
        className="test-sdk-panel-pinned"
      />
    </ChatLayout>,
  );
  const panel = document.querySelector('.test-sdk-panel-pinned');
  expect(panel).not.toBeNull();
  // 外部点击（消息区 / body）不再触发收起：副屏仅在顶栏右侧副屏钮与右上悬浮收起钮处关闭。
  fireEvent.pointerDown(screen.getByText('消息区'));
  fireEvent.pointerDown(document.body);
  expect(onOpen).not.toHaveBeenCalledWith(false);
  // 显式关闭通道（与 toggleSidePane → closePanel 同一 handle）：关闭并同步真实开合态。
  act(() => ref.current!.closePanel());
  expect(onOpen).toHaveBeenLastCalledWith(false);
  // 关闭后仍可重开，重开后外部点击依旧不收起。
  act(() => ref.current!.openPanel());
  expect(onOpen).toHaveBeenLastCalledWith(true);
  fireEvent.pointerDown(document.body);
  expect(onOpen).toHaveBeenLastCalledWith(true);
});

it('真实 SDK 副屏：内部及相邻拖宽手柄不关闭，外部点击关闭并同步真实状态，仍可重开', () => {
  const ref = createRef<PanelHandle>();
  const onOpen = jest.fn();
  render(
    <ChatLayout>
      <button type="button">消息区</button>
      <ChatLayoutPanelSlot
        ref={ref}
        defaultOpen
        autoCloseWhenEmpty={false}
        onOpenStateChange={onOpen}
        className="test-sdk-panel"
      />
    </ChatLayout>,
  );
  const panel = document.querySelector('.test-sdk-panel');
  // SDK 的拖宽条与面板为相邻兄弟，必须和面板内部一样保留交互。
  expect(panel).not.toBeNull();
  fireEvent.pointerDown(panel!);
  fireEvent.pointerDown(panel!.previousElementSibling!);
  expect(onOpen).not.toHaveBeenCalledWith(false);
  fireEvent.pointerDown(screen.getByText('消息区'));
  expect(onOpen).toHaveBeenLastCalledWith(false);
  act(() => ref.current!.openPanel());
  expect(onOpen).toHaveBeenLastCalledWith(true);
  fireEvent.pointerDown(document.body);
  expect(onOpen).toHaveBeenLastCalledWith(false);
});
