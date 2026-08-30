import { HighlightStyle, StreamLanguage, syntaxHighlighting } from '@codemirror/language';
import { EditorView } from '@codemirror/view';
import { tags as highlightTags } from '@lezer/highlight';

interface YamlParserState {
  blockScalarIndent: number | null;
}

/** 自定义 YAML stream parser（来自 open-claw，无需额外 @codemirror/lang-yaml 依赖）。 */
export const yamlLanguage = StreamLanguage.define<YamlParserState>({
  name: 'yaml',
  startState: () => ({ blockScalarIndent: null }),
  token(stream, state) {
    if (stream.sol()) {
      const isBlankLine = /^\s*$/.test(stream.string);
      if (state.blockScalarIndent !== null && (isBlankLine || stream.indentation() > state.blockScalarIndent)) {
        stream.skipToEnd();
        return 'string';
      }
      if (state.blockScalarIndent !== null) {
        state.blockScalarIndent = null;
      }
      if (stream.match(/^\s*(?:---|\.\.\.)\s*(?:#.*)?$/)) {
        return 'meta';
      }
    }

    if (stream.eatSpace()) return null;
    if (stream.peek() === '#') {
      stream.skipToEnd();
      return 'comment';
    }
    if (stream.match(/^-\s*/)) return 'punctuation';
    if (stream.match(/^(?:"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s:#][^:#]*?)(?=\s*:)/)) {
      return 'propertyName';
    }
    if (stream.match(/^:\s*/)) return 'punctuation';
    if (stream.match(/^[|>][+-]?/)) {
      state.blockScalarIndent = stream.indentation();
      return 'operator';
    }
    if (stream.match(/^"(?:[^"\\]|\\.)*"?/)) return 'string';
    if (stream.match(/^'(?:[^']|'')*'?/)) return 'string';
    if (stream.match(/^(?:true|false|yes|no|on|off)\b/i)) return 'bool';
    if (stream.match(/^(?:null|~)\b/i)) return 'null';
    if (stream.match(/^[-+]?(?:0x[\da-f]+|0o[0-7]+|(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\b/i)) {
      return 'number';
    }
    if (stream.match(/^[{}[\],]/)) return 'punctuation';
    if (stream.match(/^[*&][\w-]+/)) return 'variableName';
    if (stream.match(/^![\w:/.-]+/)) return 'keyword';
    if (stream.match(/^[^\s#,[\]{}]+/)) return 'string';

    stream.next();
    return null;
  },
  languageData: {
    commentTokens: { line: '#' },
  },
});

const yamlHighlightStyle = HighlightStyle.define([
  { tag: highlightTags.propertyName, color: '#7c3aed', fontWeight: '600' },
  { tag: highlightTags.string, color: '#0f766e' },
  { tag: highlightTags.number, color: '#2563eb' },
  { tag: [highlightTags.bool, highlightTags.null], color: '#b45309' },
  { tag: highlightTags.keyword, color: '#be123c' },
  { tag: highlightTags.variableName, color: '#4f46e5' },
  { tag: highlightTags.comment, color: '#94a3b8', fontStyle: 'italic' },
  { tag: highlightTags.meta, color: '#64748b' },
  {
    tag: [highlightTags.punctuation, highlightTags.operator],
    color: '#64748b',
  },
]);

/** YAML 编辑器扩展：语法高亮 + 自动换行。 */
export const yamlEditorExtensions = [yamlLanguage, syntaxHighlighting(yamlHighlightStyle), EditorView.lineWrapping];
