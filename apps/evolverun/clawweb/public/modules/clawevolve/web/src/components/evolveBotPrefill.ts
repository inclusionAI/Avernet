import type { EvolveBotPickerOption } from './evolveBotIdentity'

/** Query parameters select an authorized candidate; they never change the actor. */
export function resolveEvolveBotPrefill<T extends EvolveBotPickerOption>(
  query: string, bots: T[],
): { bot?: T; error?: string } {
  const params = new URLSearchParams(query)
  const botId = params.get('botId')?.trim()
  if (!botId) return { error: '请提供目标 botId' }
  const ownerId = params.get('ownerId')?.trim() || ''
  const entityId = params.get('entityId')?.trim() || ''
  if (ownerId && entityId && ownerId !== entityId) {
    return { error: 'ownerId 与 entityId 不一致，请检查跳转参数' }
  }
  const owner = ownerId || entityId
  if (botId === 'default' && !owner) return { error: 'default Bot 必须提供 ownerId（兼容 entityId）' }
  const env = params.get('botEnv')?.trim()
  if (!env) return { error: '请提供目标 botEnv' }
  const matches = bots.filter(bot => bot.botId === botId && bot.env === env
    && (!owner || bot.ownerId === owner))
  if (matches.length === 1) return { bot: matches[0] }
  return { error: matches.length > 1
    ? '匹配到多个 Bot，请根据归属手动选择，或补充 ownerId'
    : '未找到可访问的目标 Bot，请检查参数或权限' }
}
