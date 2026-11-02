// jsdom 全局补桩（jest.setup Files 引入）：Radix Popper/Tooltip 的 Arrow 尺寸探测走
// @radix-ui/react-use-size 的 ResizeObserver，jsdom 未实现——unify-tooltip-antd 给共享
// Tooltip 加箭头后所有渲染气泡的组件测试都会踩中，故收口为全局桩（此前是逐文件
// beforeEach 复制粘贴，BotTable.test 等先例）。node 环境无 window，安全跳过。
if (typeof window !== 'undefined' && typeof window.ResizeObserver === 'undefined') {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  window.ResizeObserver = ResizeObserverMock;
  globalThis.ResizeObserver = ResizeObserverMock;
}
