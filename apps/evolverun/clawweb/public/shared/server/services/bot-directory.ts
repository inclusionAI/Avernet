export type DirectoryBot = {
  botId: string
  botName?: string | null
  env?: string | null
  deviceProvider?: 'baas' | 'arca' | string | null
  activeEngine?: string | null
  botType?: string | null
  hasServiceBot?: boolean
  displayBotId: string
  status: 'active' | 'all' | string
  source: string
  ownerId?: string | null
  accessType?: 'owner' | 'collaborator'
}

/** Host supplies Bots owned by or shared with this user. */
export interface BotDirectory {
  listBots(userId: string, status: "active" | "all"): Promise<DirectoryBot[]>;
  canAccessBot(userId: string, botId: string | undefined): Promise<boolean>;
}
