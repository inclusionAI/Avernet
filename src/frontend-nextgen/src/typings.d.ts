// 位图资源导入声明（PNG 等）。运行时由 bigfish/umi 资产管线处理，import 得到产物 URL。
// 注：src/.umi/typings.d.ts 亦有同款声明，但根 tsconfig exclude 了 src/.umi/*，不生效。
declare module '*.png' {
  const src: string;
  export default src;
}
