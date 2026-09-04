import type { BackendUnknownRecord } from '../types';

export interface ChannelDto extends BackendUnknownRecord {
  id: number;
  type: 'dingding';
  description?: string;
  status: 'active' | 'inactive';
  created_at?: string | null;
  updated_at?: string | null;
  config: BackendUnknownRecord & {
    client_id: string;
    has_client_secret: boolean;
    enable_streaming_cards?: boolean;
    card_template_id?: string | null;
    card_template_key?: string | null;
    dm_policy?: 'open' | 'disabled';
    allowlist?: string[];
    reply_to_message?: boolean;
    aix_enable?: boolean;
    include_sender_name?: boolean;
  };
}
