import type { DeliveryStatusView } from '@/domain/collaboration/types';
import { backendRequest } from '../httpClient';

/**
 * 投递取消结果（与后端 CancelMessageDeliveryResult 对齐）。
 * error 非空表示该条投递取消失败（可重试），为 null 表示成功。
 */
export interface CancelDeliveryResult {
  delivery: DeliveryStatusView;
  error: string | null;
}

/**
 * ⚠ 该组 delivery 路由由 BCS 直接裸返回结果体（数组/对象），**无信封包装**
 * （与 sessions/messages 等经网关包装的信封接口不同）。失败经 HTTP 非 2xx
 * 由 httpClient 抛出，调用方按 Promise reject 处理即可，勿再判信封。
 */

/** 取消单个投递（排队中或处理中的消息）。 */
export async function cancelDelivery(messageId: string, deliveryId: string, sessionId: string) {
  return backendRequest<CancelDeliveryResult[]>(
    `/openapi/v1/collaboration/messages/${messageId}/deliveries/${deliveryId}/cancel`,
    { method: 'POST', data: { session_id: sessionId }, injectUserId: false },
  );
}

/** 取消消息的所有投递。 */
export async function cancelAllDeliveries(messageId: string, sessionId: string) {
  return backendRequest<CancelDeliveryResult[]>(`/openapi/v1/collaboration/messages/${messageId}/deliveries/cancel`, {
    method: 'POST',
    data: { session_id: sessionId },
    injectUserId: false,
  });
}

/** 查询单个消息的所有投递状态。 */
export async function listMessageDeliveries(messageId: string, sessionId: string) {
  return backendRequest<DeliveryStatusView[]>(`/openapi/v1/collaboration/messages/${messageId}/deliveries`, {
    method: 'GET',
    params: { session_id: sessionId },
    injectUserId: false,
  });
}

/** 批量查询会话中多个消息的投递状态（messageIds 为源消息/用户消息 ID，单次 ≤100 个）。 */
export async function querySessionDeliveries(sessionId: string, messageIds: string[]) {
  return backendRequest<DeliveryStatusView[]>(
    `/openapi/v1/collaboration/sessions/${sessionId}/message-deliveries/query`,
    { method: 'POST', data: { message_ids: messageIds }, injectUserId: false },
  );
}
