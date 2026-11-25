import { JSON_SCHEMA, load } from 'js-yaml';

function decimalValue(source: string): string | null {
  const text = source.replace(/_/g, '').trim();
  const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:e([+-]?\d+))?$/i.exec(text);
  if (!match || (match[5]?.length ?? 0) > 32) return null;
  const fraction = match[3] ?? match[4] ?? '';
  let digits = `${match[2] ?? ''}${fraction}`.replace(/^0+/, '');
  if (!digits) return match[1] === '-' ? '-0' : '0';
  let exponent = BigInt(match[5] ?? '0') - BigInt(fraction.length);
  let trailing = 0;
  while (trailing < digits.length && digits.charCodeAt(digits.length - 1 - trailing) === 48) trailing += 1;
  if (trailing) {
    digits = digits.slice(0, -trailing);
    exponent += BigInt(trailing);
  }
  return `${match[1] === '-' ? '-' : ''}${digits}e${exponent}`;
}

interface ParseState { position: number; kind: string | null; result: unknown }

export function parseLosslessAuthoringText(text: string): unknown {
  const starts: number[] = [];
  return load(text, {
    schema: JSON_SCHEMA,
    listener: (event: string, state: ParseState) => {
      if (event === 'open') {
        starts.push(state.position);
        return;
      }
      const start = starts.pop();
      if (typeof state.result !== 'number' || state.kind !== 'scalar') return;
      const value = state.result;
      const source = text.slice(start ?? 0, state.position).trim();
      const spelling = decimalValue(source);
      if (!Number.isFinite(value) || Object.is(value, -0) ||
        Number.isInteger(value) && !Number.isSafeInteger(value) ||
        spelling === null || spelling !== decimalValue(String(value))) {
        throw new Error('工作流数字无法无损读取；请使用字符串保留精确值');
      }
    },
  });
}
