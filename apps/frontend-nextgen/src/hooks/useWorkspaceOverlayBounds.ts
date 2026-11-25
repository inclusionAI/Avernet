import { useLayoutEffect, useRef, useState } from 'react';

/** 保留会话区尺寸，但让覆盖层脱离路由的 isolate/transform 层叠上下文。 */
export function useWorkspaceOverlayBounds() {
  const anchorRef = useRef<HTMLSpanElement>(null);
  const [bounds, setBounds] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const region = anchor?.closest('[data-workspace-conversation]') ?? anchor?.parentElement;
    if (!region) return undefined;
    const update = () => {
      const { left, top, width, height } = region.getBoundingClientRect();
      setBounds((previous) =>
        previous?.left === left && previous.top === top && previous.width === width && previous.height === height
          ? previous
          : { left, top, width, height },
      );
    };
    update();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    observer?.observe(region);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, []);
  return { anchorRef, bounds };
}
