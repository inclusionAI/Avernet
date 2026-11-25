// JSON stream parser + 高亮（仿照 yamlLanguage.ts，避免引入 @codemirror/lang-json 依赖）。
import { HighlightStyle, StreamLanguage, syntaxHighlighting } from '@codemirror/language';
import { EditorView } from '@codemirror/view';
import { tags as highlightTags } from '@lezer/highlight';

const jsonLanguage = StreamLanguage.define<null>({
  name: 'json',
  startState: () => null,
  token(stream) {
    if (stream.eatSpace()) return null;
    // 后接冒号的字符串视为属性名
    if (stream.match(/^"(?:[^"\\]|\\.)*"(?=\s*:)/)) return 'propertyName';
    if (stream.match(/^"(?:[^"\\]|\\.)*"/)) return 'string';
    if (stream.match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][-+]?\d+)?/)) return 'number';
    if (stream.match(/^(?:true|false)\b/)) return 'bool';
    if (stream.match(/^null\b/)) return 'null';
    if (stream.match(/^[{}[\],:]/)) return 'punctuation';
    stream.next();
    return null;
  },
});

const jsonHighlightStyle = HighlightStyle.define([
  { tag: highlightTags.propertyName, color: '#7c3aed', fontWeight: '600' },
  { tag: highlightTags.string, color: '#0f766e' },
  { tag: highlightTags.number, color: '#2563eb' },
  { tag: [highlightTags.bool, highlightTags.null], color: '#b45309' },
  { tag: highlightTags.punctuation, color: '#64748b' },
]);

/** JSON 编辑器扩展：语法高亮 + 自动换行。 */
export const jsonEditorExtensions = [jsonLanguage, syntaxHighlighting(jsonHighlightStyle), EditorView.lineWrapping];
