export interface ExpertSummary {
  expert_id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  creation_count: number;
  first_added_at: string;
}

export interface ExpertDetail extends ExpertSummary {
  identity: Array<{ type: string; content: string }>;
  skills: Array<{ name: string; description: string }>;
  /** Older catalogue responses omit MCP declarations during rollout. */
  mcp?: Array<{ server_code: string }>;
  /** Null/omitted = no operation; [] = clear non-protected workspace items. */
  resources?: Array<{ path: string }> | null;
}

export interface ExpertQuery {
  keyword: string;
  category: string;
  sort: 'hot' | 'newest';
  page_num: number;
  page_size: number;
}

export interface ExpertPage {
  total: number;
  items: ExpertSummary[];
}

export interface ExpertBotAccepted {
  bot_id: string;
  iframe_url: string;
  redirect_url: string;
}
export interface ExpertBotCreationStatus extends ExpertBotAccepted {
  state:
    | 'AWAITING_AUTHORIZATION'
    | 'CREATING'
    | 'APPLYING'
    | 'READY'
    | 'CREATE_FAILED'
    | 'APPLY_FAILED'
    | 'AUTHORIZATION_REJECTED'
    | 'AUTHORIZATION_EXPIRED';
  message?: string;
  bot?: Record<string, unknown> | null;
  apply?: ExpertApplyReport | null;
}

export interface ExpertApplyReport {
  trigger?: string;
  state?: { kind: string; reference?: string | null };
  apply_id: string;
  result: 'RUNNING' | 'SUCCEEDED' | 'PARTIAL' | 'FAILED';
  entries: Array<{ category: string; name: string; action: string; error?: string | null; note?: string | null }>;
  notes?: string[];
}
