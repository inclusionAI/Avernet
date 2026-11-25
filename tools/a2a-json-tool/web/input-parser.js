import { ConversionError, parseJson } from './converter.js';

const has = (object, key) => Object.hasOwn(object, key);
const numberPattern = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;

function sameValue(left, right) {
  if (left === right) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object' || Array.isArray(left) !== Array.isArray(right)) return false;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every(key => has(right, key) && sameValue(left[key], right[key]));
}

export function parseInput(text, options = {}) {
  if (typeof text !== 'string') throw new ConversionError(['输入：请提供文本。']);
  let sourceOffset = text.length - text.trimStart().length;
  let sourceText = text.trim();
  const includeFields = options.includeFields === true;
  const fields = [];
  const notes = new Set();
  if (sourceText.startsWith('```')) {
    const fenced = /^```(?:[a-z0-9_+-]+)?[ \t]*\r?\n([\s\S]*?)\r?\n```$/i.exec(sourceText);
    if (!fenced) throw new ConversionError(['输入：代码围栏不完整，请提供完整围栏或移除围栏。']);
    sourceOffset += sourceText.indexOf('\n') + 1 + fenced[1].length - fenced[1].trimStart().length;
    sourceText = fenced[1].trim();
    notes.add('已移除完整 Markdown 代码围栏。');
  }
  if (!sourceText) throw new ConversionError(['输入：内容不能为空。']);

  // 先判定语法；数值校验失败不能进入宽松解析后变成字符串。
  let strict = false;
  try { JSON.parse(sourceText); strict = true; } catch { /* 非标准输入交给有限语法解析器。 */ }
  if (strict) parseJson(sourceText);

  let position = 0;
  let objectDump = false;
  let depth = 0;
  const skip = () => { while (/\s/.test(sourceText[position] ?? '') && position < sourceText.length) position++; };
  const fail = message => { throw new ConversionError([`输入第 ${position + 1} 个字符：${message}`]); };
  const closed = () => {
    notes.add('已补齐完整值之后缺失的末尾闭合括号；请核对解析结果。');
  };

  function string() {
    const quote = sourceText[position++];
    let value = '';
    while (position < sourceText.length) {
      const char = sourceText[position++];
      if (char === quote) {
        if (quote === "'") notes.add('已将单引号字符串解析为 JSON 字符串。');
        return value;
      }
      if (char.charCodeAt(0) < 32) fail('字符串包含未转义的控制字符。');
      if (char !== '\\') { value += char; continue; }
      if (position === sourceText.length) fail('字符串被截断，无法自动补齐。');
      const escape = sourceText[position++];
      const escapes = { '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' };
      if (escape === "'" && quote === "'") value += "'";
      else if (has(escapes, escape)) value += escapes[escape];
      else if (escape === 'u') {
        const hex = sourceText.slice(position, position + 4);
        if (!/^[0-9a-f]{4}$/i.test(hex)) fail('字符串的 Unicode 转义不完整或不合法。');
        value += String.fromCharCode(parseInt(hex, 16));
        position += 4;
      } else fail('字符串包含不支持的转义。');
    }
    fail('字符串被截断，无法自动补齐。');
  }

  function key() {
    skip();
    if (sourceText[position] === '"' || sourceText[position] === "'") return string();
    const match = /^[^\s:=,{}\[\]()'"\\]+/.exec(sourceText.slice(position));
    if (!match || /\/\*|\/\//.test(match[0])) fail('缺少字段名，或存在无法识别的语法。');
    position += match[0].length;
    notes.add('已解析未加引号的字段名或字符串值。');
    return match[0];
  }

  function record(close, path) {
    const result = {};
    skip();
    if (close && sourceText[position] === close) { position++; return result; }
    while (true) {
      if (position >= sourceText.length) fail('字段内容不完整，缺少字段或值。');
      const start = position;
      const name = key();
      const fieldPath = [...path, name];
      const field = includeFields ? { path: fieldPath, start: sourceOffset + start, end: sourceOffset + position } : null;
      if (field) fields.push(field);
      skip();
      const separator = sourceText[position++];
      if (separator !== ':' && separator !== '=') fail('字段名后应为冒号或等号。');
      if (separator === '=') {
        objectDump = true;
        notes.add('已将对象输出中的等号字段解析为 JSON 属性。');
      }
      skip();
      const valueStart = position;
      const item = value(fieldPath);
      if (field) {
        let valueEnd = position;
        while (valueEnd > valueStart && /\s/.test(sourceText[valueEnd - 1])) valueEnd--;
        field.valueStart = sourceOffset + valueStart;
        field.valueEnd = sourceOffset + valueEnd;
      }
      if (has(result, name)) {
        if (!sameValue(result[name], item)) fail('存在值冲突的重复字段，不能覆盖原值。');
        notes.add('重复字段的值相同，已保留一份。');
      } else Object.defineProperty(result, name, { value: item, enumerable: true, writable: true, configurable: true });
      skip();
      if (position === sourceText.length) { if (close) closed(); return result; }
      if (close && sourceText[position] === close) { position++; return result; }
      if (sourceText[position] !== ',') fail('字段之间缺少逗号，或闭合括号不匹配。');
      position++;
      skip();
      if (position === sourceText.length || (close && sourceText[position] === close)) {
        notes.add('已移除末尾多余的尾逗号。');
        if (position < sourceText.length) position++;
        else if (close) closed();
        return result;
      }
    }
  }

  function array(path) {
    const result = [];
    skip();
    if (sourceText[position] === ']') { position++; return result; }
    while (true) {
      result.push(value([...path, result.length]));
      skip();
      if (position === sourceText.length) { closed(); return result; }
      if (sourceText[position] === ']') { position++; return result; }
      if (sourceText[position] !== ',') fail('数组元素之间缺少逗号，或闭合括号不匹配。');
      position++;
      skip();
      if (position === sourceText.length || sourceText[position] === ']') {
        notes.add('已移除末尾多余的尾逗号。');
        if (position < sourceText.length) position++;
        else closed();
        return result;
      }
    }
  }

  function value(path) {
    skip();
    if (++depth > 200) fail('嵌套层级过深，请缩小输入。');
    try {
      const char = sourceText[position];
      if (!char || ',]})'.includes(char)) fail('缺少完整的字段值。');
      if (char === '"' || char === "'") return string();
      if (char === '{') { position++; return record('}', path); }
      if (char === '[') { position++; return array(path); }
      const constructor = /^[a-z_$][\w.$]*\s*\(/i.exec(sourceText.slice(position));
      if (constructor) {
        position += constructor[0].length;
        objectDump = true;
        notes.add('已移除构造器名称包装，并保留其中全部字段。');
        return record(')', path);
      }
      const start = position;
      while (position < sourceText.length && !',[]{}()'.includes(sourceText[position])) position++;
      const raw = sourceText.slice(start, position).trim();
      if (/^[a-z][a-z0-9+.-]*:[^\s"'\\]+$/i.test(raw)) {
        notes.add('已将未加引号的 URI 值解析为字符串。');
        return raw;
      }
      if (!raw || /[:="'\\]|\/\*|(?:^|\s)\/\//.test(raw)) fail('裸值边界不明确，请补齐逗号或给字符串加引号。');
      if (raw === 'null') return null;
      if (raw === 'true') return true;
      if (raw === 'false') return false;
      if (numberPattern.test(raw)) return parseJson(raw);
      if (/^[+-]?(?:NaN|Infinity)$/i.test(raw) || /^[\d.eE+\-\s]+$/.test(raw)) fail('数值格式无效或不是有限数，请勿省略数字内容。');
      notes.add('已解析未加引号的字段名或字符串值。');
      return raw;
    } finally { depth--; }
  }

  // 只在顶层识别无外层花括号的字段列表，避免将嵌套值误当作字段。
  function isFieldList() {
    const saved = position;
    try {
      if ('{[('.includes(sourceText[position])) return false;
      if (sourceText[position] === '"' || sourceText[position] === "'") string();
      else {
        const match = /^[^\s:=,{}\[\]()'"\\]+/.exec(sourceText.slice(position));
        if (!match) return false;
        position += match[0].length;
      }
      skip();
      return sourceText[position] === ':' || sourceText[position] === '=';
    } finally { position = saved; }
  }

  let result;
  if (isFieldList()) {
    notes.add('已为外层字段片段补充对象结构。');
    result = record(null, []);
  } else result = value([]);
  skip();
  if (position !== sourceText.length) fail('存在多余内容或不匹配的括号，无法确定输入边界。');
  return { value: result, notes: [...notes], source: strict ? 'json' : objectDump ? 'object-dump' : 'fragment', ...(includeFields ? { fields } : {}) };
}
