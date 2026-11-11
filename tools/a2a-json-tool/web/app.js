import { convertInput } from './convert-input.js';
import { parseInput } from './input-parser.js';
import { examples, demoExamples } from './examples.js';
import { findFieldAtSelection, resolveFieldPath } from './field-links.js';
import { createJsonEditor } from './json-editor.js';

const $ = (id) => document.getElementById(id);
const input = createJsonEditor($('json-input'), {
  label: '输入 JSON', onChange: invalidate,
  onDoubleClick: highlightField, onConvert: convert
});
const output = createJsonEditor($('json-output'), { label: '转换结果', readOnly: true, onConvert: convert });
const tabs = [...document.querySelectorAll('[data-mode]')];
const optionInputs = [...document.querySelectorAll('#message-options input')];
const drafts = Object.fromEntries(Object.entries(examples).map(([key, value]) => [key, JSON.stringify(value, null, 2)]));
let mode = 'message';
let toastTimer;
let importGeneration = 0;
let fieldIndex = null;

function clearFieldHighlight() {
  output.clearHighlight();
  $('field-link').textContent = '';
  $('field-link').hidden = true;
}

function fieldPathLabel(path) {
  return '$' + path.map(segment => typeof segment === 'number' ? `[${segment}]`
    : /^[A-Za-z_$][\w$]*$/.test(segment) ? `.${segment}` : `[${JSON.stringify(segment)}]`).join('');
}

function highlightField() {
  clearFieldHighlight();
  if (!fieldIndex || fieldIndex.text !== input.value) {
    convert();
    if (!fieldIndex) return;
  }
  const sourceField = findFieldAtSelection(fieldIndex.result.inputFields, input.selectionStart, input.selectionEnd);
  if (!sourceField) return;
  const targetPath = resolveFieldPath(sourceField.path, fieldIndex.result);
  const target = targetPath && fieldIndex.outputFields.get(JSON.stringify(targetPath));
  $('field-link').hidden = false;
  if (!target) {
    $('field-link').textContent = `${fieldPathLabel(sourceField.path)}：输出中没有对应字段，该字段已省略。`;
    return;
  }
  output.highlightRange(target.start, target.end);
  $('field-link').textContent = `${fieldPathLabel(sourceField.path)} → ${fieldPathLabel(targetPath)}`;
  $('json-output').scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

const content = {
  card: {
    title: '描述你的 Agent',
    description: '将名称、技能和接口地址整理成 A2A 1.0 AgentCard。',
    binding: 'AGENT DISCOVERY', inputFile: 'agent-config.json', outputFile: 'agent-card.json',
    help: '示例接口仅为占位，请替换为真实地址。',
    guideTitle: '从一份简单配置开始',
    guide: '至少提供 name、description、version、url 和完整 skills。url 会转换为 supportedInterfaces，协议版本固定为 1.0。'
  },
  message: {
    title: '把业务数据装入消息',
    description: '识别 Message / TextPart，或将业务 JSON 封装为 SendMessage。',
    binding: 'JSON-RPC 2.0', inputFile: 'payload.json', outputFile: 'send-message.json',
    help: '支持 JSON 片段、key=value 和 Message(...)。',
    guideTitle: '也可以粘贴对象打印文本',
    guide: 'TextPart.content 映射为 text。Message.content 在没有 parts 时映射为 data；同时提供时优先 parts，content 保留到 metadata.sourceFields.content。sessionId 映射为 contextId，其他业务字段保留到 metadata。解析修复和字段去向见转换说明。'
  }
};

function countInput() {
  $('input-count').textContent = `${input.value.length.toLocaleString()} 字符`;
  drafts[mode] = input.value;
}

function invalidate() {
  importGeneration++;
  fieldIndex = null;
  clearFieldHighlight();
  output.value = '';
  $('copy-button').disabled = true;
  $('download-button').disabled = true;
  $('output-state').textContent = '等待转换';
  $('output-state').className = 'output-state';
  $('output-count').textContent = '';
  $('feedback').hidden = true;
  $('notes').hidden = true;
  $('parsed-preview').hidden = true;
  $('normalized-input').value = '';
  input.setInvalid(false);
  countInput();
}

function notify(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 2600);
}

function showError(error) {
  const feedback = $('feedback');
  feedback.replaceChildren();
  const title = document.createElement('strong');
  title.textContent = '请检查输入';
  const list = document.createElement('ul');
  for (const issue of error.issues ?? [error.message ?? '无法完成操作，请重试。']) {
    const item = document.createElement('li');
    item.textContent = issue;
    list.append(item);
  }
  feedback.append(title, list);
  feedback.className = 'feedback';
  feedback.hidden = false;
  input.setInvalid(true);
  feedback.focus({ preventScroll: true });
  $('output-state').textContent = '输入需要调整';
}

function showNotes(notes = [], rows = notes.map(description => ({ source: '输入文本', target: '—', description }))) {
  $('notes-rows').replaceChildren(...rows.map((row) => {
    const tr = document.createElement('tr');
    for (const field of ['source', 'target', 'description']) {
      const td = document.createElement('td');
      const value = row[field];
      if (field !== 'description' && value.startsWith('$')) {
        const code = document.createElement('code');
        value.split('.').forEach((part, index) => {
          if (index) code.append('.', document.createElement('wbr'));
          code.append(part);
        });
        td.append(code);
      } else td.textContent = value;
      tr.append(td);
    }
    return tr;
  }));
  $('notes-title').textContent = `转换说明（${notes.length}）`;
  $('notes').hidden = notes.length === 0;
  $('notes').open = notes.length > 0;
}

function showParsed(result) {
  const names = { json: '标准 JSON', fragment: 'JSON 字段片段', 'object-dump': '对象打印格式' };
  $('parsed-summary').textContent = `已识别${names[result.source]} · 查看解析后的 JSON`;
  $('normalized-input').value = JSON.stringify(result.normalizedInput, null, 2);
  $('parsed-preview').hidden = result.source === 'json';
}

function convert() {
  invalidate();
  try {
    const options = Object.fromEntries(optionInputs.map((field) => [field.name, field.value]));
    const result = convertInput(input.value, mode, options);
    showParsed(result);
    output.value = JSON.stringify(result.output, null, 2);
    const outputFields = parseInput(output.value, { includeFields: true }).fields;
    fieldIndex = { text: input.value, result, outputFields: new Map(outputFields.map(field => [JSON.stringify(field.path), field])) };
    $('copy-button').disabled = false;
    $('download-button').disabled = false;
    $('output-state').textContent = '✓ 转换完成';
    $('output-state').className = 'output-state success';
    $('output-count').textContent = `${output.value.length.toLocaleString()} 字符`;
    const feedback = $('feedback');
    feedback.textContent = mode === 'card' ? '核心字段检查通过。请确认接口地址、技能和能力与实际 Agent 一致。'
      : result.mapping === 'structured-message' ? '消息请求已生成，字段去向见转换说明。未发送网络请求。'
      : 'SendMessage 请求已生成。解析后的业务数据已放入 data，未发送网络请求。';
    feedback.className = 'feedback success';
    feedback.hidden = false;
    showNotes(result.notes, result.noteRows);
  } catch (error) {
    if (error.inputResult) { showParsed(error.inputResult); showNotes(error.inputResult.notes, error.inputResult.noteRows); }
    showError(error);
  }
}

function showDemos() {
  $('demo-list').replaceChildren(...demoExamples[mode].map(demo => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'demo-button';
    button.setAttribute('aria-label', `载入${demo.title}示例`);
    button.setAttribute('aria-describedby', 'demo-help');
    const title = document.createElement('strong');
    title.textContent = demo.title;
    const description = document.createElement('span');
    description.textContent = demo.description;
    button.append(title, description);
    button.addEventListener('click', () => {
      input.value = demo.input;
      if (mode === 'message') optionInputs.forEach(field => { field.value = ''; });
      invalidate();
      notify(`已载入${demo.title}，点击“转换并检查”生成结果。`);
    });
    return button;
  }));
  $('demo-help').textContent = mode === 'message'
    ? '点击示例替换当前输入并重置消息标识，再点击“转换并检查”。'
    : '点击示例替换当前输入，再点击“转换并检查”。';
}

function selectMode(nextMode) {
  drafts[mode] = input.value;
  mode = nextMode;
  input.value = drafts[mode];
  for (const tab of tabs) {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
  }
  $('converter-panel').setAttribute('aria-labelledby', `${mode}-tab`);
  $('message-options').hidden = mode !== 'message';
  const text = content[mode];
  for (const [id, value] of Object.entries({ 'mode-title': text.title, 'mode-description': text.description, 'binding-badge': text.binding, 'input-file-label': text.inputFile, 'output-file-label': text.outputFile, 'input-help': text.help, 'guide-title': text.guideTitle, 'guide-text': text.guide })) $(id).textContent = value;
  showDemos();
  invalidate();
}

for (const tab of tabs) {
  tab.addEventListener('click', () => selectMode(tab.dataset.mode));
  tab.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[1] : tabs.find((item) => item !== tab);
    selectMode(next.dataset.mode);
    next.focus();
  });
}
for (const button of document.querySelectorAll('[data-fold]')) {
  button.addEventListener('click', () => {
    const editor = button.dataset.editor === 'input' ? input : output;
    const depth = Number(button.dataset.fold);
    if (depth) editor.foldLevel(depth);
    else editor.unfoldAll();
  });
}
optionInputs.forEach((field) => field.addEventListener('input', invalidate));
$('convert-button').addEventListener('click', convert);
$('clear-button').addEventListener('click', () => { input.value = ''; invalidate(); input.focus(); });
$('format-button').addEventListener('click', () => {
  invalidate();
  try {
    const result = parseInput(input.value);
    input.value = JSON.stringify(result.value, null, 2);
    countInput();
    showNotes(result.notes);
    notify(result.source === 'json' ? 'JSON 已格式化' : '输入已解析并格式化为 JSON，修改见转换说明。');
  }
  catch (error) { showError(error); }
});
$('import-button').addEventListener('click', () => $('file-input').click());
$('file-input').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const generation = ++importGeneration;
  const targetMode = mode;
  try {
    const text = await file.text();
    if (generation !== importGeneration || targetMode !== mode) return;
    input.value = text;
    invalidate();
    notify(`已导入 ${file.name}`);
  } catch { showError(new Error('无法读取该文件，请重新选择。')); }
  finally { event.target.value = ''; }
});
$('copy-button').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(output.value); notify('转换结果已复制'); }
  catch { output.focus(); output.select(); notify('浏览器未允许剪贴板访问，已选中结果，请按 ⌘ / Ctrl + C。'); }
});
$('download-button').addEventListener('click', () => {
  const blob = new Blob([`${output.value}\n`], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = content[mode].outputFile;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  notify('已生成 JSON 下载文件');
});
document.addEventListener('keydown', (event) => {
  if (!event.defaultPrevented && (event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); convert(); }
});

input.value = drafts[mode];
selectMode(mode);
convert();
