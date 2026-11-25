import { ConversionError, convertAgentCard, convertMessage } from './converter.js';
import { parseInput } from './input-parser.js';
import { convertStructuredMessage, getMessageSourcePath } from './message-mapper.js';

export function convertInput(text, mode, options = {}) {
  if (!['card', 'message'].includes(mode)) throw new ConversionError(['转换模式必须是 card 或 message。']);
  const parsed = parseInput(text, { includeFields: true });
  const parsedNoteRows = parsed.notes.map(description => ({ source: '输入文本', target: '—', description }));
  const structured = getMessageSourcePath(parsed.value) !== null;
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
