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
