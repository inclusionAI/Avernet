export interface BotRuntimeSource {
  engine?: string;
  templateType?: string;
  templateName?: string;
  botType?: string;
  botId?: string;
}

export interface BotRuntime {
  engine: string;
  isAgentCodingBot: boolean;
  templateType?: string;
  templateName?: string;
  botType?: string;
  isDefaultBot: boolean;
}
