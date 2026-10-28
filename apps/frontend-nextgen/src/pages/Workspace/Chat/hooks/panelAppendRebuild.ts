/**
 * panel-append 重建的纯解析层（不导入 SDK，便于单测）。
 *
 * 从会话消息中收集 `component="panel-append"` 的副屏声明，按 tabId 取 `params.blocks` 最长者为
 * 重建候选——支持 Bot 非单调发送（replace 穿插、缺步）下仍恢复完整累积序列。replace 消息
 * `blocks` 退化为单块后，其长度小于历史最长 → 不会被选为 winner，故历史保留；当 replace 为
 * 最新且为唯一最大长度时按其（单块）重建。
 */
import type { ChatMessage } from '@tc-chat/core';
import type { PanelAppendParams } from '@/components/PanelAppend';

export interface ParsedAppendTag {
  tabId: string;
  title: string;
  closable: boolean;
  defaultWidth?: number;
  params: PanelAppendParams;
  body: string;
}

const PANEL_APPEND_TAG_RE = /<AixUI\b([^>]*)>(?:([\s\S]*?)<\/AixUI>)?/gi;
const DEFAULT_TAB_ID = 'panel-append';

function extractAttr(attrsText: string, name: string): string | undefined {
  // 单引号 / 双引号 / 无引号；无引号值遇到空白或 '>' 结束。
  const re = new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s>]+))`, 'i');
  const m = re.exec(attrsText);
  if (!m) return undefined;
  return m[1] ?? m[2] ?? m[3];
}

function parseTab(rawTab: string | undefined): { id?: string; title?: string; closable?: boolean; defaultWidth?: number } {
  if (!rawTab) return {};
  try {
    const o = JSON.parse(rawTab);
    if (o && typeof o === 'object') return o as { id?: string; title?: string; closable?: boolean; defaultWidth?: number };
  } catch {
    // 忽略坏 JSON
  }
  return {};
}

function parseParams(rawParams: string | undefined): PanelAppendParams | undefined {
  if (!rawParams) return undefined;
  try {
    const o = JSON.parse(rawParams);
    if (o && typeof o === 'object' && Array.isArray(o.blocks)) return o as PanelAppendParams;
  } catch {
    // 忽略坏 JSON
  }
  return undefined;
}

/**
 * 收集 panel-append 声明，按 tabId 取 blocks 最长者。返回的候选可直接驱动 openBusinessPanel 重建。
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
      if (extractAttr(attrsText, 'component') !== 'panel-append') continue;
      const tab = parseTab(extractAttr(attrsText, 'tab'));
      const params = parseParams(extractAttr(attrsText, 'params'));
      if (!params) continue;
      const tabId = (typeof tab.id === 'string' && tab.id) ? tab.id : DEFAULT_TAB_ID;
      const blocksLen = Array.isArray(params.blocks) ? params.blocks.length : 0;
      const body = (match[2] ?? '').trim();
      const existing = winners.get(tabId);
      const existingLen = existing && Array.isArray(existing.params.blocks) ? existing.params.blocks.length : -1;
      if (!existing || blocksLen >= existingLen) {
        winners.set(tabId, {
          tabId,
          title: (typeof tab.title === 'string' && tab.title) ? tab.title : '副屏',
          closable: typeof tab.closable === 'boolean' ? tab.closable : true,
          ...(typeof tab.defaultWidth === 'number' ? { defaultWidth: tab.defaultWidth } : {}),
          params,
          body,
        });
      }
    }
  }
  return [...winners.values()];
}
