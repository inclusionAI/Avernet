import { EventEmitter } from "node:events";

export const SESSION = "grp-00000001:abcd1234";

export const CONNECT_PAYLOAD = Object.freeze({
  is_new: true,
  token: "tok-node-123",
  bot_uuid: "bot-node-01",
  protocol_version: 3,
  min_supported_version: 1,
  capabilities: {
    unified_run_events: true,
    canonical_session_id: true,
    tool_result_task_intent: false,
  },
  env: {
    BCN_BOT_UUID: "bot-node-01",
    BCN_BOT_TOKEN: "tok-node-123",
  },
});

export class MockSocket extends EventEmitter {
  constructor() {
    super();
    this.sent = [];
  }

  send(data) {
    this.sent.push(JSON.parse(String(data)));
  }

  receive(frame) {
    this.emit("message", JSON.stringify(frame));
  }

  close() {
    this.emit("close");
  }
}

export function chatSend({ id = "task-1", seq, session = SESSION, text = "节点任务" } = {}) {
  const frame = {
    type: "req",
    id,
    method: "chat.send",
    params: {
      session_key: session,
      bcs_session_id: session,
      message: {
        role: "user",
        content: [{ type: "text", text }],
      },
    },
  };
  if (seq !== undefined) frame.seq = seq;
  return frame;
}

export function chatAbort({ id = "abort-1", runId, session = SESSION }) {
  return {
    type: "req",
    id,
    method: "chat.abort",
    params: { session_key: session, run_id: runId },
  };
}

export function successfulConnect(socket, request, payload = CONNECT_PAYLOAD) {
  socket.receive({ type: "res", id: request.id, ok: true, payload });
}
