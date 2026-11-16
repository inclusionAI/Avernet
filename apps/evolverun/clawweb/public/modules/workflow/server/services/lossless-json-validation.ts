// A 1 MiB YAML document can expand to 6 MiB when every byte is JSON-escaped.
// Leave room for wrapper metadata while bounding raw scan work and allocations.
const MAX_JSON_BYTES = 8 * 1024 * 1024;
const MAX_JSON_VALUES = 1024 * 1024;

/** Compare numeric source and the JSON-serialized number without rounding. */
export function assertLosslessNumber(source: string, converted: number): void {
  const invalid = (): never => { throw new Error('Invalid or lossy number'); };
  const decimal = (value: string): { identity: string; digits: string; exponent: bigint; negative: boolean } => {
    const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(value);
    if (!match || (match[5]?.length ?? 0) > 32) return invalid();
    const fraction = match[3] ?? match[4] ?? '';
    let digits = `${match[2] ?? ''}${fraction}`.replace(/^0+/, '');
    const negative = match[1] === '-';
    if (!digits) return { identity: negative ? '-0' : '0', digits: '0', exponent: 0n, negative };
    let exponent = BigInt(match[5] ?? '0') - BigInt(fraction.length);
    // Preserve the linear-time trailing-zero scan used for large inputs.
    let trailing = 0;
    while (trailing < digits.length && digits.charCodeAt(digits.length - 1 - trailing) === 48) trailing++;
    if (trailing) { digits = digits.slice(0, -trailing); exponent += BigInt(trailing); }
    return { identity: `${negative ? '-' : ''}${digits}e${exponent}`, digits, exponent, negative };
  };
  if (!Number.isFinite(converted) || Object.is(converted, -0)) return invalid();
  let decimalSource = source;
  if (/^0[xo]/.test(source)) {
    const radix = /^0([xo])([0-9a-fA-F]+)$/.exec(source);
    if (!radix || (radix[1] === 'o' && !/^[0-7]+$/.test(radix[2]))) return invalid();
    const digits = radix[2].replace(/^0+/, '') || '0';
    // Even a finite JS number cannot represent arbitrarily long integers.
    if (digits.length > (radix[1] === 'x' ? 256 : 342)) return invalid();
    decimalSource = BigInt(`0${radix[1]}${digits}`).toString();
  }
  const original = decimal(decimalSource);
  if (original.identity !== decimal(String(converted)).identity) return invalid();
  if (original.digits !== '0' && original.exponent >= 0n) {
    if (original.exponent > 310n || BigInt(original.digits.length) + original.exponent > 310n) return invalid();
    const exact = BigInt(original.digits + '0'.repeat(Number(original.exponent))) * (original.negative ? -1n : 1n);
    if (!Number.isInteger(converted) || BigInt(converted) !== exact) return invalid();
  }
}

/** Check JSON source before JSON.parse can round numbers or discard object keys. */
export function assertLosslessJson(text: string): void {
  let pos = 0, values = 0;
  const invalid = (): never => { throw new Error('Invalid or lossy JSON'); };
  if (Buffer.byteLength(text, 'utf8') > MAX_JSON_BYTES) invalid();
  const whitespace = () => { while (/[\x20\t\r\n]/.test(text[pos] ?? '\0')) pos++; };
  const string = (): string => {
    const start = pos++;
    while (pos < text.length) {
      if (text[pos] === '"') {
        pos++;
        // This also rejects invalid escapes and unescaped control characters.
        return JSON.parse(text.slice(start, pos)) as string;
      }
      if (text[pos++] === '\\') pos++;
    }
    return invalid();
  };
  const value = (depth: number): void => {
    // Bound recursive descent even for malformed or adversarial stored drafts.
    if (depth > 200 || ++values > MAX_JSON_VALUES) return invalid();
    whitespace();
    const char = text[pos];
    if (char === '"') { string(); return; }
    if (char === '{') {
      pos++; whitespace();
      const keys = new Set<string>();
      if (text[pos] === '}') { pos++; return; }
      while (true) {
        whitespace();
        if (text[pos] !== '"') return invalid();
        const key = string();
        if (keys.has(key)) return invalid();
        keys.add(key); whitespace();
        if (text[pos++] !== ':') return invalid();
        value(depth + 1); whitespace();
        const separator = text[pos++];
        if (separator === '}') return;
        if (separator !== ',') return invalid();
      }
    }
    if (char === '[') {
      pos++; whitespace();
      if (text[pos] === ']') { pos++; return; }
      while (true) {
        value(depth + 1); whitespace();
        const separator = text[pos++];
        if (separator === ']') return;
        if (separator !== ',') return invalid();
      }
    }
    const literal = /^(?:true|false|null)/.exec(text.slice(pos));
    if (literal) { pos += literal[0].length; return; }
    const number = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(pos));
    if (!number) return invalid();
    pos += number[0].length;
    const converted = Number(number[0]);
    assertLosslessNumber(number[0], converted);
  };
  value(0); whitespace();
  if (pos !== text.length) invalid();
}
