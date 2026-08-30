import {
  createChannelBinding,
  deleteChannelBinding,
  listBindingsByTarget,
  updateChannelBinding,
  type ChannelBindingDto,
  type CreateChannelBindingRequest,
  type DingTalkConfigPayload,
  type DingTalkSendMode,
} from '@/services/backendApi/collaboration/channelBindingController';
import type { DomainError, DomainResult } from './identityService';

/** 群钉钉机器人配置（领域/表单 camelCase，组件层消费；DTO snake_case 仅停留在 controller 层）。 */
export type DingTalkGroupChatScope = 'per_sender' | 'conversation_shared';
export type DingTalkOutboundVisibility = 'full_transcript' | 'lead_only';

export interface GroupDingTalkConfig {
  robotCode: string;
  appKey: string;
  appSecret: string;
  enableStreamOutput: boolean;
  cardTemplateId: string;
  /** 会话模式：按发送人独立会话 / 共享同一个会话。 */
  groupChatScope: DingTalkGroupChatScope;
  /** 发送消息范围：完整群聊消息 / 仅 Driver 消息。 */
  outboundVisibility: DingTalkOutboundVisibility;
}

/** 加载到的绑定视图：含 id 与启停状态，用于回填/启停/改配置/删除。appSecret 恒空（后端不回显）。 */
export interface DingTalkBindingView {
  bindingId: string;
  status: 'active' | 'disabled';
  config: GroupDingTalkConfig;
}

/** 多绑定冲突标记：同一群出现 >1 条 dingtalk 绑定，不允许前端编辑，需联系管理员。 */
export const DINGTALK_BINDING_CONFLICT = 'conflict' as const;
export type DingTalkBindingState = DingTalkBindingView | null | typeof DINGTALK_BINDING_CONFLICT;

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

/** 表单 camelCase → 后端 config 子对象（snake_case）。 */
export function buildDingTalkConfigPayload(cfg: GroupDingTalkConfig): DingTalkConfigPayload {
  const send_mode: DingTalkSendMode = cfg.enableStreamOutput
    ? {
        mode: 'streaming_card',
        card_template_id: cfg.cardTemplateId.trim(),
        fallback_message_type: 'markdown',
      }
    : { mode: 'normal', message_type: 'markdown' };
  return {
    robot_code: cfg.robotCode.trim(),
    client_id: cfg.appKey.trim(),
    // app_secret 不 trim，避免误改密钥字符。
    client_secret: cfg.appSecret,
    send_mode,
  };
}

/** 表单 + groupId → POST body。不传 env（后端 deny_unknown_fields）；scope/visibility 写死 MVP 默认。 */
export function buildCreateDingTalkBindingPayload(
  groupId: string,
  cfg: GroupDingTalkConfig,
): CreateChannelBindingRequest {
  return {
    channel_type: 'dingtalk',
    account_ref: cfg.robotCode.trim(),
    target: { group: { group_id: groupId } },
    group_chat_scope: cfg.groupChatScope,
    outbound_visibility: cfg.outboundVisibility,
    config: buildDingTalkConfigPayload(cfg),
  };
}

/** 后端 DTO → 领域视图。client_secret 后端回显为 `<redacted>`，这里恒置空，编辑时需重新输入。 */
export function mapBindingToView(dto: ChannelBindingDto): DingTalkBindingView {
  const sm = dto.config?.send_mode;
  const isStreaming = sm?.mode === 'streaming_card';
  const config: GroupDingTalkConfig = {
    robotCode: dto.config?.robot_code ?? dto.account_ref ?? '',
    appKey: dto.config?.client_id ?? '',
    appSecret: '',
    enableStreamOutput: isStreaming,
    cardTemplateId: isStreaming && sm?.mode === 'streaming_card' ? sm.card_template_id ?? '' : '',
    groupChatScope: dto.group_chat_scope ?? 'per_sender',
    outboundVisibility: dto.outbound_visibility ?? 'full_transcript',
  };
  return { bindingId: dto.id, status: dto.status, config };
}

/** 从 by-target 列表里解析出目标群的唯一 dingtalk 绑定；>1 条视为冲突。 */
export function resolveGroupDingTalkBinding(
  items: ChannelBindingDto[] | undefined,
  groupId: string,
): DingTalkBindingState {
  const matched = (items ?? []).filter(
    (dto) => dto.channel_type === 'dingtalk' && dto.target?.group?.group_id === groupId,
  );
  if (matched.length === 0) return null;
  if (matched.length > 1) return DINGTALK_BINDING_CONFLICT;
  return mapBindingToView(matched[0]);
}

/**
 * 群钉钉渠道绑定 Service：加载/创建/改配置/启停/删除。
 * 编排 DTO↔领域模型转换、解包 envelope.data、错误标准化为 DomainResult。
 */
export const channelBindingService = {
  async loadGroupDingTalkBinding(groupId: string): Promise<DomainResult<DingTalkBindingState>> {
    try {
      const resp = await listBindingsByTarget({
        target_type: 'group',
        target_id: groupId,
        channel_type: 'dingtalk',
      });
      return { ok: true, data: resolveGroupDingTalkBinding(resp.data?.items, groupId) };
    } catch {
      return {
        ok: false,
        error: toDomainError('DINGTALK_LOAD_FAILED', '加载钉钉绑定配置失败，请稍后重试。'),
      };
    }
  },

  async createGroupDingTalkBinding(
    groupId: string,
    cfg: GroupDingTalkConfig,
  ): Promise<DomainResult<DingTalkBindingView>> {
    try {
      const resp = await createChannelBinding(buildCreateDingTalkBindingPayload(groupId, cfg));
      const dto = resp.data;
      if (!dto) {
        return {
          ok: false,
          error: toDomainError('DINGTALK_CREATE_FAILED', '创建钉钉绑定失败，请稍后重试。'),
        };
      }
      return { ok: true, data: mapBindingToView(dto) };
    } catch {
      return {
        ok: false,
        error: toDomainError('DINGTALK_CREATE_FAILED', '创建钉钉绑定失败，请稍后重试。'),
      };
    }
  },

  async updateGroupDingTalkBindingConfig(bindingId: string, cfg: GroupDingTalkConfig): Promise<DomainResult<void>> {
    try {
      await updateChannelBinding(bindingId, { config: buildDingTalkConfigPayload(cfg) });
      return { ok: true, data: undefined };
    } catch {
      return {
        ok: false,
        error: toDomainError('DINGTALK_UPDATE_FAILED', '更新钉钉绑定配置失败，请稍后重试。'),
      };
    }
  },

  async setGroupDingTalkBindingActive(bindingId: string, active: boolean): Promise<DomainResult<void>> {
    try {
      await updateChannelBinding(bindingId, { active });
      return { ok: true, data: undefined };
    } catch {
      return {
        ok: false,
        error: toDomainError('DINGTALK_UPDATE_FAILED', '切换钉钉绑定状态失败，请稍后重试。'),
      };
    }
  },

  async deleteGroupDingTalkBinding(bindingId: string): Promise<DomainResult<void>> {
    try {
      await deleteChannelBinding(bindingId);
      return { ok: true, data: undefined };
    } catch {
      return {
        ok: false,
        error: toDomainError('DINGTALK_DELETE_FAILED', '解绑钉钉机器人失败，请稍后重试。'),
      };
    }
  },
};
