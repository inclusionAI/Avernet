export interface BotRuntimeSource {
  engine?: string;
  templateType?: string;
  templateName?: string;
  engineForm?: string;
  bcnEnabled?: boolean;
  botType?: string;
  botId?: string;
}

export interface BotRuntime {
  engine: string;
  isAgentCodingBot: boolean;
  bcnEnabled?: boolean;
  templateType?: string;
  templateName?: string;
  botType?: string;
  isDefaultBot: boolean;
}
