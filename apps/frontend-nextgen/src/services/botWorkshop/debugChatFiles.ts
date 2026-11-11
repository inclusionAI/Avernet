import type { SendFileRefs } from '@/pages/Workspace/hooks/useBotChat';
import type { SubmitContext } from '@tc-chat/ui/es/Sender';

export interface DebugChatRequest {
  content: string;
  options?: SendFileRefs;
}

export interface DebugChatMessageContent {
  text: string;
  fileNames: string[];
}

function decodeFileName(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

/** 把 SDK 消息中的 file-ref 标签转为可读附件名，避免在调试气泡中暴露协议标签。 */
export function parseDebugChatMessageContent(content: string): DebugChatMessageContent {
  const fileNames = Array.from(content.matchAll(/<file-ref\b[^>]*\bname=["']([^"']+)["'][^>]*>/gi)).map((match) =>
    decodeFileName(match[1]),
  );
  return {
    text: content.replace(/<file-ref\b[^>]*>(?:<\/file-ref>)?/gi, '').trim(),
    fileNames,
  };
}

/** 保留 Sender（及平台扩展）生成的正文与引用 ID，转换为聊天 SDK 参数。 */
export function buildDebugChatFileRequest(text: string, context?: SubmitContext): DebugChatRequest {
  const refs = context?.fileRefs;
  if (!refs?.length) return { content: text };
  return {
    content: context?.resolvedContent ?? text,
    options: {
      resourceReferences: refs.map((file) => ({
        type: 'file',
        resource_id: file.resource_id,
        insert_id: file.insert_id,
      })),
      promptFileRefs: refs.map((file) => ({ resource_id: file.resource_id, insert_id: file.insert_id })),
      fileRefDisplay: refs.map((file) => ({ insert_id: file.insert_id, name: file.display_name })),
    },
  };
}
