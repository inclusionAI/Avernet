const UNDERCOVER_GAME_PANEL_EXPORTS = new Set([
  'UndercoverGamePanel',
  'bcsPanel.UndercoverGamePanel',
]);

/** 只向卧底面板暴露当前登录 Human 的 actor_id；身份晚到时由宿主重渲染更新。 */
export function resolveUndercoverViewerActorId(
  exportName: string | undefined,
  identityId: string | null | undefined,
): string | undefined {
  if (!exportName || !UNDERCOVER_GAME_PANEL_EXPORTS.has(exportName) || !identityId?.trim()) {
    return undefined;
  }
  const id = identityId.trim();
  return /^human[_-]/.test(id) ? id : `human_${id}`;
}
