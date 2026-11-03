import { queryWorkerTaskStatuses } from '@/services/backendApi/collaboration/workerTaskController';
import {
  cancelDelivery as cancelDeliveryApi,
  querySessionDeliveries,
  type CancelDeliveryResult,
} from '@/services/backendApi/collaboration/deliveryController';

/** 取消投递的领域结果：原始结果列表 + 汇总的首个失败原因（供 Hook 决定 toast）。 */
export interface CancelDeliveryOutcome {
  results: CancelDeliveryResult[];
  /** 结果中首个非空 error；全部成功为 null。 */
  firstError: string | null;
  /** Correlations returned by BCS when all source-message deliveries are safely removable. */
  clientMessageIds: string[];
  /** Canonical IDs from the same confirmed results, for history-loaded user messages. */
  canonicalMessageIds: string[];
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
  const removable = results.flatMap((item) =>
    !item.error && typeof item.client_message_id === 'string' && item.client_message_id.length > 0
      ? [{ clientMessageId: item.client_message_id, canonicalMessageId: item.delivery.message_id }] : [],
  );
  return {
    results,
    firstError: results.find((item) => item.error)?.error ?? null,
    clientMessageIds: Array.from(new Set(removable.map((item) => item.clientMessageId))),
    canonicalMessageIds: Array.from(new Set(removable.flatMap((item) =>
      typeof item.canonicalMessageId === 'string' && item.canonicalMessageId.length > 0
        ? [item.canonicalMessageId] : [],
    ))),
  };
}

/** Bounded recovery for task panels; ordinary group rendering does not poll this surface. */
export async function queryTaskDeliveries(sessionId: string, sourceMessageIds: string[]) {
  const deliveries: import('@/domain/collaboration').DeliveryStatusView[] = [];
  for (let index = 0; index < sourceMessageIds.length; index += 100) {
    const batch = await querySessionDeliveries(sessionId, sourceMessageIds.slice(index, index + 100));
    deliveries.push(...batch.filter((delivery) => delivery.task));
  }
  return deliveries;
}

/** One bounded indexed lookup per batch of 20 complete assignment selectors. */
export async function queryWorkerTasks(taskRefs: string[]) {
  const rows: import('@/domain/groupTasks/statusProjection').WorkerTaskStatusView[] = [];
  const refs = [...new Set(taskRefs)];
  for (let index = 0; index < refs.length; index += 20) {
    const result = await queryWorkerTaskStatuses(refs.slice(index, index + 20));
    rows.push(...result.tasks);
  }
  return rows;
}

export { cancelWorkerTask } from '@/services/backendApi/collaboration/workerTaskController';
