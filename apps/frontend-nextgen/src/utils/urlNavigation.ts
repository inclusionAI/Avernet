// src/utils/urlNavigation.ts
//
// Replace 导航（不留在历史栈）。独立小模块导出：jsdom 的 window.location 是
// LegacyUnforgeable（不可 spyOn/defineProperty），跳转收敛于此，单测经
// jest.mock 整模块替换即可断言「跳了/没跳」。
// 消费方：shell/LegacyVersionSwitch（跨版本切流「返回旧版」）。

/** replace 语义跳转：防后退键在新入口反复触发切流回退。 */
export function redirectReplace(url: string): void {
  window.location.replace(url);
}