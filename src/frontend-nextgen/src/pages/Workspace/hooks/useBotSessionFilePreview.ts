/** useBotSessionFilePreview — 单聊会话文件预览，与群聊共用 PreviewPane(contentUrl)。 */
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { botSessionFileService } from '@/services/workspace/botSessionFileService';
import { getPreviewKind, type SessionFilePreviewKind } from '@/services/workspace/sessionFileUtils';
import { useEffect, useState } from 'react';

export interface BotSessionFilePreviewState {
  kind: SessionFilePreviewKind;
  status: 'unsupported' | 'loading' | 'ready' | 'error';
  /** 供 <img> / <iframe> 标签直接加载的网关内容地址。 */
  contentUrl: string | null;
  errorMessage: string | null;
}

const initialState: BotSessionFilePreviewState = {
  kind: 'other',
  status: 'unsupported',
  contentUrl: null,
  errorMessage: null,
};

export interface UseBotSessionFilePreviewParams {
  botId: string | null;
  sessionId: string | null;
  userId: string | null;
  ownerId?: string;
}

export function useBotSessionFilePreview(
  file: BotSessionFileView | null,
  params: UseBotSessionFilePreviewParams,
): BotSessionFilePreviewState {
  const [state, setState] = useState<BotSessionFilePreviewState>(initialState);
  const resourceId = file?.resourceId ?? null;
  const name = file?.displayName ?? '';
  const { botId, sessionId, userId, ownerId } = params;

  useEffect(() => {
    if (!resourceId || !botId || !sessionId || !userId) {
      setState(initialState);
      return;
    }
    const kind = getPreviewKind(name);
    if (kind === 'other') {
      setState({ ...initialState, kind });
      return;
    }
    const contentUrl = botSessionFileService.resolveContentUrl(botId, sessionId, resourceId, userId, ownerId, 'inline');
    setState({ ...initialState, kind, status: 'ready', contentUrl });
  }, [resourceId, name, botId, sessionId, userId, ownerId]);

  return state;
}
