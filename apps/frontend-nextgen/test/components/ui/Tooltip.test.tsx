/** @jest-environment jsdom */
import '@testing-library/jest-dom';

/**
 * unify-tooltip-antd — 共享 Tooltip 基线回归。
 * 覆盖 spec（hover-tooltip）：
 *  - 统一视觉：antd 基线皮肤类 / 250px 换行 / 箭头 / 消费方 className 合成
 *  - 退出动画不得复活孤儿气泡缺陷：锚点归零 → 即时卸载；正常锚点 → 退出动画播完后卸载
 *
 * jsdom 说明：global.css 被 jest styleMock 掉，keyframes 不会产生真实 animationName，
 * 故退出场景对 window.getComputedStyle 打 spy + Proxy，按元素类/状态动态回答
 * animationName，驱动 Radix presence 的「关闭是否等动画播完」判定（与真实
 * 浏览器路径同为 presence 读 computed style 的同一分支）。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/Tooltip';

const realGetComputedStyle = window.getComputedStyle.bind(window);

// jsdom 环境补桩：Radix Popper 挂载依赖 ResizeObserver/pointer capture（BotTable.test 同款）
beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

/**
 * 对挂了 .tooltip-anim 的气泡元素代理 animationName：
 * open → tooltip-pop-in；closed 且带 .tooltip-anim-exit（锚点存活性探测通过）
 * → tooltip-pop-out；closed 无退出类 → none（presence 立即卸载）。
 * 其余元素/属性全量透传真实样式。
 */
const stubTooltipAnimationName = () => {
  const spy = jest.spyOn(window, 'getComputedStyle');
  spy.mockImplementation(((elt: Element, pseudo?: string | null) => {
    const styles = realGetComputedStyle(elt, pseudo);
    const isTooltipBubble =
      elt instanceof window.HTMLElement && elt.classList.contains('tooltip-anim');
    if (!isTooltipBubble) {
      return styles;
    }
    return new Proxy(styles, {
      get(target, prop) {
        if (prop === 'animationName') {
          const state = elt.dataset.state;
          if (state === 'open' || state === 'delayed-open' || state === 'instant-open') {
            return 'tooltip-pop-in';
          }
          if (state === 'closed' && elt.classList.contains('tooltip-anim-exit')) {
            return 'tooltip-pop-out';
          }
          return 'none';
        }
        const value = Reflect.get(target, prop, target);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    }) as unknown as CSSStyleDeclaration;
  }) as typeof window.getComputedStyle);
  return spy;
};

/** 触发元素带非零锚盒（jsdom 默认全零矩形，导致存活性探测恒 false，需打桩模拟真实几何）。 */
const stubAliveAnchorRect = (el: HTMLElement) => {
  el.getBoundingClientRect = () =>
    ({
      width: 40,
      height: 20,
      top: 100,
      left: 100,
      bottom: 120,
      right: 140,
      x: 100,
      y: 100,
      toJSON: () => ({}),
    }) as DOMRect;
};

const renderTooltip = () => {
  render(
    // Radix Root 要求 Provider 祖先（应用内由各消费方/全局挂载，测试显式包一层）
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button">触发点</button>
        </TooltipTrigger>
        <TooltipContent>提示文案</TooltipContent>
      </Tooltip>
    </TooltipProvider>,
  );
  return screen.getByRole('button', { name: '触发点' });
};

const openOn = async (trigger: HTMLElement) => {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  await user.hover(trigger);
  await waitFor(() => {
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
  });
  return screen.getByRole('tooltip');
};

describe('Tooltip 基线皮肤（antd 对齐）', () => {
  test('气泡渲染 antd 基线：深色 token 底、白字、250px 上限换行、指向箭头', async () => {
    const bubble = await openOn(renderTooltip());

    expect(bubble).toHaveClass('bg-[var(--color-tooltip)]');
    expect(bubble).toHaveClass('text-white');
    expect(bubble).toHaveClass('max-w-[250px]');
    expect(bubble).toHaveClass('[word-wrap:break-word]');
    // 指向箭头：Radix Arrow 渲染为气泡内 svg，填色跟气泡 token
    const arrow = bubble.querySelector('svg');
    expect(arrow).toBeInTheDocument();
    expect(arrow).toHaveClass('fill-[var(--color-tooltip)]');
  });

  test('消费方自定义 className 可叠加，冲突项以消费方为准（twMerge）', async () => {
    render(
      <TooltipProvider delayDuration={0}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button type="button">触发点</button>
          </TooltipTrigger>
          <TooltipContent className="max-w-[280px] bg-foreground p-2">提示文案</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    await openOn(screen.getByRole('button', { name: '触发点' }));
    const bubble = screen.getByRole('tooltip');

    // 覆写生效：基线 max-w / 背景 / 内边距被消费方值替换
    expect(bubble).toHaveClass('max-w-[280px]');
    expect(bubble).toHaveClass('bg-foreground');
    expect(bubble).toHaveClass('p-2');
    expect(bubble).not.toHaveClass('max-w-[250px]');
    expect(bubble).not.toHaveClass('bg-[var(--color-tooltip)]');
    // 非冲突项（进入动画类/箭头桥接皮肤）保留
    expect(bubble).toHaveClass('tooltip-anim');
  });
});

describe('Tooltip 退出防线（孤儿气泡红线的组件层行为）', () => {
  test('正常锚点关闭：退出动画挂载，播完后才卸载', async () => {
    const animationSpy = stubTooltipAnimationName();
    const trigger = renderTooltip();
    stubAliveAnchorRect(trigger);
    await openOn(trigger);

    // 正常关闭：锚点保持可见（触发元素已有非零矩形桩），失焦关闭
    fireEvent.blur(trigger);
    // presence 因 tooltip-pop-out 进入 unmountSuspended，气泡保留至动画播完
    await waitFor(() => {
      const closing = screen.getByRole('tooltip');
      expect(closing).toHaveAttribute('data-state', 'closed');
      expect(closing).toHaveClass('tooltip-anim-exit');
    });

    // 动画播完后才卸载。jsdom 无 AnimationEvent 实现（fireEvent.animationEnd 丢
    // animationName），手工构造带 animationName 的事件驱动 presence 的 ANIMATION_END
    const animationEnd = new window.Event('animationend');
    Object.defineProperty(animationEnd, 'animationName', { value: 'tooltip-pop-out' });
    screen.getByRole('tooltip').dispatchEvent(animationEnd);
    await waitFor(() => {
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    });
    animationSpy.mockRestore();
  });

  test('锚点归零（hover 显隐按钮失焦）关闭：跳过退出动画，同步卸载无残影', async () => {
    const animationSpy = stubTooltipAnimationName();
    const trigger = renderTooltip();
    stubAliveAnchorRect(trigger);
    await openOn(trigger);

    // 行失焦 → 行内按钮 display:none：锚点 isConnected 但矩形归零（复写为隐藏态矩形）
    trigger.getBoundingClientRect = () =>
      ({
        width: 0,
        height: 0,
        top: 0,
        left: 0,
        bottom: 0,
        right: 0,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }) as DOMRect;

    const bubbleAtClose = screen.getByRole('tooltip');
    fireEvent.blur(trigger);

    // 即使 animationName stub 会替退出动画作答，probe 判死也必须同步卸载（无残影）
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    expect(bubbleAtClose).not.toBeInTheDocument();
    animationSpy.mockRestore();
  });
});
