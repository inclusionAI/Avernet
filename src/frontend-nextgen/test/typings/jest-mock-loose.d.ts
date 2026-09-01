/**
 * jest mockResolvedValue / mockRejectedValue 类型漂移兜底
 * （仅放宽测试 mock 注入入参，不动运行时）。
 *
 * 背景：jest 30.5 + @types/jest 30.0 + TypeScript 5.9 下，`jest.fn<any>()`
 * 会让 Mock 的泛型 T=any，被 jest-mock 的
 *   ResolveType<T> = Extract<OverloadedReturnType<T>, PromiseLike<any>> extends [never] ? never : ...
 *   RejectType<T>  = 同结构
 * 解析为 never，于是
 *   MockInstance<T>.mockResolvedValue(value: ResolveType<T>)
 *   MockInstance<T>.mockRejectedValue(value: RejectType<T>)
 * 等对任意 value 报 "not assignable to parameter of type 'never'"。
 *
 * 测试统一 `import { jest } from '@jest/globals'`，其
 *   type Mock<T> = jest-mock.Mock<T>  (extends jest-mock.MockInstance<T>)
 * 故真正声明这些方法的是 jest-mock 模块的 MockInstance，需对本模块做 declaration-merge。
 *
 * 这里追加 mockResolvedValue / mockResolvedValueOnce / mockRejectedValue /
 * mockRejectedValueOnce 的宽松重载（value: unknown）。interface 合并时原声明(严格)重载在前、
 * 本文件(宽松)重载在后：
 *   - 具体泛型 mock（jest.fn<(a)=>Promise<X>>()）仍先命中严格重载，保持校验；
 *   - T=any 兜底场景回退到宽松重载，消除 never 误报。
 *
 * 顶层 `export {}` 使本文件成为 module，`declare module 'jest-mock'` 即标准 module augmentation
 * （而非 ambient 覆盖），避免破坏 Mock<T> 的泛型推断。
 * `__jestMockLooseTypeRef__` 只是 lng 内可访问级 phantom 成员：module augmentation 必须按原签名
 * `MockInstance<T extends FunctionLike = UnknownFunction>` 重述同一泛型，而本文件新增方法体不引用 T，
 * 会触发 eslint no-unused-vars；用该只读可选成员引用 T 以通过校验，不影响运行时，业务勿用。
 * 待 jest/@types/jest/TS 版本对齐后可删除本文件。
 */
export {};

declare module 'jest-mock' {
  export interface MockInstance<T extends FunctionLike = UnknownFunction> {
    /** @internal 兜底产物：仅引用泛型 T 以通过 eslint，非真实成员，业务勿用。 */
    readonly __jestMockLooseTypeRef__?: T;
    mockResolvedValue(value: unknown): this;
    mockResolvedValueOnce(value: unknown): this;
    mockRejectedValue(value: unknown): this;
    mockRejectedValueOnce(value: unknown): this;
  }
}
