export interface BotIdentityFile {
  type: string;
  exists: boolean;
  content?: string;
}
export type ChannelBindingMode = 'plugin' | 'bcn_gateway';
export type ChannelGroupChatScope = 'per_sender' | 'conversation_shared';
export type ChannelOutboundVisibility = 'full_transcript' | 'lead_only';
export interface BotChannel {
  id: number;
  type: 'dingding';
  bindingMode: ChannelBindingMode;
  description?: string;
  status: 'active' | 'inactive';
  clientId: string;
  hasSecret: boolean;
  robotCode?: string;
  enableStreamingCards: boolean;
  cardTemplateId?: string;
  cardTemplateKey?: string;
  dmPolicy: 'open' | 'disabled';
  allowlist: string[];
  replyToMessage: boolean;
  aixEnable: boolean;
  includeSenderName: boolean;
  groupChatScope?: ChannelGroupChatScope;
  outboundVisibility?: ChannelOutboundVisibility;
  createdAt?: string;
  updatedAt?: string;
}
export interface BotChannelInput {
  bindingMode: ChannelBindingMode;
  description: string;
  clientId: string;
  clientSecret: string;
  robotCode?: string;
  enableStreamingCards: boolean;
  cardTemplateId: string;
  cardTemplateKey: string;
  dmPolicy: 'open' | 'disabled';
  allowlist: string[];
  replyToMessage: boolean;
  aixEnable: boolean;
  includeSenderName: boolean;
  groupChatScope?: ChannelGroupChatScope;
  outboundVisibility?: ChannelOutboundVisibility;
}
