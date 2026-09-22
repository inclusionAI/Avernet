import { useLayoutEffect, useState, type CSSProperties, type RefObject } from 'react';

type Box = { left: number; top: number; bottom: number };
type Viewport = { left: number; top: number; width: number; height: number };
/** Keep the accepted picker anchored where possible, but never beyond the visible viewport. */
export function botPickerLayout(anchor: Box, height: number, viewport: Viewport): CSSProperties {
  const margin = 12, width = Math.min(520, Math.max(0, viewport.width - margin * 2));
  const maxHeight = Math.max(0, viewport.height - margin * 2);
  const shownHeight = Math.min(height, maxHeight);
  const left = Math.max(viewport.left + margin, Math.min(anchor.left, viewport.left + viewport.width - margin - width));
  const bottom = viewport.top + viewport.height - margin;
  const preferredTop = anchor.bottom + margin + shownHeight <= bottom ? anchor.bottom + margin : anchor.top - margin - shownHeight;
  const top = Math.max(viewport.top + margin, Math.min(preferredTop, bottom - shownHeight));
  return { position: 'fixed', left, top, width, minWidth: 0, maxHeight };
}

export function useBotPickerLayout(open: boolean, anchor: RefObject<HTMLDivElement | null>, picker: RefObject<HTMLDivElement | null>) {
  const [style, setStyle] = useState<CSSProperties>();
  useLayoutEffect(() => {
    if (!open || !anchor.current || !picker.current) return;
    const element = picker.current;
    const update = () => {
      if (!anchor.current) return;
      const view = window.visualViewport;
      const next = botPickerLayout(anchor.current.getBoundingClientRect(), element.getBoundingClientRect().height, {
        left: view?.offsetLeft ?? 0, top: view?.offsetTop ?? 0,
        width: view?.width ?? window.innerWidth, height: view?.height ?? window.innerHeight,
      });
      setStyle(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    };
    update();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    observer?.observe(element); observer?.observe(anchor.current);
    window.addEventListener('resize', update); window.addEventListener('scroll', update, true);
    window.visualViewport?.addEventListener('resize', update); window.visualViewport?.addEventListener('scroll', update);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', update); window.removeEventListener('scroll', update, true);
      window.visualViewport?.removeEventListener('resize', update); window.visualViewport?.removeEventListener('scroll', update);
    };
  }, [open, anchor, picker]);
  return style;
}
