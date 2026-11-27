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

// jsdom 未实现 structuredClone（@dicebear/core v11 内部使用）；真实 Node/浏览器环境均有，仅测试补桩。
if (typeof globalThis.structuredClone !== 'function') {
  const utilClone = (() => {
    try {
      return require('util').structuredClone;
    } catch {
      return undefined;
    }
  })();
  // jsdom 沙箱里 require('util') 可能拿不到完整 builtin，退化为 JSON 克隆
  // （dicebear 克隆的是纯 JSON 风格/选项数据，语义等价）。
  globalThis.structuredClone =
    typeof utilClone === 'function' ? utilClone : (value) => JSON.parse(JSON.stringify(value));
}
