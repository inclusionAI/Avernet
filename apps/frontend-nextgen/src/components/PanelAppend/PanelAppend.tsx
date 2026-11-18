/**
 * PanelAppend —— 副屏 append 模式渲染器。
 *
 * 由 `<AixUI type="panel" component="panel-append" tab='{id}' params='{mode:"append",blocks:[...]}'>`
 * 声明触发：SDK 按 `component="panel-append"` 走 panel-component → openBusinessPanel → 开 tab，本组件
 * 接收 `params.blocks`（有序累积数组），按序纵向堆叠渲染每个块。
 *
 * 块类型：
 * - `{ component: "taskPanel.TaskLoopView", params?: {...} }`：派发到 app-local 注册的副屏组件
 *   （`resolvePanelComponent`）；未注册回落纯文本。
 * - `{ content: "文本", label?: "步骤1" }`：纯文本块（保留换行，不做 Markdown 解析），纵向堆叠。
 *
 * append 语义：Bot 每步把"历史 + 本步"作为完整 `blocks` 重发 → openTab 同 id 替换 params →
 * 本组件按最新完整 blocks 渲染，旧块保留可见。replace 语义：只渲染最后一块（调用方通常只传一块）。
 *
 * 幂等重建（`reopenSidePaneTabsFromMessages`）对 panel-append 同 id 按 replace 边界 + append
 * 序列最长者重建，恢复完整累积序列（见 panelAppendRebuild.ts / sidePaneContent.ts）。
 */
import type { ComponentType } from 'react';
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { resolvePanelComponent } from '@/services/workspace/panelComponentRegistry';

/** 单个追加块。component 命中注册表则派发；否则按纯文本渲染。 */
export interface PanelAppendBlock {
  /** 应用副屏组件名（须已通过 registerPanelComponent 注册，如 taskPanel.TaskLoopView）。 */
  component?: string;
  /** 透传给解析组件的 params（与 `<AixUI params>` 同形）。 */
  params?: Record<string, unknown>;
  /** 纯文本内容（保留换行；component 缺省或未注册时渲染，不做 Markdown 解析）。 */
  content?: string;
  /** 可选块标签（不计入行为，仅展示起见）。 */
  label?: string;
}

/** PanelAppend 的 params 契约。 */
export interface PanelAppendParams extends Record<string, unknown> {
  /** 渲染模式：append 保留历史；缺省或 replace 只展示最后一块（向后兼容的替换语义）。 */
  mode?: 'append' | 'replace';
  /** 有序累积块数组；按序纵向堆叠。 */
  blocks: PanelAppendBlock[];
}

const BLOCK_STYLE: React.CSSProperties = {
  padding: '12px 16px',
  borderBottom: '1px solid rgba(0,10,26,0.06)',
};
const LABEL_STYLE: React.CSSProperties = {
  fontSize: 11,
  color: '#86909C',
  marginBottom: 4,
};
const CONTENT_STYLE: React.CSSProperties = {
  fontSize: 13,
  color: '#1D2129',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};
const EMPTY_STYLE: React.CSSProperties = {
  padding: 24,
  textAlign: 'center',
  color: '#86909C',
  fontSize: 13,
};

/** 单块渲染：命中注册组件则派发（透传 onAction/onInteraction/tab），否则纯文本回落。 */
function PanelAppendItem({ block, host }: { block: PanelAppendBlock; host: PanelContentProps }): React.ReactElement {
  const Comp: ComponentType<PanelContentProps> | undefined = block.component
    ? resolvePanelComponent(block.component)
    : undefined;
  if (Comp) {
    // 透传 host 的 onAction/onInteraction/tab/eventEmitter 给子块组件；params/payload 按 block 覆盖。
    const blockParams = (block.params ?? {}) as Record<string, unknown>;
    // 子块组件的 tab 身份应是组件自身而非 panel-append 宿主：任务类适配器按
    // `type==='taskPanel.TaskLoopView'` + `id==='task-<taskId>'` 登记/恢复导航状态。
    const taskId = typeof blockParams.taskId === 'string' && blockParams.taskId ? blockParams.taskId : undefined;
    const childTab = {
      ...host.tab,
      type: block.component as string,
      ...(taskId ? { id: `task-${taskId}` } : {}),
    };
    return <Comp {...host} tab={childTab} params={blockParams} payload={blockParams} />;
  }
  return (
    <div className="panel-append-block" style={BLOCK_STYLE}>
      {block.label ? <div className="panel-append-label" style={LABEL_STYLE}>{block.label}</div> : null}
      <div className="panel-append-content" style={CONTENT_STYLE}>{block.content ?? ''}</div>
    </div>
  );
}

export function PanelAppend(props: PanelContentProps): React.ReactElement {
  const p = (props.params ?? {}) as PanelAppendParams;
  const mode: 'append' | 'replace' = p.mode === 'append' ? 'append' : 'replace';
  const blocks = Array.isArray(p.blocks) ? p.blocks : [];
  // replace 是清空语义：即使调用方误传多块，也只展示最后一块（AC-4）。
  const visibleBlocks = mode === 'replace' ? blocks.slice(-1) : blocks;
  if (visibleBlocks.length === 0) {
    return <div className="panel-append-empty" style={EMPTY_STYLE}>暂无内容</div>;
  }
  return (
    <div className="panel-append" role="log" aria-live="polite" data-panel-append-mode={mode}>
      {visibleBlocks.map((block, idx) => (
        <PanelAppendItem key={idx} block={block} host={props} />
      ))}
    </div>
  );
}

export default PanelAppend;
