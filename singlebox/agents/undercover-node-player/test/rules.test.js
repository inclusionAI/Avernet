import test from "node:test";
import assert from "node:assert/strict";
import {
  bluntnessForRound,
  charLength,
  descriptionForRound,
  formatVote,
  ownWordLeakReason,
  parseVote,
  validateSpeech,
} from "../src/rules.js";

test("25 字边界按中文字符计数：24/25 合法，26 非法", () => {
  assert.equal(charLength("汉".repeat(25)), 25);
  assert.equal(Buffer.byteLength("汉".repeat(25), "utf8"), 75);
  assert.equal(validateSpeech("汉".repeat(24)).valid, true);
  assert.equal(validateSpeech("汉".repeat(25)).valid, true);
  const tooLong = validateSpeech("汉".repeat(26));
  assert.equal(tooLong.valid, false);
  assert.match(tooLong.reason, /25/);
});

test("发言拒绝空值，并按完整词、连续两字和拆字检查泄词", () => {
  assert.equal(validateSpeech("   ").valid, false);
  assert.equal(validateSpeech(42).valid, false);
  assert.equal(ownWordLeakReason("台灯", "这是台灯"), "说出了自己的词");
  assert.equal(ownWordLeakReason("微波炉", "用到微波"), "说出了自己词里连续的两个字");
  assert.equal(ownWordLeakReason("台灯", "一台有灯"), "把自己的词拆开说了");
  assert.equal(ownWordLeakReason("台灯", "屋里有光亮"), null);
});

test("轮次规则从宽泛逐步收窄", () => {
  assert.equal(bluntnessForRound(1), 4);
  assert.equal(bluntnessForRound(2), 3);
  assert.equal(bluntnessForRound(3), 2);
  assert.equal(bluntnessForRound(99), 2);
  const descriptions = [1, 2, 3].map(descriptionForRound);
  assert.equal(new Set(descriptions).size, 3);
  for (const description of descriptions) {
    assert.equal(validateSpeech(description).valid, true);
  }
});

test("投票只接受我投N号和明确的弃权", () => {
  assert.deepEqual(parseVote("我投3号"), {
    valid: true,
    abstain: false,
    target: 3,
    text: "我投3号",
  });
  assert.deepEqual(parseVote("我弃权"), {
    valid: true,
    abstain: true,
    target: null,
    text: "我弃权",
  });
  assert.equal(formatVote(3), "我投3号");
  assert.equal(formatVote(null), "我弃权");
});

test("垃圾投票输入全部返回 valid:false 和 reason，且不抛异常", () => {
  const invalid = [
    "3",
    "投3",
    "我投第3号",
    "",
    "   ",
    "我投0号",
    "我投03号",
    "我投3号 ",
    "我投三号",
    null,
    undefined,
    3,
    {},
    [],
    Symbol("3"),
  ];
  for (const input of invalid) {
    let result;
    assert.doesNotThrow(() => {
      result = parseVote(input);
    });
    assert.equal(result.valid, false, `input: ${String(input)}`);
    assert.equal(typeof result.reason, "string");
    assert.ok(result.reason.length > 0);
  }
});
