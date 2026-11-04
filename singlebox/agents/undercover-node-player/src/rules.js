export const MAX_SPEECH_CHARS = 25;

const ROUND_DESCRIPTIONS = Object.freeze({
  1: "有些人会在特定场景用到它。",
  2: "日常能接触到，但具体用法因人而异。",
  3: "常在固定场景使用，细节上有明显讲究。",
});

export function charLength(text) {
  return Array.from(text).length;
}

export function bluntnessForRound(roundNumber) {
  if (roundNumber === 1) return 4;
  if (roundNumber === 2) return 3;
  return 2;
}

export function descriptionForRound(roundNumber) {
  if (roundNumber === 1) return ROUND_DESCRIPTIONS[1];
  if (roundNumber === 2) return ROUND_DESCRIPTIONS[2];
  return ROUND_DESCRIPTIONS[3];
}

export function validateSpeech(text, { maxChars = MAX_SPEECH_CHARS, word = "" } = {}) {
  if (typeof text !== "string") {
    return { valid: false, reason: "发言必须是字符串" };
  }
  const normalized = text.trim();
  if (!normalized) {
    return { valid: false, reason: "发言不能为空" };
  }
  // 主持人按字符而不是 UTF-8 字节或 UTF-16 code unit 计数。
  if (charLength(normalized) > maxChars) {
    return { valid: false, reason: `发言超过 ${maxChars} 字` };
  }
  const leakReason = ownWordLeakReason(word, normalized);
  if (leakReason) {
    return { valid: false, reason: leakReason };
  }
  return { valid: true, reason: null, text: normalized };
}

export function ownWordLeakReason(word, text) {
  if (typeof word !== "string" || !word || typeof text !== "string") return null;
  if (text.includes(word)) return "说出了自己的词";

  const chars = Array.from(word);
  for (let index = 0; index < chars.length - 1; index += 1) {
    if (text.includes(chars[index] + chars[index + 1])) {
      return "说出了自己词里连续的两个字";
    }
  }
  if (chars.every((char) => text.includes(char))) {
    return "把自己的词拆开说了";
  }
  return null;
}

export function formatVote(target = null, { allowAbstain = true } = {}) {
  if (target === null && allowAbstain) return "我弃权";
  if (!Number.isSafeInteger(target) || target < 1) return "";
  return `我投${target}号`;
}

export function parseVote(input) {
  if (typeof input !== "string") {
    return { valid: false, reason: "投票必须是字符串" };
  }
  if (!input.trim()) {
    return { valid: false, reason: "投票不能为空" };
  }
  if (input === "我弃权") {
    return { valid: true, abstain: true, target: null, text: input };
  }

  //  deliberately strict: the referee receives an artifact, not natural language.
  const match = /^我投([1-9]\d*)号$/.exec(input);
  if (!match) {
    return {
      valid: false,
      reason: "只接受「我投N号」（N 为 1-based 座位号）或「我弃权」",
    };
  }
  const target = Number(match[1]);
  if (!Number.isSafeInteger(target)) {
    return { valid: false, reason: "座位号超出安全整数范围" };
  }
  return { valid: true, abstain: false, target, text: input };
}
