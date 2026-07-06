/**
 * @ext 注入入口 —— 开源构建（默认）。
 *
 * 开源构建没有内部扩展，本文件刻意为空。
 * 内部构建（UMI_ENV=internal）经 config.internal.ts 把 `@ext` alias 指向
 * `src/internal/index.ts`，在那里 import 各模块的 extension 并 extend。
 *
 * 应用启动入口（app.tsx）`import '@ext'` 后调用 sealExtensions()，
 * 此处空 = 所有契约保持开源默认值。
 */
export {};
