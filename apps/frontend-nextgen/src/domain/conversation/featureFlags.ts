// 对话域能力开关。
//
// 历史消息检索（单聊 ChatHeaderActions 与群聊 GroupHeader 两个入口共用同一套能力：
// SessionHistorySearchPanel + useChatHeaderPanels.historySearch，数据源为本地已载入消息；
// 后端检索接口尚未就绪，故该能力目前不完整）。在能力完善前隐藏两处入口，实现完整保留，
// 后续把此开关置 true 即可同时恢复，无需重建入口或改动面板组件。
export const HISTORY_SEARCH_ENABLED = false;