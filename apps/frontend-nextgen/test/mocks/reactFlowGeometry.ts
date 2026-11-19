/** Browser geometry shims for real React Flow integration tests in jsdom.
 * Adapted from React Flow's official testing guidance. No component/store mocks.
 * Install per suite and restore afterwards; observer timers stop on disconnect.
 */
export function installReactFlowGeometry(): () => void {
  const restore: Array<() => void> = [];
  const replace = (target: object, key: PropertyKey, descriptor: PropertyDescriptor) => {
    const previous = Object.getOwnPropertyDescriptor(target, key);
    Object.defineProperty(target, key, { configurable: true, ...descriptor });
    restore.push(() => {
      if (previous) Object.defineProperty(target, key, previous);
      else Reflect.deleteProperty(target, key);
    });
  };
  class MeasuredResizeObserver {
    private timers = new Map<Element, ReturnType<typeof setTimeout>>();
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.unobserve(target);
      this.timers.set(
        target,
        setTimeout(() => {
          this.timers.delete(target);
          const width = (target as HTMLElement).offsetWidth;
          const height = (target as HTMLElement).offsetHeight;
          this.callback([{ target, contentRect: { width, height, x: 0, y: 0, top: 0, left: 0, right: width, bottom: height },
            borderBoxSize: [{ inlineSize: width, blockSize: height }],
            contentBoxSize: [{ inlineSize: width, blockSize: height }],
          } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
        }, 0),
      );
    }
    unobserve(target: Element) {
      clearTimeout(this.timers.get(target));
      this.timers.delete(target);
    }
    disconnect() {
      this.timers.forEach((timer) => clearTimeout(timer));
      this.timers.clear();
    }
  }
  class TransformMatrix {
    readonly m22: number;
    constructor(transform?: string) {
      const scale = transform?.match(/scale\(\s*([\d.]+)/)?.[1];
      this.m22 = scale ? Number(scale) : 1;
    }
  }
  replace(window, 'ResizeObserver', { value: MeasuredResizeObserver });
  replace(window, 'DOMMatrixReadOnly', { value: TransformMatrix });
  replace(HTMLElement.prototype, 'offsetWidth', {
    get(this: HTMLElement) {
      return Number.parseFloat(this.style.width) || 1;
    },
  });
  replace(HTMLElement.prototype, 'offsetHeight', {
    get(this: HTMLElement) {
      return Number.parseFloat(this.style.height) || 1;
    },
  });
  replace(SVGElement.prototype, 'getBBox', { value: () => ({ x: 0, y: 0, width: 0, height: 0 }) });
  return () => restore.reverse().forEach((reset) => reset());
}
