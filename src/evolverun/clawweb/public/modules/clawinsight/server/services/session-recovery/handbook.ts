/** Small, versioned advice selected by code; no model-generated intervention. */
export function recoveryAdvice(code: string | null, deliveryKey: string) {
  const common = "请检查当前任务刚才的失败，结合实际错误自查；可以恢复时继续原任务。不要仅宣称成功，请以实际调用结果确认。若需要权限、用户输入或其他外部条件，明确说明阻塞原因，不要绕过权限或反复重试。";
  const mcp = "如涉及 MCP：mcporter 是通过 shell 执行的命令，按实际帮助和工具列表核对命令、tool 名称及参数，不要编造，不要改用 OC CLI 代替 mcporter。区分传输成功和工具业务成功；429 应遵循 Retry-After 或退避，权限不足应如实说明。";
  return { handbookVersion: "session-recovery-handbook/v1",
    message: `[会话自愈：${deliveryKey}]\n${common}${code?.startsWith("TC.MCP.") ? "\n" + mcp : ""}` };
}
