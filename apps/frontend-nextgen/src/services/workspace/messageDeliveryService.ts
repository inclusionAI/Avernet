import {
  cancelDelivery as cancelDeliveryApi,
  type CancelDeliveryResult,
} from '@/services/backendApi/collaboration/deliveryController';

/** 取消投递的领域结果：原始结果列表 + 汇总的首个失败原因（供 Hook 决定 toast）。 */
export interface CancelDeliveryOutcome {
  results: CancelDeliveryResult[];
  /** 结果中首个非空 error；全部成功为 null。 */
  firstError: string | null;
  /** Correlations returned by BCS when all source-message deliveries are safely removable. */
  clientMessageIds: string[];
}

/**
 * 取消单个投递（排队中或处理中的消息）。
 * Hook 不得直连 API Controller，取消能力统一经此 Service 下沉：
 * 接口裸返回结果数组，HTTP 失败抛出；单项失败经结果 error 字段返回。
 */
export async function cancelQueuedDelivery(
  messageId: string,
  deliveryId: string,
  sessionId: string,
): Promise<CancelDeliveryOutcome> {
  const results = await cancelDeliveryApi(messageId, deliveryId, sessionId);
  return {
    results,
    firstError: results.find((item) => item.error)?.error ?? null,
    clientMessageIds: Array.from(new Set(results.flatMap((item) =>
      !item.error && typeof item.client_message_id === 'string' && item.client_message_id.length > 0
        ? [item.client_message_id] : [],
    ))),
  };
}
