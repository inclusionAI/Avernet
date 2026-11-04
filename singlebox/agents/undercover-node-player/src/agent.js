import {
  MAX_SPEECH_CHARS,
  bluntnessForRound,
  charLength,
  descriptionForRound,
  formatVote,
  parseVote,
  validateSpeech,
} from "./rules.js";

export const DEFAULT_PERSONA = "平实、克制，只依据自己可见的公开发言进行属性推理";

const VOTE_OPENING = "投票开始，请大家投出一票，等待主持人公布结果。";
const SPEECH_FALLBACKS = [
  "这个描述先保留，免得说得太直白。",
  "具体感受因人而异，要看使用场合。",
];

function abortError() {
  const error = new Error("task aborted");
  error.name = "AbortError";
  return error;
}

function throwIfAborted(signal) {
  if (signal?.aborted) throw abortError();
}

function splitSections(text) {
  const headerPattern = /^[ \t]*(\[(?:Input|Upstream Outputs|Instruction)\])[ \t]*\r?$/gm;
  const headers = [...text.matchAll(headerPattern)];
  const sections = { "[Input]": "", "[Upstream Outputs]": "", "[Instruction]": "" };
  headers.forEach((header, index) => {
    let start = header.index + header[0].length;
    if (text[start] === "\n") start += 1;
    const end = index + 1 < headers.length ? headers[index + 1].index : text.length;
    sections[header[1]] = text.slice(start, end).trim();
  });
  return sections;
}

function inputObject(input) {
  if (!input) return null;
  try {
    const parsed = JSON.parse(input);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function positiveSeats(values) {
  return [...new Set(values.filter((seat) => Number.isSafeInteger(seat) && seat > 0))]
    .sort((left, right) => left - right);
}

function outerInstruction(instruction) {
  const marker = "到目前为止全场说过的话：\n";
  const start = instruction.indexOf(marker);
  if (start < 0) return instruction;
  const historyStart = start + marker.length;
  const historyEnd = instruction.indexOf("\n\n", historyStart);
  return historyEnd < 0
    ? instruction.slice(0, start)
    : instruction.slice(0, start) + instruction.slice(historyEnd + 2);
}

function classifyPhase(nodeId, displayName, instruction) {
  const outer = outerInstruction(instruction);
  if (nodeId.includes("vote_start") || displayName.includes("投票预备")) return "vote_start";
  if (/(?:^|_)will(?:_|$)/.test(nodeId) || displayName.includes("遗言") || outer.includes("遗言")) {
    return "will";
  }
  if (/(?:^|_)vote(?:_|$)/.test(nodeId) || /投票|我投\d|票箱|开投/.test(outer)) return "vote";
  if (nodeId.includes("speak") || /发言|描述你的词|说钝一点/.test(outer)) return "speech";
  return "unknown";
}

export function parseNodeTask(content) {
  const raw = typeof content === "string" ? content : "";
  const sections = splitSections(raw);
  const nodeId = /(?:^|\n)node_id:\s*(\S+)/.exec(raw)?.[1] || "unknown";
  const displayName = /(?:^|\n)display_name:\s*([^\r\n]+)/.exec(raw)?.[1]?.trim() || "";
  const instruction = raw.includes("State Machine Task")
    ? sections["[Instruction]"]
    : raw;

  const seatMatch = /你是\s*(\d+)\s*号/.exec(instruction);
  const wordMatch = /词是【(.+?)】|【(.+?)】/.exec(instruction);
  const roundMatch = /第\s*(\d+)\s*轮/.exec(instruction);
  const bluntnessMatch = /(?:钝度|钝到|至少)\s*(\d+)/.exec(instruction);
  const maxCharsMatch = /不超过\s*(\d+)\s*字/.exec(instruction);
  const roundNumber = roundMatch ? Number(roundMatch[1]) : null;
  const data = inputObject(sections["[Input]"]);

  const aliveSeats = Array.isArray(data?.alive)
    ? positiveSeats(data.alive.map((item) => {
      if (Number.isSafeInteger(item)) return item;
      if (typeof item !== "string") return null;
      const match = /^\s*(\d+)\s*号/.exec(item);
      return match ? Number(match[1]) : null;
    }))
    : [];
  const allowedText = /可以投[：:]\s*([^\n；;。]*)/.exec(instruction)?.[1] || "";
  const allowedTargets = positiveSeats(
    [...allowedText.matchAll(/(\d+)\s*号/g)].map((match) => Number(match[1])),
  );

  return {
    nodeId,
    displayName,
    input: sections["[Input]"],
    upstreamOutputs: sections["[Upstream Outputs]"],
    instruction,
    rawContent: raw,
    seat: seatMatch ? Number(seatMatch[1]) : null,
    word: wordMatch ? (wordMatch[1] || wordMatch[2]) : "",
    roundNumber,
    bluntness: bluntnessMatch ? Number(bluntnessMatch[1]) : bluntnessForRound(roundNumber),
    maxChars: maxCharsMatch ? Number(maxCharsMatch[1]) : MAX_SPEECH_CHARS,
    isPk: instruction.includes("PK"),
    allowAbstain: !instruction.includes("禁止弃权") && !instruction.includes("不能弃权"),
    phase: classifyPhase(nodeId, displayName, instruction),
    aliveSeats,
    allowedTargets,
  };
}

function decodeHistory(text) {
  const data = inputObject(text);
  if (!data || !Array.isArray(data.history)) return { speeches: [], decoded: false };
  const speeches = [];
  for (const round of data.history) {
    if (!round || !Array.isArray(round.speeches)) continue;
    for (const speech of round.speeches) {
      if (!speech || !Number.isSafeInteger(speech.seat) || speech.seat < 1) continue;
      if (typeof speech.text !== "string" || !speech.text.trim()) continue;
      speeches.push({
        seat: speech.seat,
        text: speech.text.trim().replace(/^\d+\s*号\s*[:：]\s*/, ""),
      });
    }
  }
  return { speeches, decoded: true };
}

function legacySpeeches(text) {
  const speeches = [];
  const blocks = /\[speak_(\d+)\]\s*\n([\s\S]*?)(?=\n\[speak_\d+\]|$)/g;
  for (const match of text.matchAll(blocks)) {
    const body = match[2].trim().replace(/^\d+\s*号\s*[:：]\s*/, "");
    if (body) speeches.push({ seat: Number(match[1]), text: body });
  }
  for (const match of text.matchAll(/(\d+)\s*号\s*[:：]\s*([^\n]+)/g)) {
    if (match[2].trim()) speeches.push({ seat: Number(match[1]), text: match[2].trim() });
  }
  return speeches;
}

export function publicSpeeches(task, { includeUpstream = true } = {}) {
  const sources = includeUpstream ? [task.input, task.upstreamOutputs] : [task.input];
  const result = [];
  const seen = new Set();
  for (const source of sources) {
    const decoded = decodeHistory(source);
    // Once structured history is recognized, do not reinterpret text inside it as
    // legacy "N号：" lines; that would invent speeches and change attribution.
    const speeches = decoded.decoded ? decoded.speeches : legacySpeeches(source);
    for (const speech of speeches) {
      const key = `${speech.seat}\0${speech.text}`;
      if (!seen.has(key)) {
        seen.add(key);
        result.push(speech);
      }
    }
  }
  return result;
}

export class UndercoverNodeAgent {
  constructor({ persona = DEFAULT_PERSONA } = {}) {
    this.persona = persona;
    this.privateContexts = new Map();
  }

  retainPrivateContext(task, sessionKey) {
    if (typeof sessionKey !== "string" || !sessionKey) return task;
    const previous = this.privateContexts.get(sessionKey);
    if (task.phase === "will") {
      // Restore seat/word from canonical session for last-word tasks when the
      // instruction lacks "你是 N 号" and private words.
      if (task.seat === null && previous) task.seat = previous.seat;
      if (!task.word && previous) task.word = previous.word;
      return task;
    }
    if (task.phase !== "speech" && task.phase !== "vote") return task;

    const context = {
      seat: task.seat ?? previous?.seat ?? null,
      word: task.word || previous?.word || "",
    };
    if (context.seat !== null || context.word) this.privateContexts.set(sessionKey, context);
    return task;
  }

  makeSpeech(task) {
    const maxChars = Math.min(task.maxChars, MAX_SPEECH_CHARS);
    const candidates = [descriptionForRound(task.roundNumber), ...SPEECH_FALLBACKS];
    for (const candidate of candidates) {
      const checked = validateSpeech(candidate, { maxChars, word: task.word });
      if (checked.valid) return checked.text;
    }
    throw new Error("没有不泄词且符合字数限制的发言");
  }

  eligibleTargets(task) {
    const pools = [];
    if (task.allowedTargets.length) pools.push(new Set(task.allowedTargets));
    if (task.aliveSeats.length) pools.push(new Set(task.aliveSeats));
    let eligible = pools.length ? new Set(pools[0]) : null;
    for (const pool of pools.slice(1)) {
      eligible = new Set([...eligible].filter((seat) => pool.has(seat)));
    }
    if (!eligible) {
      eligible = new Set(publicSpeeches(task, { includeUpstream: false }).map((speech) => speech.seat));
    }
    if (task.seat !== null) eligible.delete(task.seat);
    return eligible;
  }

  makeVote(task) {
    // Vote upstream is only vote_start confirmation, never other players' ballots.
    // Use only structured/public [Input] history; no private chats or global transcript.
    const speeches = publicSpeeches(task, { includeUpstream: false });
    const eligible = this.eligibleTargets(task);
    const candidates = positiveSeats(
      [...new Set(speeches.map((speech) => speech.seat))].filter((seat) => eligible.has(seat)),
    );
    const allowAbstain = task.allowAbstain && !task.isPk;

    let target = null;
    let shortest = Infinity;
    for (const speech of speeches) {
      if (!candidates.includes(speech.seat)) continue;
      const length = charLength(speech.text);
      if (length < shortest || (length === shortest && speech.seat < target)) {
        shortest = length;
        target = speech.seat;
      }
    }
    if (target === null && candidates.length) target = candidates[0];
    if (target === null && !allowAbstain) {
      // PK cannot abstain; use an explicitly eligible non-self seat as a last resort.
      target = positiveSeats([...eligible])[0] ?? null;
    }
    if (target === null && !allowAbstain) throw new Error("PK 没有合法投票目标");

    const ballot = formatVote(target, { allowAbstain });
    const parsed = parseVote(ballot);
    if (!parsed.valid) throw new Error(`无法生成合法票面：${parsed.reason}`);
    return ballot;
  }

  makeLastWords(task) {
    const history = [];
    const pattern = /第\s*\d+\s*轮(?:\s*PK)?\s*(\d+)\s*号\s*[^\s：:「]+：「([^」]*)」/g;
    for (const match of task.instruction.matchAll(pattern)) {
      if (Number(match[1]) !== task.seat && match[2].trim()) {
        history.push({ seat: Number(match[1]), text: match[2].trim() });
      }
    }
    const quoted = history.at(-1);
    const candidates = quoted
      ? [`我比较在意${quoted.seat}号那句「${Array.from(quoted.text).slice(0, 12).join("")}」。`]
      : [];
    candidates.push("我没有更多补充，请大家继续核对公开发言。");
    for (const candidate of candidates) {
      const checked = validateSpeech(candidate, { maxChars: 35, word: task.word });
      if (checked.valid) return checked.text;
    }
    throw new Error("没有不泄词的遗言可回传");
  }

  async decide({ content, signal, sessionKey }) {
    throwIfAborted(signal);
    const task = this.retainPrivateContext(parseNodeTask(content), sessionKey);
    let output;
    if (task.phase === "vote_start") output = VOTE_OPENING;
    else if (task.phase === "vote") output = this.makeVote(task);
    else if (task.phase === "will") output = this.makeLastWords(task);
    else output = this.makeSpeech(task);
    throwIfAborted(signal);
    return output;
  }
}
