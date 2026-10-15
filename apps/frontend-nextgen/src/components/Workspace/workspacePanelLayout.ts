/** 聊天右侧临时面板共用宽度口径；新 key 避免历史面板各自的旧宽度覆盖统一默认值。 */
export const WORKSPACE_RIGHT_PANEL_LAYOUT = {
  minWidth: 320,
  maxWidth: 600,
  defaultWidth: 380,
  storageKey: 'teamclaw:workspace-right-panel-width',
} as const;
