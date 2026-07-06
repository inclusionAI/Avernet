/**
 * 将 API 返回的 GroupMessageResponse 转换为前端 GroupMessage 格式
 *
 * 包含从 metadata 构建 tool_execution blocks 的逻辑，
 * 供 useGroups 和 useGroupSessions 共用。
 */

import type { GroupMessage } from '@/pages/GroupChat/types';
import { BCS_SYSTEM_MESSAGE_BOT_UUID } from '@/pages/GroupChat/types';
import type { GroupMessageResponse } from '@/services/backend-api/BcnController';

/**
 * 从 metadata 构建 tool_execution blocks
 */
function buildBlocksFromMetadata(
  metadata: GroupMessageResponse['metadata'],
): any[] | undefined {
  if (!metadata) return undefined;

  // 情况1: tool_calls 数组格式（工具调用请求）
  if (metadata.tool_calls?.length) {
    const toolData = metadata.tool_calls[0];
    return [
      {
        type: 'tool_execution',
        steps: [
          {
            id: toolData?.tool_call_id,
            tool: toolData?.tool_name,
            title: toolData?.tool_name,
            status: 'pending',
            input: toolData?.arguments
              ? JSON.stringify(toolData?.arguments || {}, null, 2)
              : undefined,
          },
        ],
      },
    ];
  }

  // 情况2: tool_call_id/tool_name 直接字段格式（工具调用结果）
  if (metadata.tool_call_id && metadata.tool_name) {
    return [
      {
        type: 'tool_execution',
        steps: [
          {
            id: metadata.tool_call_id,
            tool: metadata.tool_name,
            title: metadata.tool_name,
            status: metadata.is_error ? 'error' : 'success',
            input: metadata.arguments
              ? JSON.stringify(metadata.arguments, null, 2)
              : undefined,
            output:
              metadata.result !== null
                ? typeof metadata.result === 'string'
                  ? metadata.result
                  : JSON.stringify(metadata.result, null, 2)
                : undefined,
          },
        ],
      },
    ];
  }

  return undefined;
}

/**
 * 转换 API 消息响应为前端 GroupMessage 格式
 */
export function transformMessageData(
  msgs: GroupMessageResponse[],
): GroupMessage[] {
  return msgs.map((msg) => {
    const role =
      msg.role === 'tool_result'
        ? 'toolResult'
        : (msg.role as GroupMessage['role']);
    const senderName = msg.bot_name || msg.sender;
    const blocks = buildBlocksFromMetadata(msg.metadata) || msg.blocks;

    // 系统消息: 用固定 bot_uuid 作为识别 key, 不依赖 sender 字段(后端 sender 可能是 'user')
    const isSystem = role === 'system';
    const botUuid = isSystem ? BCS_SYSTEM_MESSAGE_BOT_UUID : msg.sender;

    return {
      id: msg.id,
      content: msg.content,
      sender: {
        id: msg.sender,
        name: senderName,
        type: (msg.message_type || role) as GroupMessage['sender']['type'],
        botUuid,
      },
      botUuid,
      botName: msg.bot_name,
      timestamp: msg.timestamp,
      status: 'sent',
      role,
      blocks,
      // conversationRoundId 优先用 historyMeta，fallback 到 run_id
      conversationRoundId: msg.historyMeta?.conversationRoundId || msg.run_id,
      isToolResult: msg.role === 'tool_result',
      runId: msg.run_id,
      metadata: msg.metadata,
    };
  });
}
