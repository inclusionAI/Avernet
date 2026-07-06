import type { PanelContentProps } from '@aix-chat/ui';

/**
 * PanelPlugin
 *
 * 描述一个副屏插件的元信息 + 渲染组件。
 * 业务方提供 npm 包时，在包内调用 registerPanel 传入此结构即可完成接入。
 */
export interface PanelPlugin {
  /** 唯一标识，对应 PanelTab.type（如 'dag' / 'search' / 'preview'） */
  type: string;
  /** 展示名称 */
  name: string;
  /** 副屏内容渲染组件 */
  component: React.ComponentType<PanelContentProps>;
}
