import { resolveTaskPanelDefaultWidth } from '@/services/workspace/panelWidthMemory';
import type { PanelAppendBlock, PanelAppendParams } from '@/components/PanelAppend';

/**
 * 构造副屏 append 模式声明式 <AixUI type="panel"> 消息串。
 *
 * 与 `buildTaskPanelAixUI` 同形：tab id 去重——append 下 Bot 每步把"历史 + 本步"作为完整 `blocks`
 * 重发，同 id 经 openTab 替换 params → PanelAppend 按最新完整 blocks 渲染、历史块保留可见。
 * replace 模式调用方只传单块 `blocks`（最新内容）。
 *
 * @param tabId    副屏 tab id（同 id 即同一副屏；Bot 多步对同一副屏 append 时保持不变）
 * @param title    tab 标题（用户可见；剔除会破坏 JSON/单引号 HTML 属性的字符）
 * @param blocks   有序累积块数组（append 传完整历史；replace 传单块最新）
 * @param opts     可选 closable / defaultWidth
 */
export function buildPanelAppendAixUI(
  tabId: string,
  title: string,
  blocks: PanelAppendBlock[],
  opts: { closable?: boolean; defaultWidth?: number } = {},
): string {
  const safeTitle = (title ?? '').replace(/[<>'"`]/g, ' ').trim() || '副屏';
  const tab = JSON.stringify({
    id: tabId,
    title: safeTitle,
    closable: opts.closable ?? true,
    defaultWidth: opts.defaultWidth ?? resolveTaskPanelDefaultWidth(),
  });
  const params: PanelAppendParams = { mode: 'append', blocks };
  const paramsAttr = JSON.stringify(params);
  return (
    `<AixUI type="panel" component="panel-append" ` +
    `tab='${tab}' params='${paramsAttr}'></AixUI>`
  );
}
