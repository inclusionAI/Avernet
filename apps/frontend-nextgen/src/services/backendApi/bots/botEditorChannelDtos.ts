import type { BackendUnknownRecord } from '../types';

export interface ChannelDto extends BackendUnknownRecord {
  id: number;
  type: 'dingding';
  binding_mode?: 'plugin' | 'bcn_gateway';
  description?: string;
  status: 'active' | 'inactive';
  created_at?: string | null;
  updated_at?: string | null;
  config: BackendUnknownRecord & {
    client_id: string;
    has_client_secret: boolean;
    robot_code?: string | null;
    enable_streaming_cards?: boolean;
    card_template_id?: string | null;
    card_template_key?: string | null;
    dm_policy?: 'open' | 'disabled';
    allowlist?: string[];
    reply_to_message?: boolean;
    aix_enable?: boolean;
    include_sender_name?: boolean;
    group_chat_scope?: 'per_sender' | 'conversation_shared' | null;
    outbound_visibility?: 'full_transcript' | 'lead_only' | null;
  };
}
