// AixUI 副屏内容判定与幂等重建（chat 侧 useChatHeaderPanels 与群侧 useGroupChatHeaderPanels
// 共用的纯函数层）：从 useChatHeaderPanels 下沉为独立纯模块（工程红线：Hook 单文件 ≤250 行），
// 语义与拆分前一一对应，无行为变化。
import type { ChatMessage, PanelHandle, PanelTab } from '@tc-chat/core';
import { openBusinessPanel } from '@tc-chat/ui/es/MarkdownRender/plugins/components/businessUtils';
import {
  buildBusinessData,
  extractTagAttr,
  resolveAixUi,
  type AixUiRawAttrs,
} from '@tc-chat/ui/es/utils/aixTags';

import { chooseAppendWinners } from './panelAppendRebuild';

/**
 * AixUI 副屏标记：显式 type=panel（双引号/单引号/无引号均命中，引擎与 BCS 输出引号风格不一），
 * 或无显式 type 时的旧版 panel= 属性（对齐 @tc-chat/ui resolveAixUi 的位置轴：
 * panel-component/panel-card）。[^>] 含换行，开标签跨行属性同样可命中。
 */
const AIX_UI_PANEL_TAG_RE = /<AixUI[^>]*\stype=["']?panel(?=["'\s>])|<AixUI[^>]*\spanel=["']?/;

/**
 * 会话已载入消息中是否有副屏内容。
 * PanelHandle 无 tabs 查询；空 tabs 的 openPanel 会被 SDK autoCloseWhenEmpty 立即弹回
 * （面板闪一下即收回，观感即「点了没反应」），故 Monitor 开启动作以此为判空依据——
 * 消息含副屏标记时其渲染器挂载即 openTab，tabs 必然存在，openPanel 不会弹回。
 */
export function sessionHasSidePaneContent(messages: ChatMessage[]): boolean {
  return messages.some((message) => typeof message.content === 'string' && AIX_UI_PANEL_TAG_RE.test(message.content));
}

/** AixUiComponent 的 rawAttrs 属性白名单。 */
const AIX_UI_ATTR_NAMES = ['type', 'cardId', 'component', 'params', 'payload', 'tab', 'panel', 'cdn', 'entry'] as const;

/** SDK panel-card 模式的 tab.type 常量（AixUiComponent CARD_PANEL_KEY）。 */
const CARD_PANEL_KEY = 'aix-card-panel';

/**
 * 按消息里的副屏声明幂等重建副屏 tabs（chat/群两页 Monitor「打开」共用）。
 *
 * 背景：切会话 closePanelForce 与渲染器「挂载即 openTab」存在时序竞态——父组件 effect
 * 晚于子组件 effect 执行，刚还原的 tab 会被抹掉且渲染器不再重试；此时裸 openPanel()
 * 只会被 SDK autoCloseWhenEmpty 立即弹回，表现为「打开一瞬又关闭」。
 *
 * 解法：Monitor 打开改为走消息标记 openTab 幂等重建——openTab 同 id 静默合并
 * （不重挂内容、不抢焦点），tab 缺失时原样重建，且 openTab 自动展开面板、
 * 重建后 tab 必在，autoCloseWhenEmpty 不会再弹回。返回是否开出至少一个 tab。
 */
export function reopenSidePaneTabsFromMessages(messages: ChatMessage[], panel: PanelHandle): boolean {
  let openedAny = false;
  for (const message of messages) {
    const content = message.content;
    if (typeof content !== 'string' || !/<aixui/i.test(content)) continue;
    const openTagRe = /<AixUI\b([^>]*)>/gi;
    let match: RegExpExecArray | null;
    while ((match = openTagRe.exec(content)) !== null) {
      const rawAttrsText = match[1] ?? '';
      // 自闭合 <AixUI ... />：无标签体（『/』是属性串最后一个非空白字符）。
      const closingSlashIdx = rawAttrsText.trimEnd().endsWith('/') ? rawAttrsText.length - 1 : -1;
      const selfClosing = closingSlashIdx !== -1;
      const attrsText = selfClosing ? rawAttrsText.slice(0, closingSlashIdx) : rawAttrsText;
      const bodyStart = match.index + match[0].length;
      let body = '';
      if (!selfClosing) {
        // 成对标签截 body（MarkdownRender lexer 走 lexer.blockTokens，这里简化为取直接文本）；未闭合为流式中态，跳过。
        const closeIdx = content.toLowerCase().indexOf('</aixui>', bodyStart);
        if (closeIdx === -1) continue;
        body = content.slice(bodyStart, closeIdx).trim();
      }
      const rawAttrs: AixUiRawAttrs = {};
      for (const name of AIX_UI_ATTR_NAMES) {
        const value = extractTagAttr(attrsText, name);
        if (value !== undefined) rawAttrs[name] = value;
      }
      const resolved = resolveAixUi(rawAttrs);
      if (resolved.mode === 'panel-card' && resolved.cardId) {
        // 与 PanelCardTrigger 同款 payload：{cardId, params, data(标签体 JSON)}。
        const data = buildBusinessData(resolved.params, resolved.payload) as Record<string, unknown>;
        if (body) {
          try {
            data.content = JSON.parse(body);
          } catch {
            data.content = body;
          }
        }
        const payload = { cardId: resolved.cardId, params: resolved.params ?? {}, data: data.content };
        panel.openTab({
          id: resolved.tab.id || resolved.cardId,
          type: CARD_PANEL_KEY,
          title: resolved.tab.title ?? resolved.cardId,
          closable: resolved.tab.closable ?? false,
          ...(Object.prototype.hasOwnProperty.call(resolved.tab, 'defaultWidth')
            ? // TabConfig.defaultWidth 为 unknown（运行时由 ChatLayout.openTab 归一化），与 PanelCardTrigger 同形透传。
              { defaultWidth: resolved.tab.defaultWidth as PanelTab['defaultWidth'] }
            : {}),
          params: payload,
          payload,
        });
        openedAny = true;
      } else if (resolved.mode === 'panel-component' && resolved.component && resolved.component !== 'panel-append') {
        // 与 PanelComponentTrigger 同款：命令式 openBusinessPanel（tab.id 缺省取 component）。
        openBusinessPanel(panel, {
          component: resolved.component,
          cdn: resolved.cdn,
          entry: resolved.entry,
          params: resolved.params,
          payload: resolved.payload,
          tab: resolved.tab,
          children: body || undefined,
        });
        openedAny = true;
      }
    }
  }
  // panel-append 重建：对 component="panel-append" 的同 id 取 params.blocks 最长者为候选（跳过
  // 上方 panel-component 即时开 tab 分支，避免双开），按 winner.params 一次开 tab 恢复完整累积序列。
  const appendWinners = chooseAppendWinners(messages);
  for (const winner of appendWinners) {
    openBusinessPanel(panel, {
      component: 'panel-append',
      params: winner.params,
      tab: {
        id: winner.tabId,
        title: winner.title,
        closable: winner.closable,
        ...(winner.defaultWidth !== undefined ? { defaultWidth: winner.defaultWidth as PanelTab['defaultWidth'] } : {}),
      },
      children: winner.body || undefined,
    });
    openedAny = true;
  }
  return openedAny;
}