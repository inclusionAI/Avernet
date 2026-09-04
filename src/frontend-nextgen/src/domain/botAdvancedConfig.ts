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
  enableStreamingCards: boolean;
  cardTemplateId?: string;
  cardTemplateKey?: string;
  dmPolicy: 'open' | 'disabled';
  allowlist: string[];
  replyToMessage: boolean;
  aixEnable: boolean;
  includeSenderName: boolean;
  createdAt?: string;
  updatedAt?: string;
}
export interface BotChannelInput {
  description: string;
  clientId: string;
  clientSecret: string;
  enableStreamingCards: boolean;
  cardTemplateId: string;
  cardTemplateKey: string;
  dmPolicy: 'open' | 'disabled';
  allowlist: string[];
  replyToMessage: boolean;
  aixEnable: boolean;
  includeSenderName: boolean;
}
