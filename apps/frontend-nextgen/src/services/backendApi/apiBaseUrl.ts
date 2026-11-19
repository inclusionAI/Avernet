// src/services/backendApi/apiBaseUrl.ts
//
// 请求 URL 变体接缝:Open Core 默认恒等(相对路径原样发出,由 dev server / 部署代理转发);
// 内部 Overlay 在 boot(`@/extensions` internal.ts)经 `setApiUrlResolver` 注入绝对地址改写规则。
// 约束:resolver 只改 origin/前缀,不得吞并 query;调用方(injectUserId 等 URL 语义判定)
// 仍在原始相对 URL 上进行——两通道(httpClient / umi request)共用本 seam,保证规则单点。

export type ApiUrlResolver = (url: string) => string;

const identity: ApiUrlResolver = (url) => url;

let resolver: ApiUrlResolver = identity;

/** 应用当前 resolver(未注册时恒等)。 */
export const resolveApiUrl = (url: string): string => resolver(url);

/** 注册/复位 resolver;传 null 恢复恒等(Open Core 与 jest 缺省态)。 */
export function setApiUrlResolver(next: ApiUrlResolver | null): void {
  resolver = next ?? identity;
}
