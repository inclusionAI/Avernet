export interface BotIdentityFile {
  type: string;
  exists: boolean;
  content?: string;
}
export type ChannelBindingMode = 'plugin' | 'bcn_gateway';
export type ChannelStage = 'draft' | 'verify' | 'online';
export type ChannelGroupChatScope = 'per_sender' | 'conversation_shared';
export type ChannelOutboundVisibility = 'full_transcript' | 'lead_only';
export interface BotChannel {
  id: number;
  type: 'dingding';
  bindingMode: ChannelBindingMode;
  stage?: ChannelStage;
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
  forwardSenderIdentity?: boolean;
  createdAt?: string;
  updatedAt?: string;
}
export interface BotChannelInput {
  bindingMode: ChannelBindingMode;
  /** 服务 Bot 的开源插件渠道创建时选择；创建后不可变。 */
  stage?: ChannelStage;
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
  forwardSenderIdentity?: boolean;
}
