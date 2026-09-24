interface GroupChatAbortErrorShape extends Error {
  code: string;
  abortedRunIds: string[];
  failures?: Array<{ code?: string }>;
}

export interface GroupChatAbortErrorView {
  message: string;
  partial: boolean;
  abortedCount: number;
  restartRequired: boolean;
}

function isAbortError(error: unknown): error is GroupChatAbortErrorShape {
  return (
    error instanceof Error &&
    typeof (error as Partial<GroupChatAbortErrorShape>).code === 'string' &&
    Array.isArray((error as Partial<GroupChatAbortErrorShape>).abortedRunIds)
  );
}

/** 将协议错误收敛为可展示文案，避免向 UI 泄漏原始响应、连接地址或堆栈。 */
export function toGroupChatAbortErrorView(error: unknown): GroupChatAbortErrorView {
  if (!isAbortError(error)) {
    return {
      message: '终止失败，请检查网络后重试',
      partial: false,
      abortedCount: 0,
      restartRequired: false,
    };
  }

  const abortedCount = error.abortedRunIds.length;
  const restartRequired =
    error.code === 'chat_abort_not_supported' ||
    error.failures?.some((failure) => failure.code === 'chat_abort_not_supported') === true;
  if (error.code === 'chat_abort_partial_failure') {
    return {
      message: '部分输出终止失败，可稍后重试',
      partial: true,
      abortedCount,
      restartRequired,
    };
  }

  const messages: Record<string, string> = {
    unauthorized: '登录状态已失效，请重新登录后重试',
    forbidden: '你当前无权终止该会话中的输出',
    session_not_subscribed: '当前会话尚未连接，请重新进入会话后重试',
    token_scope_mismatch: '会话连接已失效，请重新进入会话后重试',
    client_scope_mismatch: '当前会话已切换，请在新会话中重试',
    chat_abort_timeout: '终止请求超时，请稍后重试',
    chat_abort_disconnected: '群聊连接已断开，请重连后重试',
    chat_abort_send_failed: '终止请求发送失败，请检查网络后重试',
    chat_abort_not_supported: '当前 Bot 使用的插件版本不支持终止输出',
  };
  return {
    message: messages[error.code] ?? '终止失败，请稍后重试',
    partial: false,
    abortedCount,
    restartRequired,
  };
}
