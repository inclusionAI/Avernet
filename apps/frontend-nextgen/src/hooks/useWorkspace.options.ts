export interface UseWorkspaceOptions {
  /**
   * 副屏 onAction(send_message) 的自定义回流。
   *
   * 产线默认走 sendMessage → chat.onRequest（真实对话 provider）。
   * 自测面板可注入只 toast/log 的回调，不触碰真实对话链路，便于零对话依赖自测 send_message 回流。
   * fill_input 不在此注入（始终走本地 setDraft）。
   */
  onPanelSend?: (content: string) => void;
}
