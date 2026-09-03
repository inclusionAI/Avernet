export interface BotRuntimeSource {
  engine?: string;
  templateType?: string;
  botType?: string;
  botId?: string;
}

export interface BotRuntime {
  engine: string;
  isAgentCodingBot: boolean;
  templateType?: string;
  botType?: string;
  isDefaultBot: boolean;
}
