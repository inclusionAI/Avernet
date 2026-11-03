// 多人类「偷看者」隔离测试（panel 数据层）
// 覆盖 issue #2609 验收第 4 条的前端半:
//  - 公开 panel 参数不携带任何私有词(任何普通玩家一调用接口就拿不到别人的词)
//  - 每个 currentViewerActorId 只把"本人那一格"高亮/当 own;多人类(2 人)下,
//    彼此不因公开参数互相看见对方私有词。
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const api = require('../../dist/index.umd.js');
const { parsePrivateActionContext, normalizePanelParams, normalizeUndercoverGameViewModel } = api;

const WORDS = ['隐词甲', '隐词乙'];

// 2 Human + 3 Bot,总 5 人,1 卧底。与 referee 侧 fixture 对齐。
function params2Human(viewer) {
  const base = {
    runId: 'run-multi', groupId: 'g', sessionId: 's', gameSessionId: 'g', phase: 'speaking', round: 1, attempt: 1,
    host: { actorId: 'host', displayName: '主持人' },
    seatOrder: ['human-A', 'human-B', 'bot-wukong', 'bot-bajie', 'bot-shaseng'],
    turnOrder: ['human-A', 'human-B', 'bot-wukong', 'bot-bajie', 'bot-shaseng'],
    players: [
      { actorId: 'human-A', displayName: '你', seatNumber: 1, isHuman: true },
      { actorId: 'human-B', displayName: '你', seatNumber: 2, isHuman: true },
      { actorId: 'bot-wukong', displayName: '悟空', seatNumber: 3 },
      { actorId: 'bot-bajie', displayName: '八戒', seatNumber: 4 },
      { actorId: 'bot-shaseng', displayName: '沙僧', seatNumber: 5 },
    ],
    nodeActorMap: {
      speak_1: 'human-A', speak_2: 'human-B', speak_3: 'bot-wukong', speak_4: 'bot-bajie', speak_5: 'bot-shaseng', collect: 'host',
    },
    currentViewerActorId: viewer,
    currentAction: { actorId: 'human-A', type: 'speech', nodeId: 'speak_1' },
    rules: { speechMaxChars: 25, forbidOwnWord: true, bluntness: 4 },
    publicHistory: [], voteCandidates: [],
  };
  return normalizePanelParams(base);
}

// 1) 公开参数(full serialize)绝不含任何词
for (const viewer of ['human-A', 'human-B', 'bot-wukong']) {
  const params = params2Human(viewer);
  const blob = JSON.stringify(params);
  for (const word of WORDS) assert.ok(!blob.includes(word), `public params must not leak word ${word} (viewer ${viewer})`);
  assert.ok(!blob.includes('word'), 'public params must not carry a "word" field');
}

// 2) 私有 instruction 只来自"本人待办",且只含本人可读的上下文。
//    构造 A 与 B 各自不同的私有 speech 上下文,确保解析结果彼此隔离。
function ctx(word, seat) {
  const instruction = `轮到你\n[UNDERCOVER_UI_CONTEXT_V1]\n${JSON.stringify({ action: 'speech', round: 1, seatNumber: seat, word, maxChars: 25, forbidOwnWord: true, bluntness: 4 })}\n[/UNDERCOVER_UI_CONTEXT_V1]`;
  return parsePrivateActionContext(instruction);
}
assert.equal(ctx('隐词甲', 1).context.word, '隐词甲');
assert.equal(ctx('隐词乙', 2).context.word, '隐词乙');
// A 的私有上下文解析绝不含 B 的词
assert.ok(!JSON.stringify(ctx('隐词甲', 1)).includes('隐词乙'));
assert.ok(!JSON.stringify(ctx('隐词乙', 2)).includes('隐词甲'));

// 3) 多人类 view model:A 视角下 pendingHumanActorId 只可能是 A(服务端已按 assignee 过滤);
//    在公开参数不含词的前提下,普通玩家拿不到别人的词。
const graphA = { run: { run_id: 'run-multi', status: 'running' }, nodes: [{ node_id: 'speak_1', status: 'running', attempt: 1 }] };
const modelA = normalizeUndercoverGameViewModel(params2Human('human-A'), graphA, [{ node_id: 'speak_1', attempt: 1 }]);
assert.equal(modelA.pendingHumanActorId, 'human-A');
// B 同样只高亮自己
const graphB = { run: { run_id: 'run-multi', status: 'running' }, nodes: [{ node_id: 'speak_2', status: 'running', attempt: 1 }] };
const modelB = normalizeUndercoverGameViewModel(params2Human('human-B'), graphB, [{ node_id: 'speak_2', attempt: 1 }]);
assert.equal(modelB.pendingHumanActorId, 'human-B');

console.log('Multi-human peeker isolation tests passed (panel data layer).');