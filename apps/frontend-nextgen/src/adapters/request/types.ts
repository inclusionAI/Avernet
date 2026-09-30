export interface RuntimeRequestConfig {
  url?: string;
  method?: string;
  headers?: Record<string, string | number | boolean>;
  skipErrorHandler?: boolean;
  [key: string]: unknown;
}

export interface RuntimeRequestContext {
  platform: 'web' | 'electron' | 'dingtalk' | 'vscode';
}

export type RuntimeRequestAdapter = (
  config: RuntimeRequestConfig,
  context: RuntimeRequestContext,
) => RuntimeRequestConfig;
