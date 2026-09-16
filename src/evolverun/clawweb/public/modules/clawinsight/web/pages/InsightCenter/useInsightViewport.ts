import { useLayoutEffect, useRef } from 'react';

/** Fit below either host's navigation without changing body styles or other routes. */
export function useInsightViewport() {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const shell = ref.current;
    if (!shell) return;
    let frame = 0;
    const measure = () => {
      // Document-relative offset also works when entering from a scrolled route.
      const offset = `${Math.max(0, shell.getBoundingClientRect().top + window.scrollY)}px`;
      if (shell.style.getPropertyValue('--insight-viewport-offset') !== offset) {
        shell.style.setProperty('--insight-viewport-offset', offset);
      }
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
    // Observe the surrounding layout, including a wrapping/resizing host navigation.
    // Do not depend on private host selectors or assume a 56/64px header height.
    for (let node: Element | null = shell; node && node !== document.body; node = node.parentElement) {
      if (node.parentElement) observer?.observe(node.parentElement);
      for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
        observer?.observe(sibling);
      }
    }
    window.addEventListener('resize', schedule);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', schedule);
      cancelAnimationFrame(frame);
    };
  }, []);
  return ref;
}
