/**
 * jest 30 mock 返回值宽松兜底（存量测试兼容）。
 *
 * jest 30 严格化了 `ResolveType<T>` / `RejectType<T>`(见 `jest-mock` 包 `MockInstance`)：
 * 当 `jest.fn()` / `jest.fn<any>()` 未显式声明 Promise 返回类型时，`mockResolvedValue`
 * 系列入参被收窄为 `never`，使项目存量写法 `jest.fn<any>().mockResolvedValue(X)`
 * / `jest.fn().mockResolvedValue(X)` 大面积报 TS2345（jest 29→30 行为变更）。
 *
 * 本补丁在保留 `jest-mock` 原生严格重载（更具体、TS 按声明顺序先匹配）的前提下，
 * 追加 `value: unknown` 兜底重载：未类型化/不匹配的 mock 调用回退到 `unknown`
 * 入参（等价恢复 jest 29 宽松语义）；严格重载在声明合并顺序中先命中，故只兜住其
 * 未覆盖的剩余调用，不削弱任何已显式类型化 mock 的返回值检查。
 *
 * 仅作用于 mockResolvedValue/mockResolvedValueOnce/mockRejectedValue(Once)；
 * mockReturnValue / mockImplementation 等仍维持 jest-mock 原生类型。
 */
declare module 'jest-mock' {
  interface MockInstance<T extends FunctionLike = UnknownFunction> {
    mockResolvedValue(value: unknown | T): this;
    mockResolvedValueOnce(value: unknown | T): this;
    mockRejectedValue(value: unknown | T): this;
    mockRejectedValueOnce(value: unknown | T): this;
  }
}
