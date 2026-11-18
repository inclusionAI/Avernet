/**
 * panel-append 重建的纯解析层。
 *
 * 从会话消息中收集 `component="panel-append"` 的副屏声明，按 tabId 选择重建候选：
 * - 显式 `mode:"replace"`（或未声明 mode，按 AC-2 等价 replace）是序列边界：它清空此前的累积，
 *   之后的 append 从新序列重新累积；因此 replace 之后重建的是这一条（单块最新）。
 * - append 序列内取 `params.blocks` 最长者为 winner：Bot 正常每步重发完整历史时最长即最新，
 *   同时容忍非单调/缺步发送，不把历史折叠为最后一条。
 *
 * 属性取值/JSON 解析复用 SDK 的 `extractTagAttr` + `parseAixJson`，与 live 渲染共用同一套
 * HTML 实体反解与宽松 JSON 语义，避免重建与展示对同一条消息得出不同结果。
 */
import type { ChatMessage } from '@tc-chat/core';
import { extractTagAttr, isPlainObject, parseAixJson } from '@tc-chat/ui/es/utils/aixTags';
import type { PanelAppendParams } from '@/components/PanelAppend';

export interface ParsedAppendTag {
  tabId: string;
  title: string;
  closable: boolean;
  defaultWidth?: number;
  mode: 'append' | 'replace';
  params: PanelAppendParams;
  body: string;
}

const PANEL_APPEND_TAG_RE = /<AixUI\b([^>]*)>(?:([\s\S]*?)<\/AixUI>)?/gi;
const DEFAULT_TAB_ID = 'panel-append';

function parseTab(rawTab: string | undefined): { id?: string; title?: string; closable?: boolean; defaultWidth?: number } {
  const parsed = parseAixJson(rawTab);
  if (!isPlainObject(parsed)) return {};
  return parsed as { id?: string; title?: string; closable?: boolean; defaultWidth?: number };
}

function parseParams(rawParams: string | undefined): PanelAppendParams | undefined {
  const parsed = parseAixJson(rawParams);
  if (!isPlainObject(parsed) || !Array.isArray(parsed.blocks)) return undefined;
  return parsed as PanelAppendParams;
}

/** 未显式声明 `mode:"append"` 的声明按 replace 处理（AC-2：opt-in，缺省保持既有替换语义）。 */
function resolveMode(params: PanelAppendParams): 'append' | 'replace' {
  return params.mode === 'append' ? 'append' : 'replace';
}

/**
 * 收集 panel-append 声明，按 tabId 选重建候选。返回的候选可直接驱动 openBusinessPanel 重建。
 */
export function chooseAppendWinners(messages: ChatMessage[]): ParsedAppendTag[] {
  const winners = new Map<string, ParsedAppendTag>();
  for (const message of messages) {
    const content = message.content;
    if (typeof content !== 'string' || !/<AixUI[^>]*\scomponent=["']?panel-append/i.test(content)) continue;
    PANEL_APPEND_TAG_RE.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = PANEL_APPEND_TAG_RE.exec(content)) !== null) {
      const attrsText = match[1] ?? '';
      if (extractTagAttr(attrsText, 'component') !== 'panel-append') continue;
      const tab = parseTab(extractTagAttr(attrsText, 'tab'));
      const params = parseParams(extractTagAttr(attrsText, 'params'));
      if (!params) continue;
      const tabId = (typeof tab.id === 'string' && tab.id) ? tab.id : DEFAULT_TAB_ID;
      const blocksLen = Array.isArray(params.blocks) ? params.blocks.length : 0;
      const mode = resolveMode(params);
      const body = (match[2] ?? '').trim();
      const existing = winners.get(tabId);
      const existingLen = existing && Array.isArray(existing.params.blocks) ? existing.params.blocks.length : -1;
      // replace 是序列边界：它本身替代旧序列；其后的 append 再从新序列累积。
      // append 序列内保留最长者，兼顾正常“每步重发完整历史”与非单调/缺步发送。
      const shouldReplace =
        !existing || mode === 'replace' || existing.mode === 'replace' || blocksLen >= existingLen;
      if (shouldReplace) {
        winners.set(tabId, {
          tabId,
          title: (typeof tab.title === 'string' && tab.title) ? tab.title : '副屏',
          closable: typeof tab.closable === 'boolean' ? tab.closable : true,
          ...(typeof tab.defaultWidth === 'number' ? { defaultWidth: tab.defaultWidth } : {}),
          mode,
          params,
          body,
        });
      }
    }
  }
  return [...winners.values()];
}
