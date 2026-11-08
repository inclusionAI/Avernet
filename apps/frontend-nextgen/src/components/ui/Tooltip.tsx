import { cn } from '@/utils/cn';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import React from 'react';

/**
 * Tooltip 基础组件：统一 hover/focus 提示（unify-tooltip-antd），不替代控件的 accessible name。
 *
 * 视觉/动效对齐 antd Tooltip 默认基线（design.md 实测 antd@6.6.1 token）：
 * 深色 token 底（--color-tooltip，亮 85% 黑 / 暗 #424242）+ 白字（colorTextLightSolid，
 * 明暗恒白）+ 6px 圆角 + 6/8 内边距 + antd dropShadowPopover 三层阴影 + 指向箭头
 * + max-width 250px 换行；进出场为 zoom-big-fast 观感（~100ms 缩放+淡入淡出，
 * keyframes 见 global.css 的 tooltip-pop-in/out，用 CSS scale 独立属性合成定位
 * inline transform，不会跳位）。
 */
const TooltipProvider = TooltipPrimitive.Provider;

/**
 * 锚点桥（退出防线）：Trigger 把自身/气泡 DOM 节点登记进 Root 持有的 guard，
 * 在 pointerleave/blur 合成事件帧内（早于 Radix 关闭 commit）探测锚点存活性——
 * hover 显隐按钮（hidden/group-hover）失焦场景锚点已 display:none/归零，probeExit
 * 直接摘除气泡的 .tooltip-anim-exit 类；Radix presence 在关闭 commit 里读到
 * animation:none 即同步卸载（同帧，保持孤儿气泡缺陷修复行为）。正常锚点关闭则
 * 保留该类，走缩放淡出。详见下方 guard.probeExit 与 TooltipTrigger 注释。
 */
interface TooltipGuard {
  anchorRef: React.MutableRefObject<HTMLElement | null>;
  contentRef: React.MutableRefObject<HTMLDivElement | null>;
  /** 本次关闭是否跳过退出动画（锚点已消失/矩形归零时 true）。 */
  skipExit: boolean;
  /** 探针写入：供 Trigger 在指针/焦点离开时同步调用（见 TooltipTrigger 注释）。 */
  probeExit: () => void;
}

/** 默认 guard：Tooltip 外直接裸用 TooltipContent/TooltipTrigger 时兜底（跳过退出动画路径）。 */
const noopGuard: TooltipGuard = {
  anchorRef: { current: null },
  contentRef: { current: null },
  skipExit: true,
  probeExit: () => {},
};
const TooltipGuardContext = React.createContext<TooltipGuard>(noopGuard);

type TooltipProps = React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Root>;

const Tooltip = ({ children, ...props }: TooltipProps) => {
  const anchorRef = React.useRef<HTMLElement | null>(null);
  const contentRef = React.useRef<HTMLDivElement | null>(null);
  const [skipExit, setSkipExit] = React.useState(false);

  const probeExit = React.useCallback(() => {
    const anchor = anchorRef.current;
    const visible = !!anchor && anchor.isConnected && anchor.getBoundingClientRect().width > 0;
    // ⚠ 时序关键：退出动画的挂载决策必须发生在 Radix 关闭 commit 之前。presence 在
    // data-state=closed 落地的 commit layout effect 里同步读 computed style 决定是否
    // 保留气泡等动画播完；而 Radix 非受控 Root 的 onOpenChange 在 useEffect 里才回调
    // （晚于该判定），靠它改 class 必晚一帧。故探针挂在 Trigger 的 pointerleave/blur
    // 合成事件里（早于 Radix 内部关闭状态更新），于此同步直写气泡 classList。
    contentRef.current?.classList.toggle('tooltip-anim-exit', visible);
    setSkipExit(!visible);
  }, []);

  const guard = React.useMemo<TooltipGuard>(
    () => ({ anchorRef, contentRef, skipExit, probeExit }),
    [skipExit, probeExit],
  );

  return (
    <TooltipGuardContext.Provider value={guard}>
      <TooltipPrimitive.Root {...props}>{children}</TooltipPrimitive.Root>
    </TooltipGuardContext.Provider>
  );
};

type TooltipTriggerProps = React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Trigger>;

/**
 * 退出动画探针入口：hover 显隐按钮（hidden/group-hover）失焦场景，行失焦令锚点
 * display:none 的 pointerleave/blur 在此事件帧内锚盒即已归零——探测并直写气泡
 * 退出动画类（见 guard.probeExit 注释）；正常锚点离开时探针什么也不改（类由渲染
 * 默认带上）。事件 prop 与消费方的同名回调串行合成（先消费方后探针）。
 */
const TooltipTrigger = React.forwardRef<HTMLButtonElement, TooltipTriggerProps>(
  ({ onPointerLeave, onBlur, ...props }, forwardedRef) => {
    const guard = React.useContext(TooltipGuardContext);
    const setAnchor = React.useCallback(
      (node: HTMLElement | null) => {
        guard.anchorRef.current = node;
        if (typeof forwardedRef === 'function') {
          forwardedRef((node as HTMLButtonElement) ?? null);
        } else if (forwardedRef) {
          forwardedRef.current = (node as HTMLButtonElement) ?? null;
        }
      },
      [forwardedRef, guard],
    );
    const composedPointerLeave = React.useCallback(
      (event: React.PointerEvent<HTMLButtonElement>) => {
        onPointerLeave?.(event);
        guard.probeExit();
      },
      [guard, onPointerLeave],
    );
    const composedBlur = React.useCallback(
      (event: React.FocusEvent<HTMLButtonElement>) => {
        onBlur?.(event);
        guard.probeExit();
      },
      [guard, onBlur],
    );
    return (
      <TooltipPrimitive.Trigger
        ref={setAnchor}
        onPointerLeave={composedPointerLeave}
        onBlur={composedBlur}
        {...props}
      />
    );
  },
);
TooltipTrigger.displayName = TooltipPrimitive.Trigger.displayName;

type TooltipContentProps = React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>;

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  TooltipContentProps
>(
  ({ className, sideOffset = 6, children, ...props }, ref) => {
    const guard = React.useContext(TooltipGuardContext);
    // 气泡节点登记进 guard：Trigger 探针需直写该节点的退出动画类（时序见 TooltipTrigger 注释）
    const setContent = React.useCallback(
      (node: HTMLDivElement | null) => {
        guard.contentRef.current = node;
        if (typeof ref === 'function') {
          ref(node);
        } else if (ref) {
          ref.current = node;
        }
      },
      [guard, ref],
    );
    return (
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          ref={setContent}
          sideOffset={sideOffset}
          className={cn(
            // 观感基线见组件头注释；gap（sideOffset 6px）由下方 Arrow 完整桥接。
            'tooltip-anim z-[var(--z-tooltip)] max-w-[250px] overflow-visible rounded-md bg-[var(--color-tooltip)] px-2 py-1.5 text-sm leading-normal [word-wrap:break-word] text-white',
            // 层叠 drop-shadow 取自 antd dropShadowPopover 原值（colorBgSpotlight 深底
            // 上的阴影观感按 antd 的 filter 方案复刻，非 box-shadow）。
            '[filter:drop-shadow(0_6px_16px_rgba(0,0,0,0.08))_drop-shadow(0_3px_6px_rgba(0,0,0,0.12))_drop-shadow(0_9px_28px_rgba(0,0,0,0.05))]',
            // 退出动画仅在锚点存活性探测通过时随关闭状态挂载；防残影路径见 TooltipTrigger/probeExit 注释。
            !guard.skipExit && 'tooltip-anim-exit',
            className,
          )}
          {...props}
        >
          {children}
          {/* 箭头：填色跟气泡 token（明暗随 --tooltip-bg），高度对齐 sideOffset 桥满间隙。 */}
          <TooltipPrimitive.Arrow
            width={12}
            height={6}
            className="fill-[var(--color-tooltip)]"
          />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    );
  },
);
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger };
