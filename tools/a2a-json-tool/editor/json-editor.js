import { Compartment, EditorSelection, EditorState, Prec, StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView, drawSelection, keymap, lineNumbers, placeholder } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import {
  bracketMatching, codeFolding, defaultHighlightStyle, ensureSyntaxTree, foldAll as foldAllCode,
  foldedRanges, foldEffect, foldGutter, foldKeymap, syntaxHighlighting, syntaxTree,
  unfoldAll as unfoldAllCode, unfoldEffect
} from '@codemirror/language';
import { json } from '@codemirror/lang-json';

const setHighlight = StateEffect.define();
const fieldHighlight = StateField.define({
  create: () => Decoration.none,
  update(value, transaction) {
    if (transaction.docChanged) value = Decoration.none;
    for (const effect of transaction.effects) {
      if (effect.is(setHighlight)) {
        value = effect.value
          ? Decoration.set([Decoration.mark({ class: 'cm-field-link' }).range(effect.value.from, effect.value.to)])
          : Decoration.none;
      }
    }
    return value;
  },
  provide: field => EditorView.decorations.from(field)
});

const theme = EditorView.theme({
  '&': { height: '420px', fontSize: '12px', color: '#344967', backgroundColor: '#fcfdff' },
  '&.cm-focused': { outline: 'none', boxShadow: 'inset 0 0 0 2px #6681e0' },
  '.cm-scroller': { overflow: 'auto', fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace', lineHeight: '1.85' },
  '.cm-content': { padding: '18px 0', caretColor: '#2845d2' },
  '.cm-line': { padding: '0 12px' },
  '.cm-gutters': { backgroundColor: '#f6f8fd', color: '#95a0b3', borderRight: '1px solid #e9edf4' },
  '.cm-lineNumbers .cm-gutterElement': { minWidth: '28px', paddingLeft: '6px' },
  '.cm-foldGutter .cm-gutterElement': { padding: '0 5px', cursor: 'pointer' },
  '.cm-foldPlaceholder': { backgroundColor: '#edf0fc', color: '#4059ad', border: '1px solid #cdd6ee', borderRadius: '3px', padding: '0 4px' },
  '.cm-matchingBracket': { color: '#1c5f96', backgroundColor: '#d8eaff', outline: '1px solid #7ca9dc' },
  '.cm-nonmatchingBracket': { color: '#a93838', backgroundColor: '#ffe3e3', outline: '1px solid #dc9292' },
  '.cm-field-link': { backgroundColor: '#ffe09a', borderRadius: '3px', boxShadow: '0 0 0 1px #e5ad38' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: '#dce5ff' },
  '.cm-content[aria-invalid="true"]': { boxShadow: 'inset 3px 0 0 #c65d5d' },
  '@media (max-width: 520px)': { '.cm-content': { padding: '14px 0' }, '.cm-line': { padding: '0 8px' } }
});

export function createJsonState(text, extensions = []) {
  return EditorState.create({ doc: text, extensions: [json(), codeFolding(), ...extensions] });
}

const matchingBracket = Decoration.mark({ class: 'cm-matchingBracket' });
const nonmatchingBracket = Decoration.mark({ class: 'cm-nonmatchingBracket' });

// CM 的文本扫描也会匹配同一字符串里的括号；JSON 中它们是内容，不是结构。
export function renderJsonBracketMatch(match, state) {
  const tokens = match.end ? [match.start, match.end] : [match.start];
  const tree = syntaxTree(state);
  for (const token of tokens) {
    for (let node = tree.resolveInner(token.from, 1); node; node = node.parent) {
      if (node.name === 'String' || node.name === 'PropertyName') return [];
    }
  }
  const mark = match.matched ? matchingBracket : nonmatchingBracket;
  return tokens.map(token => mark.range(token.from, token.to));
}

// 只在语法树里的对象/数组边界折叠，字符串中的括号不会成为层级。
export function foldRangesAtDepth(state, depth) {
  if (!Number.isInteger(depth) || depth < 1) throw new RangeError('折叠层级必须为正整数。');
  const tree = ensureSyntaxTree(state, state.doc.length, 100) ?? syntaxTree(state);
  const ranges = [];
  tree.iterate({
    enter(ref) {
      if (!['Object', 'Array'].includes(ref.name)) return;
      let level = 0;
      for (let node = ref.node; node; node = node.parent) {
        if (['Object', 'Array'].includes(node.name)) level++;
      }
      if (level !== depth) return;
      const expectedEnd = ref.name === 'Object' ? '}' : ']';
      if (ref.to > ref.from + 2 && state.sliceDoc(ref.to - 1, ref.to) === expectedEnd) {
        ranges.push({ from: ref.from + 1, to: ref.to - 1 });
      }
      return false;
    }
  });
  return ranges;
}

export function createJsonEditor(host, { label, readOnly = false, onChange, onDoubleClick, onConvert }) {
  const accessibility = new Compartment();
  let invalid = false;
  const attributes = () => ({
    'aria-label': label,
    'aria-readonly': String(readOnly),
    'aria-invalid': String(invalid),
    'aria-describedby': host.getAttribute('aria-describedby') ?? '',
    spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off'
  });
  const extensions = [
    history(), lineNumbers(), foldGutter(), bracketMatching({ renderMatch: renderJsonBracketMatch }), drawSelection(),
    syntaxHighlighting(defaultHighlightStyle), fieldHighlight, theme,
    EditorState.readOnly.of(readOnly),
    EditorState.phrases.of({ 'Fold line': '折叠此层', 'Unfold line': '展开此层', 'folded code': '已折叠内容', unfold: '展开', 'Folded lines': '已折叠行', 'Unfolded lines': '已展开行', to: '至' }),
    EditorView.cspNonce.of(document.querySelector('meta[name="csp-nonce"]')?.content ?? ''),
    Prec.highest(keymap.of([{ key: 'Mod-Enter', run: () => { onConvert?.(); return true; } }])),
    keymap.of([...defaultKeymap, ...historyKeymap, ...foldKeymap]),
    EditorView.updateListener.of(update => {
      // 应用回调会更新编辑器状态；离开 CM 的当前更新后再调用，避免嵌套 dispatch。
      if (update.docChanged) queueMicrotask(() => onChange?.());
    }),
    EditorView.domEventHandlers({
      dblclick: () => { requestAnimationFrame(() => onDoubleClick?.()); return false; }
    })
  ];
  if (readOnly) extensions.push(
    placeholder('转换后的 A2A JSON 将显示在这里。'),
    EditorView.theme({ '&': { backgroundColor: '#f8faff', color: '#38579c' } })
  );
  const makeState = text => createJsonState(text, [
    ...extensions, accessibility.of(EditorView.contentAttributes.of(attributes()))
  ]);
  const view = new EditorView({ state: makeState(''), parent: host });
  const unfoldEffects = () => {
    const effects = [];
    foldedRanges(view.state).between(0, view.state.doc.length, (from, to) => {
      effects.push(unfoldEffect.of({ from, to }));
    });
    return effects;
  };

  return {
    get value() { return view.state.doc.toString(); },
    set value(text) {
      if (text === view.state.doc.toString()) return;
      // 替换 Demo/草稿/结果时重建状态，清理旧折叠和撤销历史；不触发 onChange。
      view.setState(makeState(text));
    },
    get selectionStart() { return view.state.selection.main.from; },
    get selectionEnd() { return view.state.selection.main.to; },
    focus() { view.focus(); },
    select() {
      view.dispatch({ selection: EditorSelection.range(0, view.state.doc.length), effects: unfoldEffects() });
    },
    setInvalid(value) {
      if (invalid === value) return;
      invalid = value;
      view.dispatch({ effects: accessibility.reconfigure(EditorView.contentAttributes.of(attributes())) });
    },
    highlightRange(from, to) {
      if (from < 0 || to <= from || to > view.state.doc.length) return;
      const effects = [];
      foldedRanges(view.state).between(from, to, (start, end) => {
        if (start < to && end > from) effects.push(unfoldEffect.of({ from: start, to: end }));
      });
      effects.push(setHighlight.of({ from, to }), EditorView.scrollIntoView(from, { y: 'center', x: 'nearest' }));
      view.dispatch({ effects });
      view.dom.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    },
    clearHighlight() { view.dispatch({ effects: setHighlight.of(null) }); },
    foldAll() { foldAllCode(view); },
    unfoldAll() { unfoldAllCode(view); },
    foldLevel(depth) {
      const effects = unfoldEffects().concat(foldRangesAtDepth(view.state, depth).map(range => foldEffect.of(range)));
      if (effects.length) view.dispatch({ effects });
    }
  };
}
