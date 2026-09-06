export type EvolveBotPickerOption = {
  botId: string
  botName?: string | null
  env?: string | null
  deviceProvider?: string | null
  activeEngine?: string | null
  botType?: string | null
  hasServiceBot?: boolean
  ownerId?: string | null
  accessType?: 'owner' | 'collaborator'
}

export function evolveBotOptionKey(bot: EvolveBotPickerOption): string {
  return [bot.ownerId ?? '', bot.botId, bot.env ?? ''].join('\u0000')
}
