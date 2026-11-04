import test from "node:test";
import assert from "node:assert/strict";
import { UndercoverNodeAgent, parseNodeTask, publicSpeeches } from "../src/agent.js";
import { descriptionForRound, parseVote, validateSpeech } from "../src/rules.js";

function speechPrompt(roundNumber, seat = 3, word = "台灯") {
  return `[State Machine Task]\nnode_id: speak_${seat}\ndisplay_name: ${seat}号发言\n\n` +
    `[Input]\n{}\n\n[Upstream Outputs]\n(none)\n\n[Instruction]\n` +
    `你是 ${seat} 号，你的词是【${word}】。第 ${roundNumber} 轮发言。\n` +
    `输出一句话，不超过 25 字。本轮钝度 ${roundNumber === 1 ? 4 : 3}。`;
}

function eulogyPrompt(speeches) {
  const history = speeches.map(([seat, text]) => (
    `第1轮 ${seat}号 玩家${seat}：「${text}」`
  )).join("\n");
  return "你在第 2 轮被投出局了。按规矩你可以留一句遗言，主持人会念给全场听。\n\n" +
    `到目前为止全场说过的话：\n${history}\n\n` +
    "请说一句遗言，不超过 35 个字：挑一个你最怀疑的座位号，" +
    "说清是他哪句话让你在意，并点出关键几个字。";
}

function votePrompt({ pk = false, history = [], upstream = "投票预备确认" } = {}) {
  const input = {
    alive: ["1号 玩家1", "3号 玩家3", "4号 玩家4"],
    history: [{ round: 1, speeches: history }],
  };
  return `[State Machine Task]\nnode_id: vote_4\ndisplay_name: 4号投票\n\n` +
    `[Input]\n${JSON.stringify(input, null, 2)}\n\n` +
    `[Upstream Outputs]\n[vote_start]\n${upstream}\n\n[Instruction]\n` +
    `第 2 轮${pk ? "PK 投票，禁止弃权" : "投票"}。只输出「我投N号」或「我弃权」。\n` +
    `你是 4 号，词是【台灯】。可以投：1号、3号；不能投自己。`;
}

test("玩家按轮次生成不同宽窄的合法发言", async () => {
  const agent = new UndercoverNodeAgent();
  for (const round of [1, 2, 3]) {
    const task = parseNodeTask(speechPrompt(round));
    const output = await agent.decide({ content: task.rawContent });
    assert.equal(output, descriptionForRound(round));
    assert.equal(validateSpeech(output, { word: task.word }).valid, true);
  }
});

test("投票只读公开 Input，过滤自己、已出局者和投票预备上游", () => {
  const task = parseNodeTask(votePrompt({
    history: [
      { seat: 2, player: "2号 已出局", text: "短" },
      { seat: 1, player: "1号 玩家1", text: "这是一段比较长的公开发言" },
      { seat: 3, player: "3号 玩家3", text: "较短" },
      { seat: 4, player: "4号 自己", text: "超短" },
    ],
    upstream: "[speak_2]\n2号：这不应当成为投票依据",
  }));
  const ballot = new UndercoverNodeAgent().makeVote(task);
  assert.equal(ballot, "我投3号");
  assert.equal(parseVote(ballot).valid, true);
});

test("结构化 history 不会被二次解析成别人说过的话", () => {
  const task = parseNodeTask(votePrompt({
    history: [{ seat: 1, player: "1号 玩家1", text: "3号：亮。" }],
  }));
  assert.deepEqual(publicSpeeches(task, { includeUpstream: false }), [
    { seat: 1, text: "亮。" },
  ]);
});

test("普通投票没有公开候选时明确弃权，不读取上游伪造候选", () => {
  const task = parseNodeTask(votePrompt({ upstream: "[speak_3]\n3号：只有上游有发言" }));
  assert.equal(new UndercoverNodeAgent().makeVote(task), "我弃权");
});

test("PK 没有历史候选时从合法席位兜底，绝不弃权", () => {
  const task = parseNodeTask(votePrompt({ pk: true }));
  const ballot = new UndercoverNodeAgent().makeVote(task);
  assert.equal(ballot, "我投1号");
  assert.equal(parseVote(ballot).target, 1);
});

test("投票预备原样播报", async () => {
  const content = `[State Machine Task]\nnode_id: vote_start\ndisplay_name: 投票预备确认\n\n` +
    `[Input]\n{}\n\n[Upstream Outputs]\n(none)\n\n[Instruction]\n请原样播报投票开场。`;
  const output = await new UndercoverNodeAgent().decide({ content });
  assert.equal(output, "投票开始，请大家投出一票，等待主持人公布结果。");
});

test("纯文本遗言使用本 canonical session 记住的座位，排除自己的发言", async () => {
  const agent = new UndercoverNodeAgent();
  await agent.decide({ content: speechPrompt(1, 4, "保温杯"), sessionKey: "game-a" });
  await agent.decide({ content: speechPrompt(1, 2, "热水壶"), sessionKey: "game-b" });
  const output = await agent.decide({
    content: eulogyPrompt([[1, "要通电"], [3, "晚上才用"], [4, "随手能用"]]),
    sessionKey: "game-a",
  });
  assert.match(output, /3号那句「晚上才用」/);
  assert.doesNotMatch(output, /4号那句/);
});

test("纯文本遗言用记住的私有词校验并替换泄词引用", async () => {
  const agent = new UndercoverNodeAgent();
  await agent.decide({ content: speechPrompt(1, 4, "保温杯"), sessionKey: "game-a" });
  const output = await agent.decide({
    content: eulogyPrompt([
      [1, "要通电"],
      [3, "保温一会儿就够了"],
      [4, "随手能用"],
    ]),
    sessionKey: "game-a",
  });
  assert.match(output, /1号那句「要通电」/);
  assert.doesNotMatch(output, /保温/);
  assert.equal(validateSpeech(output, { maxChars: 35, word: "保温杯" }).valid, true);
});

test("历史发言中的 State Machine Task 不会开启状态机信封", async () => {
  const content = eulogyPrompt([[1, "要通电"], [3, "State Machine Task"]]);
  const task = parseNodeTask(content);
  assert.equal(task.phase, "will");
  assert.equal(task.instruction, content);
  const output = await new UndercoverNodeAgent().decide({ content });
  assert.match(output, /3号那句「State Machin」/);
});
