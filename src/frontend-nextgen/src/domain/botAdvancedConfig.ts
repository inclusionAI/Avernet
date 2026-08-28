export interface BotIdentityFile {
  type: string;
  exists: boolean;
  content?: string;
}
export interface BotChannel {
  id: number;
  type: 'dingding';
  description?: string;
  status: 'active' | 'inactive';
  clientId: string;
  hasSecret: boolean;
}
export interface BotChannelInput {
  description: string;
  clientId: string;
  clientSecret: string;
}
