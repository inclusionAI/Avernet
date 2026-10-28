/**
 * A2A 客户端构建期默认开关的值形态源（spec stage2 §1）。
 * 单独成文件是评审要求：config.ts 的 umi 配置链无法在 jest 里驱动，
 * 值计算必须抽成可直测的纯函数。
 *
 * 输出形态契约（与 switch.ts 的双形态判定对齐）：
 *   env A2A_CLIENT_DEFAULT === 'true'  →  boolean true（umi define 收到
 *   JS 值后整体 JSON 化进 bundle，运行时即布尔 true；不再手拼字符串——
 *   旧实现 "'true'" 带引号字面量，经 define 转换后 runtime 是四字符
 *   "'true'" 字符串，恒不等于 'true'，灰度镜像实际全在跑 v3）；
 *   其余（未注入/false/任意串）→ boolean false，主线构建 off 语义不变。
 * 运行期 localStorage '0'/'1' 仍优先于构建期默认（现场回退通道）。
 */
export function a2aClientBuildDefault(
  env: { A2A_CLIENT_DEFAULT?: string } = process.env,
): boolean {
  return env.A2A_CLIENT_DEFAULT === 'true';
}