import { ConversionError, convertAgentCard, convertMessage } from './converter.js';
import { parseInput } from './input-parser.js';
import { convertStructuredMessage } from './message-mapper.js';

const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const isMessage = value => isObject(value) && (Object.hasOwn(value, 'parts') || Object.hasOwn(value, 'messageId'))
  && (Object.hasOwn(value, 'role') || value.kind === 'message');

export function convertInput(text, mode, options = {}) {
  if (!['card', 'message'].includes(mode)) throw new ConversionError(['转换模式必须是 card 或 message。']);
  const parsed = parseInput(text, { includeFields: true });
  const parsedNoteRows = parsed.notes.map(description => ({ source: '输入文本', target: '—', description }));
  const nested = isObject(parsed.value) ? parsed.value.message : undefined;
  const structured = isMessage(parsed.value) || (isObject(nested) && (Object.hasOwn(nested, 'parts') || Object.hasOwn(nested, 'role')));
  try {
    const result = mode === 'card' ? convertAgentCard(parsed.value)
      : structured ? convertStructuredMessage(parsed.value, options) : convertMessage(parsed.value, options);
    return {
      ...result,
      notes: [...parsed.notes, ...result.notes],
      noteRows: [...parsedNoteRows, ...result.noteRows],
      normalizedInput: parsed.value,
      inputFields: parsed.fields,
      source: parsed.source,
      mapping: mode === 'card' ? 'card' : structured ? 'structured-message' : 'data'
    };
  } catch (error) {
    error.inputResult = { normalizedInput: parsed.value, source: parsed.source, notes: parsed.notes, noteRows: parsedNoteRows };
    throw error;
  }
}
