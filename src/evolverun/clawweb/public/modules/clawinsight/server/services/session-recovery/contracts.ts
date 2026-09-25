export type RecoveryRequest = {
  event_id: string;
  tc_fault_label: string | null;
  target: { bot_id: string; entity_id: string; env: "pre" | "prod"; engine: "OC" | "TE" };
  session_key: string;
  delivery_key: string;
  diagnosis: string;
};
export type RecoveryAcknowledgment = { event_id: string; delivery_key: string; status: "accepted" };
export type { MessageReceipt as DeliveryReceipt } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { MessageReceipt as DeliveryReceipt } from "@avernet/clawweb-shared/server/services/bot-runtime";
export type RecoveryActor = { userId: string; isAdmin: boolean };
export interface SessionRecoveryApi { create(body: unknown, actor: RecoveryActor): Promise<RecoveryAcknowledgment> }

/** Outbound contract owned by monitoring; message delivery does not imply task recovery. */
export type RecoveryResult = {
  event_id: string;
  delivery: {
    delivery_key: string;
    status: DeliveryReceipt["status"];
    result_text: string;
    result_payload?: Record<string, string | number>;
    started_at_ms: number;
    finished_at_ms: number;
    error: string | null;
  };
};
export interface RecoveryResultReporter { report(result: RecoveryResult): Promise<void> }
export type RecoveryDeliveryLog = RecoveryResult & { event: "session_recovery_delivery" };
export interface RecoveryLogger { log(result: RecoveryDeliveryLog): void }

export type DeliveryKey = Pick<RecoveryRequest, "event_id" | "delivery_key">;
export type DeliveryRejection = { status: number; code: string; message: string };
export type DeliveryRecord = {
  fingerprint: string;
  startedAt: number;
  settleAfter: number;
  outcome: { result: RecoveryResult } | { rejection: DeliveryRejection } | null;
  callbackDelivered: boolean;
};
/** A successful claim is the only authorization to send. Claims are never recycled. */
export interface RecoveryDeliveryStore {
  claim(key: DeliveryKey, fingerprint: string, startedAt: number, settleAfter: number): Promise<{ acquired: boolean; record: DeliveryRecord }>;
  finish(key: DeliveryKey, outcome: NonNullable<DeliveryRecord["outcome"]>): Promise<DeliveryRecord>;
  markReported(key: DeliveryKey): Promise<void>;
}
