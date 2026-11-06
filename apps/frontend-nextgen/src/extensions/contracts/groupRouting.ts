// 建群 service-bot 选路槽位契约（collab-form Task 17）：纯类型文件，运行时零字节。
// useServiceBotRouting / ServiceBotRoutingControl 属 pages 裁剪面（collab 无 TC Bot 分类服务），
// 选路模型与 hook/组件签名从实现原样搬出；实现文件反向注解本契约（type-only，编译期擦除）。
// 依赖红线：仅 domain（serviceBotRouting 纯模型）与 React 类型，零 import pages/hooks 实现。
import type {
  ParticipantRoutingStates,
  ServiceBotRoutingMode,
  ServiceBotStage,
} from '@/domain/collaboration/serviceBotRouting';
import type { ComponentType } from 'react';

/** 建群弹窗 service-bot 选路模型（useServiceBotRouting 的返回面，原样展开）。 */
export interface ServiceBotRoutingModel {
  /** 状态属于建群弹窗；移除、重选、关闭以及卸载均使旧查询失效。 */
  states: ParticipantRoutingStates;
  ready: boolean;
  selectRoutingMode: (id: string, routingMode: ServiceBotRoutingMode) => void;
  selectStage: (id: string, stage: ServiceBotStage) => void;
  /** 单 Bot 重试（识别失败恢复出口）。 */
  retry: (actorId: string) => Promise<void>;
  /** 提交前为参与者打 routing 阶段 tags（指定路由模式）。 */
  applyTags: <T extends { actor_id: string }>(participants: T[]) => Array<T & { tags?: ServiceBotStage[] }>;
}

/** 选路 hook 契约（useServiceBotRouting 签名；open=false 时持空态不发起分类查询）。 */
export type ServiceBotRoutingHook = (actorIds: string[], open: boolean) => ServiceBotRoutingModel;

/** 建群参与者行内选路控件 props（原样搬自 ServiceBotRoutingControl 的内联对象类型）。 */
export interface ServiceBotRoutingControlProps {
  actorId: string;
  botName: string;
  routing: ServiceBotRoutingModel;
}

/** 选路控件组件类型。 */
export type ServiceBotRoutingControlComponent = ComponentType<ServiceBotRoutingControlProps>;
