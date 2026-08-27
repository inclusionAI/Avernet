/**
 * 单聊桥路径 buildRequestParams——把 aixcore 卡片 `bridge.sendMessage(content)` 转成
 * `chat.onRequest` 期望的请求参数（顶层 content + targetId + userMessage）。
 *
 * 从 useWorkspace 抽出为纯函数：控 useWorkspace ≤250 行门禁（design D1 / O4）。
 *
 * 根因 A：SDK 默认 buildRequestParams 仅产 `{...extra, userMessage:{content}}`（无顶层 content）→
 *   teamclaw 单聊 provider（botChatProvider:174 / supportProvider:152 读 params.content → query）→
 *   query=undefined → ws 帧 message 丢。显式产顶层 content 修复。
 *
 * 返回**统一形态**（非按活跃方分支）：
 *   useWorkspace 的 useChatBridge TInput 经 `chat: isSupportTarget ? chat : botChat.chat` 推断为
 *   `SupportChatRequest | never` = `SupportChatRequest`（support provider 真型 / bot provider `as never`）。
 *   故 buildRequestParams 返回须满足 SupportChatRequest（content + targetId 均为 required string）。
 *   统一含 targetId：support 路径 provider 验证用（supportProvider.request:153 比对 targetId）；
 *   bot 路径 provider（BotChatProvider.request:174）不读 targetId，'' 无害。
 *   support 模式下 isSupportTarget ⟹ activeTargetId === TEST_USER_SUPPORT_TARGET_ID（已设），故 `?? ''` 不触发。
 *
 * panelContext 不在此透传：useChat.onRequest 内部会再次 withPanelContext(params) 注入
 * （useChat.ts:138-248），桥路径 build 的输出经此 rescued，single-chat 不丢 panelContext。
 */
export function buildSingleChatBridgeRequest(activeTargetId: string | null, content: string) {
  const displayTime = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  return {
    content,
    targetId: activeTargetId ?? '',
    userMessage: { content, extra: { displayTime } },
  };
}
