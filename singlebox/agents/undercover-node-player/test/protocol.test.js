import test from "node:test";
import assert from "node:assert/strict";
import { UndercoverNodeAgent } from "../src/agent.js";
import { BcnProtocol } from "../src/protocol.js";
import {
  CONNECT_PAYLOAD,
  MockSocket,
  SESSION,
  chatAbort,
  chatSend,
  successfulConnect,
} from "./helpers.js";

function makeProtocol(options = {}) {
  const socket = new MockSocket();
  const protocol = new BcnProtocol({ now: () => 1710960001000, ...options });
  protocol.attach(socket);
  return { protocol, socket };
}

test("握手成功：请求 V3、保存身份并注入 env", async () => {
  const socket = new MockSocket();
  const environment = {};
  const protocol = new BcnProtocol({ environment });
  const connecting = protocol.connect(socket);
  const request = socket.sent[0];
  assert.equal(request.method, "bot.connect");
  assert.equal(request.params.protocol_version, 3);
  assert.equal(request.params.client_kind, "undercover-node-player");
  successfulConnect(socket, request);
  const payload = await connecting;
  assert.equal(payload.bot_uuid, "bot-node-01");
  assert.equal(protocol.token, "tok-node-123");
  assert.equal(protocol.botUuid, "bot-node-01");
  assert.deepEqual(environment, CONNECT_PAYLOAD.env);
  protocol.stop();
});

test("握手拒绝协商版本不符，且不提交身份信息", async () => {
  const socket = new MockSocket();
  const protocol = new BcnProtocol();
  const connecting = protocol.connect(socket);
  const request = socket.sent[0];
  successfulConnect(socket, request, { ...CONNECT_PAYLOAD, protocol_version: 2 });
  await assert.rejects(connecting, (error) => {
    assert.equal(error.code, "unsupported_protocol_version");
    assert.match(error.message, /protocol_version=3/);
    return true;
  });
  assert.equal(protocol.token, null);
  assert.equal(protocol.botUuid, null);
  assert.equal(protocol.heartbeatTimer, null);
});

test("握手拒绝服务端 unsupported_protocol_version 错误", async () => {
  const socket = new MockSocket();
  const protocol = new BcnProtocol();
  const connecting = protocol.connect(socket);
  const request = socket.sent[0];
  socket.receive({
    type: "res",
    id: request.id,
    ok: false,
    error: { code: "unsupported_protocol_version", message: "V3 unavailable" },
  });
  await assert.rejects(connecting, /V3 unavailable/);
});

test("心跳发送 bot.status 并处理响应", async () => {
  const { protocol, socket } = makeProtocol();
  const heartbeat = protocol.sendHeartbeat();
  const request = socket.sent.at(-1);
  assert.equal(request.method, "bot.status");
  assert.deepEqual(request.params, {});
  socket.receive({ type: "res", id: request.id, ok: true, payload: {} });
  assert.equal((await heartbeat).ok, true);
  protocol.stop();
});

test("chat.send 先 ACK，再回传 canonical final", async () => {
  let visibleContent;
  const { protocol, socket } = makeProtocol({
    onTask: async ({ content }) => {
      visibleContent = content;
      return "这是完整发言";
    },
  });
  const accepted = await protocol.handleFrame(chatSend({ text: "只有自己可见的任务" }));
  await protocol.idle();

  const ack = socket.sent.find((frame) => frame.type === "res" && frame.id === "task-1");
  assert.deepEqual(ack.payload, { run_id: accepted.runId });
  assert.equal(visibleContent, "只有自己可见的任务");
  const final = socket.sent.find((frame) => frame.type === "event");
  assert.equal(final.event, "chat");
  assert.deepEqual(final.payload, {
    runId: accepted.runId,
    sessionId: SESSION,
    seq: 1,
    ts: 1710960001000,
    state: "final",
    content: "这是完整发言",
    stopReason: "complete",
  });
  assert.equal(final.seq, final.payload.seq);
  assert.equal(protocol.runs.get(accepted.runId).status, "finished");
  protocol.stop();
});

test("V3 装饰后的投票读取未包装 prompt，不退化成弃权", async () => {
  const input = {
    alive: ["1号 玩家1", "3号 玩家3", "4号 玩家4"],
    history: [{
      round: 1,
      speeches: [
        { seat: 1, player: "1号 玩家1", text: "要通电" },
        { seat: 3, player: "3号 玩家3", text: "晚上才用" },
      ],
    }],
  };
  const prompt = `[State Machine Task]\nnode_id: vote_4\ndisplay_name: 4号投票\n\n` +
    `[Input]\n${JSON.stringify(input, null, 2)}\n\n` +
    "[Upstream Outputs]\n[vote_start]\n投票预备确认\n\n[Instruction]\n" +
    "第 2 轮投票。只输出「我投N号」或「我弃权」。\n" +
    "你是 4 号，词是【台灯】。可以投：1号、3号；不能投自己。";
  const wrapped = "[BCS Group Context]\n" +
    "- 参与者: 主持人, 玩家4\n" +
    "- 你是: 玩家4\n" +
    "- 当前投递: chat.send (需要响应)\n" +
    "- 发起方(driver): 主持人\n" +
    "- 消息来自: 主持人\n\n" +
    `[消息内容]\n[from:主持人]${prompt}`;

  for (const hasSessionContext of [true, false]) {
    const agent = new UndercoverNodeAgent();
    const { protocol, socket } = makeProtocol({
      onTask: (task) => agent.decide(task),
    });
    const frame = chatSend({ text: wrapped });
    if (hasSessionContext) frame.params.session_context = { message: prompt };
    await protocol.handleFrame(frame);
    await protocol.idle();
    const final = socket.sent.find((item) => item.type === "event");
    assert.equal(final.payload.state, "final");
    assert.equal(final.payload.content, "我投1号");
    protocol.stop();
  }
});

test("chat.abort 中止正在生成的 run，迟到内容不作为半截 final 回传", async () => {
  let release;
  let markStarted;
  let observedSignal;
  const gate = new Promise((resolve) => { release = resolve; });
  const started = new Promise((resolve) => { markStarted = resolve; });
  const { protocol, socket } = makeProtocol({
    onTask: async ({ signal }) => {
      observedSignal = signal;
      markStarted();
      await gate;
      return "这是一段迟到内容，绝不能回传";
    },
  });

  const accepted = await protocol.handleFrame(chatSend());
  await started;
  const aborted = await protocol.handleFrame(chatAbort({ runId: accepted.runId }));
  assert.equal(aborted.aborted, accepted.runId);
  assert.equal(observedSignal.aborted, true);
  release();
  await protocol.idle();

  const abortAck = socket.sent.find((frame) => frame.type === "res" && frame.id === "abort-1");
  assert.deepEqual(abortAck.payload, {
    aborted: true,
    aborted_run_ids: [accepted.runId],
  });
  assert.equal(socket.sent.filter((frame) => frame.type === "event").length, 0);
  assert.equal(protocol.runs.get(accepted.runId).status, "aborted");

  await protocol.handleFrame(chatAbort({ id: "abort-2", runId: accepted.runId }));
  const secondAck = socket.sent.find((frame) => frame.type === "res" && frame.id === "abort-2");
  assert.deepEqual(secondAck.payload, { aborted: true, aborted_run_ids: [] });
  protocol.stop();
});

test("未收到 chat.abort 的 AbortError 仍回传 error 终态", async () => {
  const { protocol, socket } = makeProtocol({
    onTask: async () => {
      throw new DOMException("timeout", "AbortError");
    },
  });

  const accepted = await protocol.handleFrame(chatSend());
  await protocol.idle();

  const ack = socket.sent.find((frame) => frame.type === "res" && frame.id === "task-1");
  assert.equal(ack.ok, true);
  const events = socket.sent.filter((frame) => frame.type === "event");
  assert.equal(events.length, 1);
  assert.equal(events[0].payload.runId, accepted.runId);
  assert.equal(events[0].payload.state, "error");
  assert.equal(events[0].payload.errorCode, "DECISION_ERROR");
  assert.equal(events[0].payload.errorMessage, "timeout");
  assert.equal(protocol.runs.get(accepted.runId).status, "finished");
  protocol.stop();
});

test("迟到任务 seq 被丢弃，不新增 run、不推进状态、不回传产物", async () => {
  let decisions = 0;
  const { protocol, socket } = makeProtocol({
    onTask: async () => {
      decisions += 1;
      return "新任务产物";
    },
  });
  const current = await protocol.handleFrame(chatSend({ id: "new-task", seq: 10 }));
  await protocol.idle();
  const eventCount = socket.sent.filter((frame) => frame.type === "event").length;

  const stale = await protocol.handleFrame(chatSend({ id: "old-task", seq: 9, text: "过期任务" }));
  await protocol.idle();
  assert.deepEqual(stale, { accepted: false, reason: "stale_seq" });
  assert.equal(decisions, 1);
  assert.equal(protocol.runs.size, 1);
  assert.equal(protocol.latestTaskSeq.get(SESSION), 10);
  assert.equal(protocol.runs.has(current.runId), true);
  assert.equal(socket.sent.filter((frame) => frame.type === "event").length, eventCount);
  const staleAck = socket.sent.find((frame) => frame.type === "res" && frame.id === "old-task");
  assert.equal(staleAck.ok, false);
  assert.equal(staleAck.error.code, "invalid_request");
  protocol.stop();
});

test("V3 chat.send 缺少 canonical session 时拒绝，不创建 run", async () => {
  const { protocol, socket } = makeProtocol();
  const frame = chatSend();
  delete frame.params.bcs_session_id;
  const result = await protocol.handleFrame(frame);
  assert.deepEqual(result, { accepted: false, reason: "invalid_session" });
  assert.equal(protocol.runs.size, 0);
  assert.equal(socket.sent[0].error.code, "invalid_request");
});

test("chat.inject 只 ACK，不生成发言", async () => {
  const { protocol, socket } = makeProtocol();
  const result = await protocol.handleFrame({
    type: "req",
    id: "inject-1",
    method: "chat.inject",
    params: { session_key: SESSION, bcs_session_id: SESSION },
  });
  assert.equal(result.accepted, true);
  assert.equal(socket.sent.length, 1);
  assert.deepEqual(socket.sent[0], {
    type: "res",
    id: "inject-1",
    ok: true,
    payload: {},
  });
});
