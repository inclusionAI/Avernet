/**
 * @jest-environment jsdom
 */
import { ChatLayoutPanelSlot } from '@/components/Workspace/ChatLayoutPanelSlot';
import { PANEL_WIDTH_STORAGE_KEY } from '@/services/workspace/panelWidthMemory';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { render } from '@testing-library/react';
import React from 'react';

// 捕获 <ChatLayoutPanelSlot> 内部渲染 <ChatLayout.Panel> 收到的 props（push-stack 防类型收窄）。
// 真实运行时 ChatLayoutRoot 会 cloneElement 注入 onIsOpenChange（Root 的 ResizeHandle
// setter）——用例模拟该注入行为，验证组合分发语义。
let mockPanelRenders: Array<Record<string, unknown>> = [];

/**
 * 模拟 Root cloneElement 注入：onIsOpenChange 不在 ChatLayoutPanelSlot 公开类型里
 * （运行时由 ChatLayoutRoot 注入），测试里以类型逃逸挂载同语义 props；
 * 对 spread 不做 excess property 检查，故以组件 props 整体断言即可。
 */
const asRootInjected = (props: Record<string, unknown>) =>
  props as unknown as React.ComponentProps<typeof ChatLayoutPanelSlot>;
jest.mock('@tc-chat/ui/es/ChatLayout', () => {
  const { forwardRef } = require('react');
  const Panel = forwardRef((props: Record<string, unknown>) => {
    mockPanelRenders.push(props);
    return null;
  });
  const ChatLayout = () => null;
  ChatLayout.Panel = Panel;
  return { ChatLayout };
});

describe('ChatLayoutPanelSlot', () => {
  beforeEach(() => {
    mockPanelRenders = [];
    localStorage.clear();
  });

  it('displayName 为 "ChatLayout.Panel"，使 ChatLayoutRoot 识别插槽并 cloneElement 注入真实开合态', () => {
    // Root 以 child.type.displayName === 'ChatLayout.Panel' 扫描 child 识别副屏插槽。
    expect(ChatLayoutPanelSlot.displayName).toBe('ChatLayout.Panel');
  });

  it('SDK 尚未测得 containerWidth 时直接以 TaskPanel 的 420px 初始宽回退', () => {
    render(<ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', containerWidth: null })} />);
    expect(mockPanelRenders.at(-1)?.panelWidth).toBe(420);
  });

  it('调用方显式提供 panelWidth 时仍优先使用调用方宽度', () => {
    render(<ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', panelWidth: 500 })} />);
    expect(mockPanelRenders.at(-1)?.panelWidth).toBe(500);
  });

  it('组合分发 onIsOpenChange：Root 注入的 setter 原样透传，App 侧 onOpenStateChange 串联其后', () => {
    // Root 经 cloneElement 注入的 setIsPanelOpen（ResizeHandle 开合镜像）。
    const rootIsOpenChange = jest.fn();
    // App 侧真实开合态观察口。
    const onOpenStateChange = jest.fn();
    const { rerender } = render(
      <ChatLayoutPanelSlot
        {...asRootInjected({ bridge: 'bridge', onIsOpenChange: rootIsOpenChange, onOpenStateChange })}
      />,
    );

    // 内部 <ChatLayout.Panel> 收到组合后的 onIsOpenChange（openTab 自动展开 /
    // closePanel / closePanelForce / autoCloseWhenEmpty 弹回全部写路径均经此回调）。
    const composed = mockPanelRenders.at(-1)?.onIsOpenChange as ((open: boolean) => void) | undefined;
    expect(typeof composed).toBe('function');
    composed?.(true);
    expect(rootIsOpenChange).toHaveBeenCalledWith(true);
    expect(onOpenStateChange).toHaveBeenCalledWith(true);
    composed?.(false);
    expect(rootIsOpenChange).toHaveBeenCalledWith(false);
    expect(onOpenStateChange).toHaveBeenCalledWith(false);
    expect(onOpenStateChange).toHaveBeenCalledTimes(2);

    // 业务 props（bridge 等）透传不丢。
    expect(mockPanelRenders.at(-1)?.bridge).toBe('bridge');

    // onOpenStateChange 更换引用：重渲染后组合函数跟随新回调（闭包不过期）。
    const next = jest.fn();
    rerender(<ChatLayoutPanelSlot {...asRootInjected({ onIsOpenChange: rootIsOpenChange, onOpenStateChange: next })} />);
    (mockPanelRenders.at(-1)?.onIsOpenChange as (open: boolean) => void)?.(true);
    expect(next).toHaveBeenCalledWith(true);
  });

  it('未注入 Root setter 时（面板独立渲染等）仅通知 App 侧观察口', () => {
    const onOpenStateChange = jest.fn();
    render(<ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', onOpenStateChange })} />);
    (mockPanelRenders.at(-1)?.onIsOpenChange as (open: boolean) => void)?.(true);
    expect(onOpenStateChange).toHaveBeenCalledWith(true);
  });

  it('slot 私有 props 剥离：dismissOnOutside 不透传给 SDK Panel（缺省 true 行为由真实 SDK 用例看守）', () => {
    mockPanelRenders = [];
    render(<ChatLayoutPanelSlot {...asRootInjected({ bridge: 'bridge', dismissOnOutside: false })} />);
    // strip-before-forward：SDK Panel 不感知收起策略，只收到业务 props。
    expect(mockPanelRenders.at(-1)?.dismissOnOutside).toBeUndefined();
    expect(mockPanelRenders.at(-1)?.bridge).toBe('bridge');
  });

  it('Root 注入拖拽结果（dynamicWidth% + containerWidth）时换算 px 落盘（拖宽记忆）', () => {
    localStorage.clear();
    render(
      <ChatLayoutPanelSlot
        {...asRootInjected({ bridge: 'bridge', dynamicWidth: '45%', containerWidth: 1000 })}
      />,
    );
    expect(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY)).toBe('450');
  });

  it('拖拽更新时持续跟踪最新宽度（连续拖动记忆的是落点不是起点）', () => {
    localStorage.clear();
    const { rerender } = render(
      <ChatLayoutPanelSlot
        {...asRootInjected({ bridge: 'bridge', dynamicWidth: '45%', containerWidth: 1000 })}
      />,
    );
    rerender(
      <ChatLayoutPanelSlot
        {...asRootInjected({ bridge: 'bridge', dynamicWidth: '56%', containerWidth: 1000 })}
      />,
    );
    expect(localStorage.getItem(PANEL_WIDTH_STORAGE_KEY)).toBe('560');
  });
});
