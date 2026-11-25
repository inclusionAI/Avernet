import type { SessionFileView } from '@/services/workspace/sessionFileService';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import { getPreviewKind, type SessionFilePreviewKind } from '@/services/workspace/sessionFileUtils';
import { useEffect, useState } from 'react';

export interface SessionFilePreviewState {
  kind: SessionFilePreviewKind;
  status: 'unsupported' | 'loading' | 'ready' | 'error';
  /** 供 <img> / <iframe> 标签直接加载的内容地址，部署态优先直连网关。 */
  contentUrl: string | null;
  /** 兼容共享预览组件的自绘文本字段；群文件由标签加载，此字段保持 null。 */
  text: string | null;
  /** 自绘文本截断标记；群文件标签预览保持 false。 */
  truncated: boolean;
  errorMessage: string | null;
}

const initialState: SessionFilePreviewState = {
  kind: 'other',
  status: 'unsupported',
  contentUrl: null,
  text: null,
  truncated: false,
  errorMessage: null,
};

/** 构造群文件的浏览器预览地址，不在 JavaScript 中读取跨域文件内容。 */
export function useSessionFilePreview(file: SessionFileView | null): SessionFilePreviewState {
  const [state, setState] = useState<SessionFilePreviewState>(initialState);
  const fileId = file?.fileId ?? null;
  const sessionId = file?.sessionId ?? null;
  const name = file?.name ?? '';
  const mimeType = file?.mimeType ?? '';

  useEffect(() => {
    if (!fileId || !sessionId) {
      setState(initialState);
      return;
    }
    const kind = getPreviewKind(name, mimeType);
    if (kind === 'other') {
      setState({ ...initialState, kind });
      return;
    }
    const contentUrl = sessionFileService.buildContentUrl(sessionId, fileId);
    // 文本也交给 iframe 导航加载：show=true 仍可能 302 到存储；fetch 读取会受最终响应 CORS 限制。
    setState({ ...initialState, kind, status: 'ready', contentUrl });
  }, [fileId, sessionId, name, mimeType]);

  return state;
}
