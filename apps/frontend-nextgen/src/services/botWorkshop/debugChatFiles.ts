import type { SendFileRefs } from '@/pages/Workspace/hooks/useBotChat';

export interface DebugChatFileReference {
  resourceId: string;
  displayName: string;
}

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

/** 将会话文件转换为 SDK 识别的 file-ref 与资源引用参数。 */
export function buildDebugChatFileRequest(text: string, files: DebugChatFileReference[]): DebugChatRequest {
  if (files.length === 0) return { content: text };

  const references = files.map((file, index) => ({
    ...file,
    insertId: `debug_file_${index + 1}`,
  }));

  return {
    content: `${references.map((file) => `<file-ref insert_id="${file.insertId}"></file-ref>`).join('')}${text}`,
    options: {
      resourceReferences: references.map((file) => ({
        type: 'file',
        resource_id: file.resourceId,
        insert_id: file.insertId,
      })),
      promptFileRefs: references.map((file) => ({
        resource_id: file.resourceId,
        insert_id: file.insertId,
      })),
      fileRefDisplay: references.map((file) => ({
        insert_id: file.insertId,
        name: file.displayName,
      })),
    },
  };
}
