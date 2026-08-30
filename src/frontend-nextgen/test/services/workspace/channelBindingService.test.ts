import type { ChannelBindingDto } from '@/services/backendApi/collaboration/channelBindingController';
import type { GroupDingTalkConfig } from '@/services/workspace/channelBindingService';
import {
  buildCreateDingTalkBindingPayload,
  buildDingTalkConfigPayload,
  DINGTALK_BINDING_CONFLICT,
  mapBindingToView,
  resolveGroupDingTalkBinding,
} from '@/services/workspace/channelBindingService';
import { expect, it } from '@jest/globals';

const CFG: GroupDingTalkConfig = {
  robotCode: '  robot-1  ',
  appKey: '  key-1  ',
  appSecret: 'secret-1',
  enableStreamOutput: false,
  cardTemplateId: '',
  groupChatScope: 'conversation_shared',
  outboundVisibility: 'lead_only',
};

it('buildDingTalkConfigPayload: 普通模式编码 send_mode 为 normal/markdown, trim robotCode/appKey, 不 trim secret', () => {
  const payload = buildDingTalkConfigPayload(CFG);
  expect(payload).toEqual({
    robot_code: 'robot-1',
    client_id: 'key-1',
    client_secret: 'secret-1',
    send_mode: { mode: 'normal', message_type: 'markdown' },
  });
});

it('buildDingTalkConfigPayload: 流式模式编码为 streaming_card + card_template_id + fallback', () => {
  const payload = buildDingTalkConfigPayload({
    ...CFG,
    enableStreamOutput: true,
    cardTemplateId: '  tpl-9  ',
  });
  expect(payload.send_mode).toEqual({
    mode: 'streaming_card',
    card_template_id: 'tpl-9',
    fallback_message_type: 'markdown',
  });
});

it('buildCreateDingTalkBindingPayload: 不传 env,target=group,scope/visibility 写死默认,account_ref=robotCode', () => {
  const body = buildCreateDingTalkBindingPayload('grp-1', CFG);
  expect(body.channel_type).toBe('dingtalk');
  expect(body.target).toEqual({ group: { group_id: 'grp-1' } });
  expect(body.account_ref).toBe('robot-1');
  expect(body.group_chat_scope).toBe('conversation_shared');
  expect(body.outbound_visibility).toBe('lead_only');
  expect(body.config.robot_code).toBe('robot-1');
  // 严禁 env 字段
  expect((body as unknown as Record<string, unknown>).env).toBeUndefined();
});

it('mapBindingToView: 回填 robotCode/appKey, appSecret 恒空(后端 <redacted>), 流式标志从 send_mode 推导', () => {
  const dto: ChannelBindingDto = {
    id: 'b-1',
    channel_type: 'dingtalk',
    account_ref: 'robot-1',
    target: { group: { group_id: 'grp-1' } },
    group_chat_scope: 'conversation_shared',
    outbound_visibility: 'lead_only',
    env: '',
    status: 'active',
    config: {
      robot_code: 'robot-1',
      client_id: 'key-1',
      client_secret: '<redacted>',
      send_mode: { mode: 'streaming_card', card_template_id: 'tpl-9', fallback_message_type: 'markdown' },
    },
  };
  const view = mapBindingToView(dto);
  expect(view).toEqual({
    bindingId: 'b-1',
    status: 'active',
    config: {
      robotCode: 'robot-1',
      appKey: 'key-1',
      appSecret: '',
      enableStreamOutput: true,
      cardTemplateId: 'tpl-9',
      groupChatScope: 'conversation_shared',
      outboundVisibility: 'lead_only',
    },
  });
});

it('mapBindingToView: 缺 config.send_mode 时按普通模式回填, robotCode 兜底 account_ref', () => {
  const view = mapBindingToView({
    id: 'b-2',
    channel_type: 'dingtalk',
    account_ref: 'robot-x',
    target: { group: { group_id: 'grp-1' } },
    outbound_visibility: 'full_transcript',
    env: '',
    status: 'disabled',
    config: { client_secret: '<redacted>' },
  });
  expect(view.config).toEqual({
    robotCode: 'robot-x',
    appKey: '',
    appSecret: '',
    enableStreamOutput: false,
    cardTemplateId: '',
    groupChatScope: 'per_sender',
    outboundVisibility: 'full_transcript',
  });
  expect(view.status).toBe('disabled');
});

it('resolveGroupDingTalkBinding: 空列表 → null', () => {
  expect(resolveGroupDingTalkBinding([], 'grp-1')).toBeNull();
  expect(resolveGroupDingTalkBinding(undefined, 'grp-1')).toBeNull();
});

it('resolveGroupDingTalkBinding: 仅本群 1 条 dingtalk 绑定 → view', () => {
  const items: ChannelBindingDto[] = [
    {
      id: 'b-1',
      channel_type: 'dingtalk',
      account_ref: 'r',
      target: { group: { group_id: 'grp-1' } },
      outbound_visibility: 'full_transcript',
      env: '',
      status: 'active',
      config: { client_secret: '<redacted>' },
    },
    // 其它群 + 非 dingtalk 渠道应被过滤掉
    {
      id: 'b-2',
      channel_type: 'dingtalk',
      account_ref: 'r',
      target: { group: { group_id: 'grp-other' } },
      outbound_visibility: 'full_transcript',
      env: '',
      status: 'active',
      config: { client_secret: '<redacted>' },
    },
  ];
  const resolved = resolveGroupDingTalkBinding(items, 'grp-1');
  expect(resolved).not.toBeNull();
  expect(resolved).not.toBe(DINGTALK_BINDING_CONFLICT);
  expect((resolved as { bindingId: string }).bindingId).toBe('b-1');
});

it('resolveGroupDingTalkBinding: 本群 >1 条 dingtalk 绑定 → conflict', () => {
  const items: ChannelBindingDto[] = [
    {
      id: 'b-1',
      channel_type: 'dingtalk',
      account_ref: 'r',
      target: { group: { group_id: 'grp-1' } },
      outbound_visibility: 'full_transcript',
      env: '',
      status: 'active',
      config: { client_secret: '<redacted>' },
    },
    {
      id: 'b-2',
      channel_type: 'dingtalk',
      account_ref: 'r',
      target: { group: { group_id: 'grp-1' } },
      outbound_visibility: 'full_transcript',
      env: '',
      status: 'disabled',
      config: { client_secret: '<redacted>' },
    },
  ];
  expect(resolveGroupDingTalkBinding(items, 'grp-1')).toBe(DINGTALK_BINDING_CONFLICT);
});
